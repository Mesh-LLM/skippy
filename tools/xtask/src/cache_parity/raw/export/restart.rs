use super::{DynResult, Engine, load_json, request};
use crate::cache_parity::contract::is_digest;
use serde::Deserialize;
use serde_json::Value;
use std::path::Path;

#[derive(Deserialize)]
struct Owners {
    publisher_pid: u32,
    replay_pid: u32,
    slots: usize,
}

pub(super) fn evidence(
    root: &Path,
    directory: &Path,
    engine: Engine,
    burst: &[request::Input],
    slots: usize,
) -> DynResult<(u32, request::Source)> {
    let owners: Owners = load_json(&directory.join("restart.json"))?;
    if owners.slots != slots
        || owners.publisher_pid == 0
        || owners.replay_pid == 0
        || owners.publisher_pid == owners.replay_pid
    {
        return Err("post-restart burst needs distinct publisher and replay processes".into());
    }
    let snapshots = verify_publication(root, &directory.join("publish"), engine, burst)?;
    Ok((owners.replay_pid, request::Source::Restarted(snapshots)))
}

fn verify_publication(
    root: &Path,
    publish: &Path,
    engine: Engine,
    burst: &[request::Input],
) -> DynResult<Vec<Snapshot>> {
    let mut snapshots = Vec::new();
    for (slot, input) in burst.iter().enumerate() {
        let seed = if burst.len() == 1 {
            publish.join("seed")
        } else {
            publish.join(format!("seed-{:03}", slot + 1))
        };
        let observation = request::prepared_stream(
            root,
            &seed,
            engine,
            slot,
            input
                .seed
                .as_ref()
                .ok_or("workload slot omitted its pinned seed")?,
        )?;
        if observation["cached_tokens"] != 0 {
            return Err("publisher seed did not prove a successful cold request".into());
        }
        if engine == Engine::Baseline {
            let receipt = if burst.len() == 1 { publish } else { &seed };
            snapshots.push(Snapshot::read(receipt, slot)?);
        }
    }
    if engine == Engine::Candidate {
        let status: Value = load_json(&publish.join("disk-status.json"))?;
        if status["available"] != true
            || status["usage"]["manifests"]
                .as_u64()
                .is_none_or(|n| n < burst.len() as u64)
        {
            return Err("publisher did not leave the required durable cache inventory".into());
        }
    }
    Ok(snapshots)
}

pub(super) struct Snapshot {
    slot: usize,
    filename: String,
    tokens: u64,
    bytes: u64,
}

impl Snapshot {
    fn read(path: &Path, slot: usize) -> DynResult<Self> {
        let saved: Value = load_json(&path.join("slot-save.json"))?;
        let snapshot: Value = load_json(&path.join("snapshot.json"))?;
        let response = &saved["response"];
        let filename = response["filename"]
            .as_str()
            .filter(|name| !name.is_empty())
            .ok_or("missing native snapshot name")?;
        let tokens = positive(&response["n_saved"])?;
        let bytes = positive(&response["n_written"])?;
        if response["id_slot"] != slot
            || snapshot["bytes"] != bytes
            || !snapshot["sha256"]
                .as_str()
                .is_some_and(|sha| is_digest(sha, 64))
        {
            return Err("native snapshot does not match its publication receipt".into());
        }
        Ok(Self {
            slot,
            filename: filename.into(),
            tokens,
            bytes,
        })
    }

    pub(super) fn restore_time(&self, path: &Path) -> DynResult<(u64, u64)> {
        let restored: Value = load_json(&path.join("slot-restore.json"))?;
        let response = &restored["response"];
        let client_ns = positive(&restored["client_ns"])?;
        let engine_ns = request::millis(&response["timings"]["restore_ms"])?;
        if response["id_slot"] != self.slot
            || response["filename"] != self.filename
            || response["n_restored"] != self.tokens
            || response["n_read"] != self.bytes
            || engine_ns > client_ns
        {
            return Err(
                "native slot restore does not match its published snapshot and timing".into(),
            );
        }
        Ok((client_ns, engine_ns))
    }
}

fn positive(value: &Value) -> DynResult<u64> {
    value
        .as_u64()
        .filter(|n| *n > 0)
        .ok_or_else(|| "missing positive slot receipt count or duration".into())
}
