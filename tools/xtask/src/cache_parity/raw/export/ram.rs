use super::{DynResult, Engine, load_json, request};
use crate::cache_parity::capture::slot_restores;
use serde::Deserialize;
use serde_json::{Value, json};
use std::path::Path;

#[derive(Deserialize)]
struct Process {
    pid: u32,
    slots: usize,
    disk_payload_before: Option<u64>,
}

pub(super) enum Proof {
    Candidate { disk_before: u64 },
    Baseline(Vec<(u64, u64)>),
}

pub(super) fn evidence(
    root: &Path,
    directory: &Path,
    engine: Engine,
    burst: &[request::Input],
    slots: usize,
) -> DynResult<(u32, request::Source)> {
    let process: Process = load_json(&directory.join("l2.json"))?;
    if process.pid == 0 || process.slots != slots || burst.is_empty() {
        return Err("L2 preparation has an invalid server process receipt".into());
    }
    let mut cached = Vec::new();
    for (slot, input) in burst.iter().enumerate() {
        let path = directory.join(format!("slot-{slot:03}"));
        let seed = input.seed.as_ref().ok_or("workload omitted its L2 seed")?;
        let displacement = input
            .eviction
            .as_ref()
            .ok_or("workload omitted its L2 eviction")?;
        let initial = request::prepared_stream(root, &path.join("seed"), engine, slot, seed)?;
        if initial["cached_tokens"] != 0 {
            return Err("L2 publisher seed did not prove a successful cold request".into());
        }
        let eviction =
            request::prepared_stream(root, &path.join("eviction"), engine, slot, displacement)?;
        cached.push(
            eviction["cached_tokens"]
                .as_u64()
                .ok_or("missing L2 eviction cached token count")?,
        );
    }
    let proof = match engine {
        Engine::Candidate => {
            let slot = burst.len() - 1;
            let eviction = burst[slot]
                .eviction
                .as_ref()
                .ok_or("missing final L2 eviction input")?;
            let observation = request::prepared_stream(
                root,
                &directory.join(format!("slot-{slot:03}/final-eviction")),
                engine,
                slot,
                eviction,
            )?;
            let disk_before = counter(&observation)?;
            if process.disk_payload_before != Some(disk_before) {
                return Err(
                    "L2 preparation counter differs from the retained final eviction".into(),
                );
            }
            Proof::Candidate { disk_before }
        }
        Engine::Baseline => {
            if process.disk_payload_before.is_some() {
                return Err("unexpected Skippy disk counter for the native baseline".into());
            }
            let timings = slot_restores(directory, burst.len())?
                .into_iter()
                .zip(cached)
                .map(|(ms, tokens)| Ok((request::millis(&json!(ms))?, tokens)))
                .collect::<DynResult<_>>()?;
            Proof::Baseline(timings)
        }
    };
    Ok((process.pid, request::Source::Ram(proof)))
}

fn counter(observation: &Value) -> DynResult<u64> {
    observation["final_server_timings"]["cache_disk_payload_bytes_read_total"]
        .as_u64()
        .ok_or_else(|| "missing L2 disk payload counter in the preparation stream".into())
}
