use super::{DynResult, capture, host_ram, request_bytes, restart, server::Server, trace};
use crate::cache_parity::contract::{CacheState, Engine};
use crate::{artifact, command::write_json_file};
use process_supervision::{Cancellation, interrupt::Interrupt};
use serde::Deserialize;
use serde_json::json;
use std::{
    fs,
    path::{Path, PathBuf},
};
use trace::durable::Restore;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Configuration {
    pairs: usize,
    host_profile: Option<PathBuf>,
    seed_request: Option<PathBuf>,
    l2_eviction_request: Option<PathBuf>,
    #[serde(default)]
    restart_from_disk: bool,
    skippy: Server,
    llama_server: Server,
}

impl Engine {
    fn name(self) -> &'static str {
        match self {
            Self::Candidate => "skippy",
            Self::Baseline => "llama_server",
        }
    }
}

pub(in crate::cache_parity) fn run(config: &Path, request: &Path, output: &Path) -> DynResult<()> {
    let (config, config_bytes) = load_config(config)?;
    let request = request_bytes(request)?;
    let seed = config
        .seed_request
        .as_deref()
        .map(request_bytes)
        .transpose()?;
    let eviction = config
        .l2_eviction_request
        .as_deref()
        .map(request_bytes)
        .transpose()?;
    if config.restart_from_disk && eviction.is_some() {
        return Err(
            "restart_from_disk and l2_eviction_request select different cache states".into(),
        );
    }
    if config.restart_from_disk || eviction.is_some() {
        if seed.is_none()
            || serde_json::from_slice::<serde_json::Value>(&request)?["cache_prompt"] != true
        {
            return Err(
                "L2 and post-restart replay need a seed request and cache_prompt enabled for replay".into(),
            );
        }
        restart::validate_args(&config.skippy, &config.llama_server)?;
    }
    if let Some(eviction) = &eviction {
        host_ram::validate(&config.skippy, &request, eviction, seed.as_deref())?;
    }
    let hashes = json!({ "skippy": artifact::sha256(&config.skippy.binary)?, "llama_server": artifact::sha256(&config.llama_server.binary)? });
    fs::create_dir(output)?;
    fs::write(output.join("configuration.json"), config_bytes)?;
    let interruption = Interrupt::install()?;
    let result = capture_pairs(
        &config,
        &request,
        seed.as_deref(),
        eviction.as_deref(),
        output,
        hashes,
        &interruption.cancellation(),
    );
    interruption.finish()?;
    result
}

fn capture_pairs(
    config: &Configuration,
    request: &[u8],
    seed: Option<&[u8]>,
    eviction: Option<&[u8]>,
    output: &Path,
    hashes: serde_json::Value,
    cancellation: &Cancellation,
) -> DynResult<()> {
    let summary = json!({
        "schema_version": 1,
        "scope": "diagnostic_stream_capture",
        "requested_pairs": config.pairs,
        "binary_sha256": hashes,
        "request_sha256": artifact::digest(request)?,
        "seed_sha256": seed.map(artifact::digest).transpose()?,
        "l2_eviction_sha256": eviction.map(artifact::digest).transpose()?,
        "restart_from_disk": config.restart_from_disk,
        "memory_sampling": config.skippy.memory_sampling,
        "legs": [],
    });
    run_pairs(
        config,
        output,
        cancellation,
        summary,
        |engine, server, leg_output| {
            if config.restart_from_disk {
                restart::capture(
                    engine,
                    server,
                    seed.ok_or("missing restart seed")?,
                    request,
                    leg_output,
                    cancellation,
                )
            } else if let Some(eviction) = eviction {
                host_ram::capture(
                    engine,
                    server,
                    seed.ok_or("missing L2 seed")?,
                    eviction,
                    request,
                    leg_output,
                    cancellation,
                )
            } else {
                capture_leg(engine, server, request, seed, leg_output, cancellation)
            }
        },
    )
}

pub(in crate::cache_parity) fn run_trace(
    config: &Path,
    workload: &Path,
    output: &Path,
) -> DynResult<()> {
    let (config, config_bytes) = load_config(config)?;
    let workload = trace::Workload::load(workload)?;
    let seed = config
        .seed_request
        .as_deref()
        .map(request_bytes)
        .transpose()?;
    let eviction = config
        .l2_eviction_request
        .as_deref()
        .map(request_bytes)
        .transpose()?;
    let burst_state = validate_trace_bursts(&workload, &config)?;
    let restore = match (
        config.restart_from_disk,
        seed.as_deref(),
        eviction.as_deref(),
    ) {
        (true, _, Some(_)) => return Err("select either L2 or post-restart L3 for a trace".into()),
        (true, Some(seed), None) => Some(Restore::L3 { seed }),
        (false, Some(seed), Some(eviction)) => Some(Restore::L2 { seed, eviction }),
        (false, None, Some(_)) => {
            return Err("an L2 or L3 trace requires a seed request".into());
        }
        (_, _, None) => None,
    };
    if let Some(restore) = restore {
        restore.validate(&workload, &config.skippy, &config.llama_server)?;
    }
    let state = match (restore, seed.is_some(), burst_state) {
        (_, _, Some(state)) => state,
        (Some(restore), _, None) => restore.state(),
        (None, true, None) => CacheState::WarmL1,
        (None, false, None) => CacheState::ColdPrefill,
    };
    if workload.design.is_some_and(|design| design.cache != state) {
        return Err("workload cache state does not match the capture configuration".into());
    }
    let identity = config
        .host_profile
        .as_deref()
        .map(|host| trace::identity::Inputs::observe(host, &config.skippy, &config.llama_server))
        .transpose()?;
    let summary = json!({
        "schema_version": 1,
        "scope": "diagnostic_workload_capture",
        "requested_pairs": config.pairs,
        "binary_sha256": {
            "skippy": artifact::sha256(&config.skippy.binary)?,
            "llama_server": artifact::sha256(&config.llama_server.binary)?,
        },
        "workload_sha256": artifact::digest(workload.manifest.as_slice())?,
        "configuration_sha256": artifact::digest(config_bytes.as_slice())?,
        "seed_sha256": seed.as_deref().map(artifact::digest).transpose()?,
        "l2_eviction_sha256": eviction.as_deref().map(artifact::digest).transpose()?,
        "cache_state": state,
        "prepared_bursts": burst_state.is_some(),
        "memory_sampling": config.skippy.memory_sampling,
        "legs": [],
    });
    fs::create_dir(output)?;
    fs::write(output.join("configuration.json"), config_bytes)?;
    workload.retain_inputs(output)?;
    if let Some(identity) = &identity {
        identity.retain(output)?;
    }
    if let Some(seed) = &seed {
        fs::write(output.join("seed.json"), seed)?;
    }
    if let Some(eviction) = &eviction {
        fs::write(output.join("l2-eviction.json"), eviction)?;
    }
    let interruption = Interrupt::install()?;
    let cancellation = interruption.cancellation();
    let result = run_pairs(
        &config,
        output,
        &cancellation,
        summary,
        |engine, server, leg| {
            match burst_state {
                Some(CacheState::WarmL1) => {
                    return trace::resident::capture(engine, server, &workload, leg, &cancellation);
                }
                Some(CacheState::PostRestartL3) => {
                    return trace::restarted::capture(
                        engine,
                        server,
                        &workload,
                        leg,
                        &cancellation,
                    );
                }
                Some(CacheState::L2Restore) => {
                    return trace::ram::capture(engine, server, &workload, leg, &cancellation);
                }
                _ => {}
            }
            if let Some(restore) = restore {
                return restore.capture(engine, server, &workload, leg, &cancellation);
            }
            server
                .with_ready(engine, &[], leg, &cancellation, |address, memory, stop| {
                    if let Some(seed) = &seed {
                        capture(address, seed, &leg.join("seed"), Some(engine))?;
                    }
                    trace::capture(address, engine, &workload.bursts, memory, stop, leg, &[])
                })
                .map(|_| ())
        },
    );
    interruption.finish()?;
    if let Some(identity) = &identity {
        identity.complete(output)?;
    }
    result
}

fn validate_trace_bursts(
    workload: &trace::Workload,
    config: &Configuration,
) -> DynResult<Option<CacheState>> {
    let pinned_eviction = workload
        .bursts
        .iter()
        .flatten()
        .any(|request| request.eviction.is_some());
    let global_preparation = config.seed_request.is_some() || config.l2_eviction_request.is_some();
    if pinned_eviction {
        if global_preparation || config.restart_from_disk {
            return Err("per-request L2 evictions require per-request seeds and a continuing server process".into());
        }
        restart::validate_args(&config.skippy, &config.llama_server)?;
        trace::ram::validate(workload, &config.skippy)?;
        return Ok(Some(CacheState::L2Restore));
    }
    if config.restart_from_disk && !global_preparation {
        restart::validate_args(&config.skippy, &config.llama_server)?;
        trace::restarted::validate(workload)?;
        return Ok(Some(CacheState::PostRestartL3));
    }
    if workload
        .bursts
        .iter()
        .flatten()
        .any(|request| request.seed.is_some())
    {
        if global_preparation {
            return Err(
                "per-request seeds cannot be combined with global preparation requests".into(),
            );
        }
        restart::validate_args(&config.skippy, &config.llama_server)?;
        trace::resident::validate(workload)?;
        return Ok(Some(CacheState::WarmL1));
    }
    Ok(None)
}

fn load_config(path: &Path) -> DynResult<(Configuration, Vec<u8>)> {
    let bytes = fs::read(path)?;
    let config: Configuration = serde_json::from_slice(&bytes)?;
    if !(1..=20).contains(&config.pairs)
        || !config.skippy.binary.is_absolute()
        || !config.llama_server.binary.is_absolute()
    {
        return Err("capture requires 1–20 pairs and two absolute binary paths".into());
    }
    if config.skippy.memory_sampling != config.llama_server.memory_sampling {
        return Err("both engines must use the same memory sampling profile".into());
    }
    if config.skippy.probe_sha256()? != config.llama_server.probe_sha256()? {
        return Err("both engines must use identical Metal probe bytes".into());
    }
    Ok((config, bytes))
}

fn run_pairs(
    config: &Configuration,
    output: &Path,
    cancellation: &Cancellation,
    mut summary: serde_json::Value,
    mut measure: impl FnMut(Engine, &Server, &Path) -> DynResult<()>,
) -> DynResult<()> {
    let mut failed = false;
    for pair in 1..=config.pairs {
        let mut legs = [
            (Engine::Candidate, &config.skippy),
            (Engine::Baseline, &config.llama_server),
        ];
        if pair % 2 == 0 {
            legs.reverse();
        }
        for (engine, server) in legs {
            if cancellation.is_cancelled() {
                return Err("cache parity capture was interrupted".into());
            }
            let directory = format!("pair-{pair:02}-{}", engine.name());
            let leg_output = output.join(&directory);
            let result = measure(engine, server, &leg_output);
            let error = result.err().map(|error| error.to_string());
            failed |= error.is_some();
            summary["legs"]
                .as_array_mut()
                .ok_or("invalid capture summary")?
                .push(json!({
                    "pair": pair, "engine": engine, "directory": directory, "error": error,
                }));
            write_json_file(&output.join("pairs.json"), &summary)?;
        }
    }
    if failed {
        return Err("one or more cache parity captures failed; see pairs.json".into());
    }
    Ok(())
}

fn capture_leg(
    engine: Engine,
    server: &Server,
    request: &[u8],
    seed: Option<&[u8]>,
    output: &Path,
    cancellation: &Cancellation,
) -> DynResult<()> {
    server
        .with_ready(engine, &[], output, cancellation, |address, memory, _| {
            if let Some(seed) = seed {
                capture(address, seed, &output.join("seed"), Some(engine))?;
            }
            let measured = output.join("capture");
            memory.record(&measured.join("memory.json"), || {
                capture(address, request, &measured, Some(engine))
            })
        })
        .map(|_| ())
}
