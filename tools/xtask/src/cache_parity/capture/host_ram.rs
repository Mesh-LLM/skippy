use super::{
    DynResult, Engine, capture as capture_request, memory, restart::observation, server::Server,
};
use crate::{command::write_json_file, execution};
use process_supervision::Cancellation;
use serde_json::{Value, json};
use std::{
    fs,
    net::SocketAddr,
    path::Path,
    time::{Duration, Instant},
};

const BASELINE_RESTORE_MARKER: &str = "__TEST_TAG_PROMPT_CACHE_RESTORE__";
pub(super) mod native;

pub(super) fn validate(
    server: &Server,
    replay: &[u8],
    eviction: &[u8],
    seed: Option<&[u8]>,
) -> DynResult<()> {
    if eviction == replay
        || seed == Some(eviction)
        || serde_json::from_slice::<Value>(eviction)?["cache_prompt"] != true
    {
        return Err("L2 needs a distinct eviction request with cache_prompt enabled".into());
    }
    if server
        .args
        .iter()
        .any(|arg| arg.split('=').next() == Some("--prefix-cache-max-entries"))
    {
        return Err(
            "L2 capture sets --prefix-cache-max-entries; omit it from Skippy's arguments".into(),
        );
    }
    Ok(())
}

pub(super) fn capture(
    engine: Engine,
    server: &Server,
    seed: &[u8],
    eviction: &[u8],
    replay: &[u8],
    output: &Path,
    cancellation: &Cancellation,
) -> DynResult<()> {
    let parent = output
        .parent()
        .ok_or("missing parent directory for L2 capture")?
        .canonicalize()?;
    let state = parent
        .join(
            output
                .file_name()
                .ok_or("missing L2 capture directory name")?,
        )
        .join("state");
    let args = match engine {
        Engine::Candidate => execution::strings(&[
            "--prefix-cache",
            "on",
            "--prefix-cache-max-entries",
            "1",
            "--prefix-cache-ram",
            "512MiB",
            "--kv-cache-disk",
            "auto",
            "--kv-cache-disk-dir",
            state.to_str().ok_or("non-UTF-8 cache path")?,
        ]),
        Engine::Baseline => {
            execution::strings(&["--cache-ram", "512", "--parallel", "1", "--verbosity", "5"])
        }
    };
    server.with_ready(
        engine,
        &args,
        output,
        cancellation,
        |address, memory, stop| {
            let seed_output = output.join("seed");
            capture_request(address, seed, &seed_output, Some(engine))?;
            if observation(&seed_output)?["cached_tokens"] != 0 {
                return Err("fresh L2 publisher reported reused prompt tokens".into());
            }
            match engine {
                Engine::Candidate => {
                    capture_skippy(address, memory, eviction, replay, output, stop)
                }
                Engine::Baseline => {
                    capture_request(address, eviction, &output.join("eviction"), Some(engine))?;
                    let measured = output.join("capture");
                    memory.record(&measured.join("memory.json"), || {
                        capture_request(address, replay, &measured, Some(engine))
                    })
                }
            }
        },
    )?;
    let measured = observation(&output.join("capture"))?;
    let cached_tokens = measured["cached_tokens"]
        .as_u64()
        .filter(|n| *n > 0)
        .ok_or("L2 replay did not reuse prompt tokens")?;
    let (source, native_update_ms) = match engine {
        Engine::Candidate => ("l2", None),
        Engine::Baseline => {
            let eviction = observation(&output.join("eviction"))?;
            if eviction["cached_tokens"]
                .as_u64()
                .is_none_or(|n| n >= cached_tokens)
            {
                return Err(
                    "llama-server did not prove host prompt-cache reuse on the measured replay"
                        .into(),
                );
            }
            ("prompt_cache_ram", Some(native_cache_update_ms(output)?))
        }
    };
    let disk_payload_bytes = (engine == Engine::Candidate).then_some(0_u64);
    let sampled_ttft_ms = measured["sampled_phases"]["ttft_ms"]
        .as_f64()
        .map(|ms| ms + native_update_ms.unwrap_or(0.0));
    write_json_file(
        &output.join("host-restore.json"),
        &json!({"cache_source":source,"cached_tokens":cached_tokens,"prompt_tokens":measured["prompt_tokens"],"disk_payload_bytes":disk_payload_bytes,
            "sampled_ttft_ms":sampled_ttft_ms,"end_to_end_ttft_ns":measured["first_token_ns"],"native_cache_update_ms":native_update_ms}),
    )
}

fn native_cache_update_ms(output: &Path) -> DynResult<f64> {
    let mut measured = None;
    for name in ["server.stdout.log", "server.stderr.log"] {
        let log = fs::read_to_string(output.join(name))?;
        if let Some(ms) = parse_native_cache_update(&log)?
            && measured.replace(ms).is_some()
        {
            return Err("llama-server reported more than one host prompt-cache restore".into());
        }
    }
    measured.ok_or_else(|| "llama-server did not log a successful host prompt-cache restore".into())
}

fn parse_native_cache_update(log: &str) -> DynResult<Option<f64>> {
    let Some((_, after)) = log.split_once(BASELINE_RESTORE_MARKER) else {
        return Ok(None);
    };
    if after.contains(BASELINE_RESTORE_MARKER) {
        return Err("llama-server reported more than one host prompt-cache restore".into());
    }
    let elapsed = after.lines().find_map(|line| {
        line.split_once("prompt cache update took ")
            .map(|(_, duration)| duration)
    });
    let milliseconds: f64 = elapsed
        .and_then(|duration| duration.trim().strip_suffix(" ms"))
        .ok_or("missing llama-server host prompt-cache timing")?
        .parse()?;
    if !milliseconds.is_finite() || milliseconds < 0.0 {
        return Err("invalid llama-server host prompt-cache timing".into());
    }
    Ok(Some(milliseconds))
}

#[cfg(test)]
#[test]
fn host_timing_comes_after_the_single_successful_restore() {
    let previous = "srv  get_availabl: prompt cache update took 15.54 ms\n";
    let selected = "srv          load: __TEST_TAG_PROMPT_CACHE_RESTORE__\nsrv  get_availabl: prompt cache update took 21.85 ms\n";
    assert_eq!(parse_native_cache_update(previous).unwrap(), None);
    assert_eq!(
        parse_native_cache_update(&format!("{previous}{selected}")).unwrap(),
        Some(21.85)
    );
    assert!(parse_native_cache_update(&format!("{selected}{selected}")).is_err());
    assert!(parse_native_cache_update(BASELINE_RESTORE_MARKER).is_err());
}

fn capture_skippy(
    address: SocketAddr,
    memory: &memory::Monitor<'_>,
    eviction: &[u8],
    replay: &[u8],
    output: &Path,
    stop: &Cancellation,
) -> DynResult<()> {
    let state = output.join("state");
    await_published_prefixes(&state, 1, stop)?;
    for index in 1..=5 {
        let displacement = output.join(format!("eviction-{index:02}"));
        capture_request(address, eviction, &displacement, Some(Engine::Candidate))?;
        if index == 1 {
            await_published_prefixes(&state, 2, stop)?;
        }
        let attempt = output.join(format!("conditioning-replay-{index:02}"));
        capture_request(address, replay, &attempt, Some(Engine::Candidate))?;
        let measured = observation(&attempt)?;
        if measured["final_server_timings"]["cache_source"] == "l2" {
            return measure_skippy_l2(address, memory, eviction, replay, output);
        }
        if stop.is_cancelled() {
            break;
        }
    }
    Err(
        "Skippy did not observe an L2 restore after preparing and displacing the durable prefix"
            .into(),
    )
}

fn measure_skippy_l2(
    address: SocketAddr,
    memory: &memory::Monitor<'_>,
    eviction: &[u8],
    replay: &[u8],
    output: &Path,
) -> DynResult<()> {
    let displacement = output.join("measured-eviction");
    capture_request(address, eviction, &displacement, Some(Engine::Candidate))?;
    let attempt = output.join("capture");
    memory.record(&attempt.join("memory.json"), || {
        capture_request(address, replay, &attempt, Some(Engine::Candidate))
    })?;
    let replay = observation(&attempt)?;
    let before = disk_payload_counter(&observation(&displacement)?)?;
    let after = disk_payload_counter(&replay)?;
    write_json_file(
        &attempt.join("disk-payload-counters.json"),
        &json!({"before":before,"after":after}),
    )?;
    if replay["final_server_timings"]["cache_source"] != "l2" || before != after {
        return Err(format!(
            "fixed measured replay expected L2 with an unchanged disk-payload read counter, observed {} and {before} to {after}",
            replay["final_server_timings"]["cache_source"],
        ).into());
    }
    Ok(())
}

pub(super) fn disk_payload_counter(observation: &Value) -> DynResult<u64> {
    observation["final_server_timings"]["cache_disk_payload_bytes_read_total"]
        .as_u64()
        .ok_or_else(|| "missing Skippy disk-payload read counter".into())
}

pub(super) fn await_published_prefixes(
    state: &Path,
    minimum: usize,
    stop: &Cancellation,
) -> DynResult<()> {
    let prefix_dir = state.join("prefixes");
    let deadline = Instant::now() + Duration::from_secs(90);
    while !stop.is_cancelled() && Instant::now() < deadline {
        let mut published = 0;
        for scope in fs::read_dir(&prefix_dir)? {
            let scope = scope?;
            if scope.file_type()?.is_dir() {
                for entry in fs::read_dir(scope.path())? {
                    if entry?
                        .path()
                        .extension()
                        .is_some_and(|extension| extension == "key")
                    {
                        published += 1;
                    }
                }
            }
        }
        if published >= minimum {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    Err(format!("Skippy did not publish {minimum} durable prefix keys before the L2 replay").into())
}
