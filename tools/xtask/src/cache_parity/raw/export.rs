use super::{DynResult, MemorySample, RAW_VERSION, RawTrial, Request};
use crate::{
    artifact,
    cache_parity::{
        capture::{TraceIdentity, validate_managed_args},
        contract::*,
        identity::Checkout,
    },
    cache_qualification::{MANIFEST, read_manifest},
};
use serde::Deserialize;
use std::{
    fs,
    io::{ErrorKind, Write},
    path::{Path, PathBuf},
};

mod burst;
mod ram;
mod request;
mod restart;
#[cfg(test)]
mod tests;

#[derive(Deserialize)]
struct Capture {
    schema_version: u32,
    scope: String,
    configuration_sha256: String,
    workload_sha256: String,
    cache_state: CacheState,
    requested_pairs: usize,
    binary_sha256: Binaries,
    legs: Vec<Leg>,
}

#[derive(Deserialize)]
struct Binaries {
    skippy: String,
    llama_server: String,
}

#[derive(Deserialize)]
struct Leg {
    pair: usize,
    engine: Engine,
    directory: PathBuf,
    error: Option<String>,
}

#[derive(Deserialize)]
struct Trace {
    schema_version: u32,
    engine: Engine,
    duration_ns: u64,
    requests: Vec<request::Receipt>,
    error: Option<String>,
}

#[derive(Deserialize)]
struct WorkloadManifest {
    schema_version: u32,
    design: Option<WorkloadDesign>,
    bursts: Vec<Vec<request::Input>>,
}

impl WorkloadManifest {
    fn uses_bursts(&self, state: CacheState) -> bool {
        matches!(state, CacheState::L2Restore | CacheState::PostRestartL3)
            || state == CacheState::WarmL1
                && self
                    .bursts
                    .iter()
                    .flatten()
                    .any(|request| request.seed.is_some())
    }
}

#[derive(Deserialize)]
struct Memory {
    schema_version: u32,
    profile: String,
    duration_ns: u64,
    pid: Option<u32>,
    probe_sha256: Option<String>,
    samples: Vec<MemorySample>,
}

#[derive(Deserialize)]
struct ProbePin {
    metal_memory_probe_sha256: Option<String>,
}

pub(in crate::cache_parity) fn run(
    plan: &Path,
    cell: &Path,
    capture: &Path,
    output: &Path,
) -> DynResult<()> {
    let plan_bytes = fs::read(plan)?;
    let plan: Plan = serde_json::from_slice(&plan_bytes)?;
    let manifest = fs::read_to_string(MANIFEST)?;
    plan.validate(&read_manifest(&manifest)?, &Checkout::load(&manifest)?)?;
    let cell: Cell = load_json(cell)?;
    let row = plan
        .cells
        .iter()
        .find(|row| row.cell == cell)
        .ok_or("selected cell is absent from the parity plan")?;
    let plan_sha256 = artifact::digest(plan_bytes.as_slice())?;
    let host = plan.backends.get(cell.backend);
    let measured = assemble(capture, row, host, &plan_sha256, output)?;
    println!("{}", serde_json::to_string_pretty(&measured)?);
    Ok(())
}

fn assemble(
    directory: &Path,
    row: &PlannedCell,
    host: &Host,
    plan_sha256: &str,
    output: &Path,
) -> DynResult<MeasuredCell> {
    let capture: Capture = load_json(&directory.join("pairs.json"))?;
    let workload: WorkloadManifest = load_json(&directory.join("workload.json"))?;
    if workload.design.is_some_and(|design| {
        design.cache != row.cell.cache || design.workload != row.cell.workload
    }) {
        return Err("workload design does not match the frozen parity cell".into());
    }
    check_identity(
        directory,
        &capture,
        row,
        host,
        workload.uses_bursts(row.cell.cache),
    )?;
    let probe: ProbePin = load_json(&directory.join("host-profile.json"))?;
    if workload.schema_version != 1
        || workload.bursts.iter().map(Vec::len).sum::<usize>() != REQUESTS as usize
    {
        return Err("raw trial export requires all 200 planned workload requests".into());
    }
    let mut artifacts = Vec::new();
    let mut pairs = Vec::new();
    for (index, [a, b]) in capture.legs.as_chunks::<2>().0.iter().enumerate() {
        let first = index + 1;
        let expected = if first % 2 == 1 {
            [Engine::Candidate, Engine::Baseline]
        } else {
            [Engine::Baseline, Engine::Candidate]
        };
        let build = |leg: &Leg, engine| -> DynResult<(Vec<u8>, Trial)> {
            if leg.pair != first || leg.engine != engine {
                return Err("capture pairs do not match the planned alternating order".into());
            }
            let raw = build_trial(directory, leg, &workload, row, host, plan_sha256, &probe)?;
            let bytes = serde_json::to_vec(&raw)?;
            let digest = artifact::digest(bytes.as_slice())?;
            let summary = raw.summary(digest)?;
            Ok((bytes, summary))
        };
        let (a_bytes, a_trial) = build(a, expected[0])?;
        let (b_bytes, b_trial) = build(b, expected[1])?;
        artifacts.push((a_trial.raw_artifact_sha256.clone(), a_bytes));
        artifacts.push((b_trial.raw_artifact_sha256.clone(), b_bytes));
        pairs.push([a_trial, b_trial]);
    }
    fs::create_dir_all(output)?;
    for (digest, bytes) in artifacts {
        store(&output.join(format!("{digest}.json")), &bytes)?;
    }
    Ok(MeasuredCell {
        cell: row.cell.clone(),
        model_sha256: row.model_sha256.clone(),
        template_sha256: row.template_sha256.clone(),
        sidecars_sha256: row.sidecars_sha256.clone(),
        workload_sha256: row.workload_sha256.clone(),
        pairs,
    })
}

fn check_identity(
    directory: &Path,
    capture: &Capture,
    row: &PlannedCell,
    host: &Host,
    prepared_bursts: bool,
) -> DynResult<()> {
    if capture.schema_version != 1
        || capture.scope != "diagnostic_workload_capture"
        || capture.cache_state != row.cell.cache
        || !(1..=PAIRS).contains(&capture.requested_pairs)
        || capture.legs.len() != 2 * capture.requested_pairs
    {
        return Err("export requires complete paired traces for the selected cache state".into());
    }
    let identity: TraceIdentity = load_json(&directory.join("capture-identity.json"))?;
    if identity.schema_version != 1
        || identity.pairs_sha256 != artifact::sha256(&directory.join("pairs.json"))?
        || identity.model_sha256 != row.model_sha256
        || identity.host_profile_sha256 != host.host_profile_sha256
        || identity.host_profile_sha256 != artifact::sha256(&directory.join("host-profile.json"))?
        || capture.workload_sha256 != row.workload_sha256
        || capture.workload_sha256 != artifact::sha256(&directory.join("workload.json"))?
        || capture.binary_sha256.skippy != host.candidate_binary_sha256
        || capture.binary_sha256.llama_server != host.baseline_binary_sha256
    {
        return Err(
            "capture model, host, binaries or workload does not match the frozen plan".into(),
        );
    }
    let config = directory.join("configuration.json");
    if capture.configuration_sha256 != artifact::sha256(&config)?
        || capture.configuration_sha256 != row.candidate_config_sha256
        || capture.configuration_sha256 != row.baseline_config_sha256
    {
        return Err("capture configuration digest does not match the frozen plan".into());
    }
    if row.cell.cache == CacheState::WarmL1 && !prepared_bursts {
        request::verify_baseline_resident(&config)?;
    }
    if prepared_bursts {
        let config: serde_json::Value = load_json(&config)?;
        let restart = config["restart_from_disk"].as_bool().unwrap_or(false);
        if restart != (row.cell.cache == CacheState::PostRestartL3)
            || !config["seed_request"].is_null()
            || !config["l2_eviction_request"].is_null()
        {
            return Err("restore burst export requires pinned per-request preparation for the selected cache state".into());
        }
        let args =
            |engine: &str| serde_json::from_value::<Vec<String>>(config[engine]["args"].clone());
        validate_managed_args(&args("skippy")?, &args("llama_server")?)?;
    }
    Ok(())
}

fn build_trial(
    directory: &Path,
    leg: &Leg,
    workload: &WorkloadManifest,
    row: &PlannedCell,
    host: &Host,
    plan_sha256: &str,
    probe: &ProbePin,
) -> DynResult<RawTrial> {
    let pair = leg.pair;
    let engine = leg.engine;
    let name = match engine {
        Engine::Candidate => "skippy",
        Engine::Baseline => "llama_server",
    };
    let expected_directory = PathBuf::from(format!("pair-{pair:02}-{name}"));
    if leg.directory != expected_directory || leg.error.is_some() {
        return Err("capture directory does not match its engine and pair".into());
    }
    let leg_root = directory.join(&leg.directory);
    let (requests, duration_ns, memory) = if workload.uses_bursts(row.cell.cache) {
        burst::measure(
            directory,
            &leg_root,
            &workload.bursts,
            engine,
            row.cell.backend,
            probe,
            row.cell.cache,
        )?
    } else {
        let trace = open_trace(&leg_root, engine)?;
        let requests = request::collect(
            &workload.bursts,
            &trace,
            directory,
            &leg_root,
            engine,
            request::Source::Stream(row.cell.cache),
        )?;
        let memory = open_memory(&leg_root, row.cell.backend, trace.duration_ns, probe)?;
        (requests, memory.duration_ns, memory.samples)
    };
    let (binary_sha256, config_sha256) = match engine {
        Engine::Candidate => (&host.candidate_binary_sha256, &row.candidate_config_sha256),
        Engine::Baseline => (&host.baseline_binary_sha256, &row.baseline_config_sha256),
    };
    Ok(RawTrial {
        schema_version: RAW_VERSION,
        plan_sha256: plan_sha256.into(),
        workload_sha256: row.workload_sha256.clone(),
        cell: row.cell.clone(),
        pair,
        engine,
        binary_sha256: binary_sha256.clone(),
        config_sha256: config_sha256.clone(),
        host_profile_sha256: host.host_profile_sha256.clone(),
        duration_ns,
        requests,
        memory,
    })
}

fn open_trace(directory: &Path, engine: Engine) -> DynResult<Trace> {
    let trace: Trace = load_json(&directory.join("trace.json"))?;
    if trace.schema_version != 1
        || trace.engine != engine
        || trace.error.is_some()
        || trace.duration_ns == 0
    {
        return Err("capture trace is incomplete or malformed".into());
    }
    Ok(trace)
}

fn open_memory(
    directory: &Path,
    backend: Backend,
    duration_ns: u64,
    probe: &ProbePin,
) -> DynResult<Memory> {
    let memory: Memory = load_json(&directory.join("memory.json"))
        .map_err(|error| format!("missing or invalid GPU memory evidence: {error}"))?;
    validate_memory(&memory, backend, duration_ns, probe)?;
    if memory
        .samples
        .first()
        .is_none_or(|sample| sample.offset_ns != 0)
        || memory
            .samples
            .last()
            .is_none_or(|sample| sample.offset_ns != memory.duration_ns)
        || memory
            .samples
            .windows(2)
            .any(|pair| pair[0].offset_ns >= pair[1].offset_ns)
    {
        return Err("memory samples do not span an ordered measurement interval".into());
    }
    Ok(memory)
}

fn validate_memory(
    memory: &Memory,
    backend: Backend,
    duration_ns: u64,
    probe: &ProbePin,
) -> DynResult<()> {
    if memory.schema_version != 1 || memory.duration_ns < duration_ns {
        return Err("raw trace export requires GPU memory over the complete request window".into());
    }
    match (
        backend,
        memory.profile.as_str(),
        memory.probe_sha256.as_deref(),
    ) {
        (Backend::Cuda, "cuda", None) if probe.metal_memory_probe_sha256.is_none() => Ok(()),
        (Backend::Metal, "metal", Some(sha256))
            if is_digest(sha256, 64)
                && probe.metal_memory_probe_sha256.as_deref() == Some(sha256) =>
        {
            Ok(())
        }
        _ => Err(
            "raw trace export requires GPU memory from the sampling source pinned for this backend"
                .into(),
        ),
    }
}

fn load_json<T: serde::de::DeserializeOwned>(path: &Path) -> DynResult<T> {
    Ok(serde_json::from_reader(std::io::BufReader::new(
        fs::File::open(path)?,
    ))?)
}

fn store(path: &Path, bytes: &[u8]) -> DynResult<()> {
    let mut temporary =
        tempfile::NamedTempFile::new_in(path.parent().ok_or("missing raw trial parent")?)?;
    temporary.write_all(bytes)?;
    match temporary.persist_noclobber(path) {
        Ok(_) => Ok(()),
        Err(error)
            if error.error.kind() == ErrorKind::AlreadyExists && fs::read(path)? == bytes =>
        {
            Ok(())
        }
        Err(error) => Err(error.into()),
    }
}
