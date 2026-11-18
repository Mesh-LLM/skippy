use super::{DynResult, Engine, Request, Workload, burst, write_json_file};
use crate::{
    cache_parity::{
        capture::{capture as capture_request, restart::observation, server::Server},
        contract::CacheState,
    },
    execution,
};
use process_supervision::Cancellation;
use serde_json::json;
use std::{fs, net::SocketAddr, path::Path};

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
    let slots = workload
        .bursts
        .iter()
        .map(Vec::len)
        .max()
        .ok_or("resident workload has no bursts")?;
    let args = match engine {
        Engine::Candidate => execution::strings(&[
            "--prefix-cache",
            "on",
            "--prefix-cache-ram",
            "off",
            "--kv-cache-disk",
            "off",
        ]),
        Engine::Baseline => {
            execution::strings(&["--cache-ram", "0", "--parallel", &slots.to_string()])
        }
    };
    server.with_ready(
        engine,
        &args,
        output,
        cancellation,
        |address, memory, stop| {
            burst::capture(
                engine,
                workload,
                output,
                stop,
                CacheState::WarmL1,
                |burst, path, slots| {
                    let replay = prepare(engine, address, &burst[0], path, stop)?;
                    super::capture(address, engine, burst, memory, stop, &replay, &[])?;
                    verify(engine, &replay, burst[0].len())?;
                    write_json_file(
                        &path.join("resident.json"),
                        &json!({"pid":memory.pid,"slots":slots}),
                    )
                },
            )
        },
    )?;
    Ok(())
}

fn prepare(
    engine: Engine,
    address: SocketAddr,
    burst: &[Request],
    path: &Path,
    stop: &Cancellation,
) -> DynResult<std::path::PathBuf> {
    fs::create_dir(path)?;
    for (slot, request) in burst.iter().enumerate() {
        if stop.is_cancelled() {
            return Err("resident preparation interrupted".into());
        }
        capture_request(
            address,
            burst::seed(request)?,
            &path.join(format!("seed-{slot:03}")),
            Some(engine),
        )?;
    }
    let replay = path.join("replay");
    fs::create_dir(&replay)?;
    Ok(replay)
}

fn verify(engine: Engine, replay: &Path, count: usize) -> DynResult<()> {
    for ordinal in 1..=count {
        let observed = observation(&replay.join(format!("request-{ordinal:03}")))?;
        let cached = observed["cached_tokens"]
            .as_u64()
            .filter(|n| *n > 0)
            .ok_or("resident replay did not reuse any tokens")?;
        let native = &observed["final_server_timings"];
        let valid = match engine {
            Engine::Candidate => {
                matches!(native["cache_source"].as_str(), Some("resident" | "radix"))
            }
            Engine::Baseline => native["cache_n"] == cached,
        };
        if !valid {
            return Err(
                format!("resident request {ordinal} did not prove its L1 cache source").into(),
            );
        }
    }
    Ok(())
}
