use super::{
    Backend, CacheState, DynResult, Engine, Memory, MemorySample, ProbePin, Request, load_json,
    open_memory, open_trace, ram, request, restart,
};
use serde::Deserialize;
use std::path::Path;

#[cfg(test)]
mod tests;

#[derive(Deserialize)]
struct Schedule {
    schema_version: u32,
    scope: String,
    engine: Engine,
    bursts: Vec<Burst>,
}

#[derive(Deserialize)]
struct Burst {
    ordinal: usize,
    directory: String,
    requests: usize,
    error: Option<String>,
}

pub(super) fn measure(
    root: &Path,
    leg: &Path,
    bursts: &[Vec<request::Input>],
    engine: Engine,
    backend: Backend,
    probe: &ProbePin,
    state: CacheState,
) -> DynResult<(Vec<Request>, u64, Vec<MemorySample>)> {
    let scope = match state {
        CacheState::WarmL1 => "resident_bursts",
        CacheState::L2Restore => "l2_restore_bursts",
        CacheState::PostRestartL3 => "post_restart_bursts",
        _ => return Err("expected a restore burst schedule".into()),
    };
    let trace: Schedule = load_json(&leg.join("trace.json"))?;
    if trace.schema_version != 1
        || trace.scope != scope
        || trace.engine != engine
        || trace.bursts.len() != bursts.len()
    {
        return Err(format!("missing or malformed {scope} schedule").into());
    }
    let slots = bursts
        .iter()
        .map(Vec::len)
        .max()
        .ok_or("restore burst schedule has no requests")?;
    let mut requests = Vec::new();
    let mut duration = 0_u64;
    let mut samples = Vec::new();
    let mut resident_pid = None;
    for (index, (receipt, burst)) in trace.bursts.into_iter().zip(bursts).enumerate() {
        let ordinal = index + 1;
        if receipt.ordinal != ordinal
            || receipt.directory != format!("burst-{ordinal:03}")
            || receipt.requests != burst.len()
            || receipt.error.is_some()
        {
            return Err(format!(
                "restore burst {ordinal} is incomplete or does not match the frozen workload"
            )
            .into());
        }
        let directory = leg.join(receipt.directory);
        let (pid, source) = match state {
            CacheState::WarmL1 => {
                let pid = resident_evidence(root, &directory, engine, burst, slots)?;
                if resident_pid
                    .replace(pid)
                    .is_some_and(|previous| previous != pid)
                {
                    return Err(
                        "resident bursts did not share one continuing server process".into(),
                    );
                }
                (pid, request::Source::Resident)
            }
            CacheState::L2Restore => ram::evidence(root, &directory, engine, burst, slots)?,
            CacheState::PostRestartL3 => restart::evidence(root, &directory, engine, burst, slots)?,
            CacheState::ColdPrefill => return Err("cold traces have no warm preparation".into()),
        };
        let replay = directory.join("replay");
        let trace = open_trace(&replay, engine)?;
        if !overlaps(&trace.requests) {
            return Err("restore request windows did not overlap in a scheduled burst".into());
        }
        requests.extend(request::collect(
            std::slice::from_ref(burst),
            &trace,
            root,
            &replay,
            engine,
            source,
        )?);
        let memory = open_memory(&replay, backend, trace.duration_ns, probe)?;
        if memory.pid != Some(pid) {
            return Err("restore memory receipt came from a different process".into());
        }
        duration = append_memory(&mut samples, duration, memory)?;
    }
    Ok((requests, duration, samples))
}

fn resident_evidence(
    root: &Path,
    directory: &Path,
    engine: Engine,
    burst: &[request::Input],
    slots: usize,
) -> DynResult<u32> {
    #[derive(Deserialize)]
    struct Owner {
        pid: u32,
        slots: usize,
    }

    let owner: Owner = load_json(&directory.join("resident.json"))?;
    if owner.pid == 0 || owner.slots != slots {
        return Err("resident burst did not identify the prepared server and slots".into());
    }
    for (slot, input) in burst.iter().enumerate() {
        let seed = input
            .seed
            .as_ref()
            .ok_or("workload omitted its resident seed")?;
        if input.eviction.is_some() {
            return Err("resident preparation cannot specify an eviction".into());
        }
        request::prepared_stream(
            root,
            &directory.join(format!("seed-{slot:03}")),
            engine,
            slot,
            seed,
        )?;
    }
    Ok(owner.pid)
}

fn overlaps(requests: &[request::Receipt]) -> bool {
    requests
        .iter()
        .map(|row| row.started_ns)
        .max()
        .zip(requests.iter().map(|row| row.finished_ns).min())
        .is_some_and(|(last_start, first_finish)| last_start < first_finish)
}

fn append_memory(samples: &mut Vec<MemorySample>, offset: u64, memory: Memory) -> DynResult<u64> {
    let duration = offset
        .checked_add(memory.duration_ns)
        .ok_or("restore duration overflowed")?;
    for mut sample in memory.samples {
        sample.offset_ns += offset;
        if let Some(last) = samples
            .last_mut()
            .filter(|last| last.offset_ns == sample.offset_ns)
        {
            // Adjacent observations share a boundary after removing the time
            // spent preparing the next burst. Retain both observed peaks.
            last.host_bytes = last.host_bytes.max(sample.host_bytes);
            last.device_bytes = last.device_bytes.max(sample.device_bytes);
        } else {
            samples.push(sample);
        }
    }
    Ok(duration)
}
