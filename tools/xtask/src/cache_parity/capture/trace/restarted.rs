use super::{DynResult, Engine, Request, Workload, burst, write_json_file};
use crate::cache_parity::{
    capture::{restart, server::Server},
    contract::CacheState,
};
use process_supervision::Cancellation;
use serde_json::json;
use std::path::Path;

pub(in crate::cache_parity::capture) fn validate(workload: &Workload) -> DynResult<()> {
    burst::validate(workload, false)
}

pub(in crate::cache_parity::capture) fn capture(
    engine: Engine,
    server: &Server,
    workload: &Workload,
    output: &Path,
    cancellation: &Cancellation,
) -> DynResult<()> {
    burst::capture(
        engine,
        workload,
        output,
        cancellation,
        CacheState::PostRestartL3,
        |burst, path, slots| capture_burst(engine, server, burst, path, cancellation, slots),
    )
}

fn capture_burst(
    engine: Engine,
    server: &Server,
    burst: &[Vec<Request>],
    directory: &Path,
    cancellation: &Cancellation,
    slots: usize,
) -> DynResult<()> {
    let seeds = burst[0]
        .iter()
        .map(burst::seed)
        .collect::<DynResult<Vec<_>>>()?;
    let published = restart::publish(engine, server, &seeds, directory, cancellation, slots)?;
    let replay = directory.join("replay");
    let replay_pid = server.with_ready(
        engine,
        &published.args,
        &replay,
        cancellation,
        |address, memory, stop| {
            super::capture(
                address,
                engine,
                burst,
                memory,
                stop,
                &replay,
                &published.snapshots,
            )
        },
    )?;
    if replay_pid == published.pid {
        return Err("publisher and replay process IDs are identical".into());
    }
    write_json_file(
        &directory.join("restart.json"),
        &json!({"publisher_pid":published.pid,"replay_pid":replay_pid,"slots":slots}),
    )?;
    for ordinal in 1..=burst[0].len() {
        let observation = restart::observation(&replay.join(format!("request-{ordinal:03}")))?;
        let cached = observation["cached_tokens"]
            .as_u64()
            .filter(|n| *n > 0)
            .ok_or_else(|| {
                format!("post-restart request {ordinal} did not reuse any prompt tokens")
            })?;
        let native = &observation["final_server_timings"];
        let restored = match engine {
            Engine::Candidate => {
                matches!(native["cache_source"].as_str(), Some("l3" | "l3-cachegen"))
            }
            Engine::Baseline => native["cache_n"] == cached,
        };
        if !restored {
            return Err(format!(
                "post-restart request {ordinal} did not prove its L3 cache source"
            )
            .into());
        }
    }
    Ok(())
}
