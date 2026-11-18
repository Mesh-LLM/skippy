use super::{DynResult, Engine, capture as capture_request, http, server::Server};
use crate::{command::write_json_file, execution};
use process_supervision::Cancellation;
use serde_json::{Value, json};
use std::{
    fs,
    path::Path,
    time::{Duration, Instant},
};

const DISK_BUDGET: &str = "1GiB";
pub(super) mod slot;
const CANDIDATE_FLAGS: &[&str] = &[
    "--prefix-cache",
    "--prefix-cache-ram",
    "--kv-cache-disk",
    "--kv-cache-disk-dir",
];
const BASELINE_FLAGS: &[&str] = &[
    "--slot-save-path",
    "--parallel",
    "-np",
    "--cache-ram",
    "-cram",
];

pub(super) fn validate_args(skippy: &Server, baseline: &Server) -> DynResult<()> {
    validate_managed_args(&skippy.args, &baseline.args)
}

pub(in crate::cache_parity) fn validate_managed_args(
    skippy: &[String],
    baseline: &[String],
) -> DynResult<()> {
    for (args, controlled) in [(skippy, CANDIDATE_FLAGS), (baseline, BASELINE_FLAGS)] {
        if let Some(arg) = args
            .iter()
            .find(|arg| controlled.contains(&arg.split('=').next().unwrap_or_default()))
        {
            return Err(
                format!("cache preparation controls {arg}; omit it from engine args").into(),
            );
        }
    }
    Ok(())
}

pub(in crate::cache_parity::capture) struct Published {
    pub(in crate::cache_parity::capture) pid: u32,
    pub(in crate::cache_parity::capture) args: Vec<String>,
    pub(in crate::cache_parity::capture) snapshots: Vec<slot::Snapshot>,
}

pub(in crate::cache_parity::capture) fn publish(
    engine: Engine,
    server: &Server,
    seeds: &[&[u8]],
    output: &Path,
    cancellation: &Cancellation,
    slots: usize,
) -> DynResult<Published> {
    fs::create_dir(output)?;
    let state = output.canonicalize()?.join("state");
    fs::create_dir(&state)?;
    let state_path = state.to_str().ok_or("non-UTF-8 cache path")?;
    let disk_budget = if slots == 1 { DISK_BUDGET } else { "auto" };
    let args = match engine {
        Engine::Candidate => execution::strings(&[
            "--prefix-cache",
            "on",
            "--prefix-cache-ram",
            "off",
            "--kv-cache-disk",
            disk_budget,
            "--kv-cache-disk-dir",
            state_path,
        ]),
        Engine::Baseline => execution::strings(&[
            "--cache-ram",
            "0",
            "--parallel",
            &slots.to_string(),
            "--slot-save-path",
            state_path,
        ]),
    };
    let publish = output.join("publish");
    let publisher_pid =
        server.with_ready(engine, &args, &publish, cancellation, |address, _, stop| {
            for (slot, seed) in seeds.iter().enumerate() {
                let (seed_output, receipt) = seed_paths(&publish, slot, seeds.len());
                capture_request(address, seed, &seed_output, Some(engine))?;
                if observation(&seed_output)?["cached_tokens"] != 0 {
                    return Err(format!(
                        "seed slot {slot} reused part of an earlier seed on the publisher"
                    )
                    .into());
                }
                match engine {
                    Engine::Candidate => await_publication(&state, stop, slot + 1)?,
                    Engine::Baseline => slot::save(address, &receipt, slot)?,
                }
            }
            Ok(())
        })?;

    let snapshots = match engine {
        Engine::Candidate => {
            confirm_disk_inventory(server, state_path, disk_budget, &publish, seeds.len())?;
            Vec::new()
        }
        Engine::Baseline => (0..seeds.len())
            .map(|slot| {
                let (_, receipt) = seed_paths(&publish, slot, seeds.len());
                slot::Snapshot::open(&state, &receipt, slot)
            })
            .collect::<DynResult<_>>()?,
    };
    Ok(Published {
        pid: publisher_pid,
        args,
        snapshots,
    })
}

fn seed_paths(
    directory: &Path,
    slot: usize,
    count: usize,
) -> (std::path::PathBuf, std::path::PathBuf) {
    if count == 1 {
        (directory.join("seed"), directory.to_owned())
    } else {
        let seed = directory.join(format!("seed-{:03}", slot + 1));
        (seed.clone(), seed)
    }
}

pub(super) fn capture(
    engine: Engine,
    server: &Server,
    seed: &[u8],
    replay: &[u8],
    output: &Path,
    cancellation: &Cancellation,
) -> DynResult<()> {
    let published = publish(engine, server, &[seed], output, cancellation, 1)?;
    let publisher_pid = published.pid;
    let replay_output = output.join("replay");
    let replay_pid = server.with_ready(
        engine,
        &published.args,
        &replay_output,
        cancellation,
        |address, memory, _| {
            let capture_output = replay_output.join("capture");
            memory.record(&capture_output.join("memory.json"), || {
                if let Some(snapshot) = published.snapshots.first() {
                    snapshot.restore_sync(address)?.retain(&replay_output)?;
                }
                capture_request(address, replay, &capture_output, Some(engine))
            })
        },
    )?;
    let replay = observation(&replay_output.join("capture"))?;
    let source = match (
        engine,
        replay["final_server_timings"]["cache_source"].as_str(),
    ) {
        (Engine::Candidate, Some(source @ ("l3" | "l3-cachegen"))) => source,
        (Engine::Baseline, _) => "slot_restore",
        (Engine::Candidate, _) => {
            return Err("restarted Skippy did not report an L3 restore".into());
        }
    };
    let cached = replay["cached_tokens"]
        .as_u64()
        .filter(|count| *count > 0)
        .ok_or("post-restart replay did not reuse any prompt tokens")?;
    if publisher_pid == replay_pid {
        return Err("publisher and replay process IDs are identical".into());
    }
    let (restore_client_ns, restore_engine_ms) = if engine == Engine::Baseline {
        let receipt = read_json(&replay_output.join("slot-restore.json"))?;
        (
            receipt["client_ns"]
                .as_u64()
                .ok_or("missing slot restore client timing")?,
            receipt["response"]["timings"]["restore_ms"]
                .as_f64()
                .ok_or("missing native slot restore timing")?,
        )
    } else {
        // Skippy's streamed timestamps already include its automatic restore.
        (0, 0.0)
    };
    let end_to_end_ttft_ns = replay["first_token_ns"]
        .as_u64()
        .and_then(|ns| ns.checked_add(restore_client_ns))
        .ok_or("invalid replay timing")?;
    let sampled_ttft_ms = replay["sampled_phases"]["ttft_ms"]
        .as_f64()
        .map(|ms| ms + restore_engine_ms);
    write_json_file(
        &output.join("restart.json"),
        &json!({
            "publisher_pid":publisher_pid, "replay_pid":replay_pid, "cache_source":source,
            "cached_tokens":cached, "prompt_tokens":replay["prompt_tokens"],
            "end_to_end_ttft_ns":end_to_end_ttft_ns, "sampled_ttft_ms":sampled_ttft_ms,
        }),
    )
}

fn await_publication(state: &Path, stop: &Cancellation, minimum: usize) -> DynResult<()> {
    let manifests = state.join("manifests");
    let deadline = Instant::now() + Duration::from_secs(30);
    while !stop.is_cancelled() && Instant::now() < deadline {
        let mut published = 0;
        for entry in fs::read_dir(&manifests)? {
            let entry = entry?;
            if entry
                .path()
                .extension()
                .is_some_and(|extension| extension == "json")
                && entry.file_type()?.is_file()
            {
                published += 1;
            }
        }
        if published >= minimum {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    Err(format!("Skippy did not commit {minimum} disk checkpoints before shutdown").into())
}

fn confirm_disk_inventory(
    server: &Server,
    state: &str,
    disk_budget: &str,
    output: &Path,
    minimum: usize,
) -> DynResult<()> {
    let program = server.binary.to_str().ok_or("non-UTF-8 engine path")?;
    let args = execution::strings(&[
        "kv-cache",
        "--kv-cache-disk",
        disk_budget,
        "--kv-cache-disk-dir",
        state,
        "--output",
        "json",
        "status",
    ]);
    let result = execution::capture_for(program, &args, &[], 240)?;
    let status: Value = serde_json::from_str(&result)?;
    write_json_file(&output.join("disk-status.json"), &status)?;
    if status["available"] != true
        || status["usage"]["manifests"]
            .as_u64()
            .is_none_or(|n| n < minimum as u64)
    {
        return Err(format!(
            "offline Skippy inventory did not contain {minimum} committed disk checkpoints"
        )
        .into());
    }
    Ok(())
}

pub(super) fn observation(output: &Path) -> DynResult<Value> {
    let record = read_json(&output.join("observation.json"))?;
    if record["error"].is_null() && record["observation"].is_object() {
        Ok(record["observation"].clone())
    } else {
        Err("missing successful stream observation".into())
    }
}

fn read_json(path: &Path) -> DynResult<Value> {
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}
