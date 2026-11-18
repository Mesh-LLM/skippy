use super::{DynResult, Engine, Request, Workload, durable::remove_private_state, write_json_file};
use crate::cache_parity::contract::CacheState;
use process_supervision::Cancellation;
use serde_json::{Value, json};
use std::{fs, path::Path};

pub(super) fn seed(request: &Request) -> DynResult<&[u8]> {
    request
        .seed
        .as_ref()
        .map(|(_, bytes)| bytes.as_slice())
        .ok_or_else(|| "each prepared request needs its own pinned seed".into())
}

pub(super) fn eviction(request: &Request) -> DynResult<&[u8]> {
    request
        .eviction
        .as_ref()
        .map(|(_, bytes)| bytes.as_slice())
        .ok_or_else(|| "each L2 restore request needs its own pinned eviction".into())
}

pub(super) fn validate(workload: &Workload, ram: bool) -> DynResult<()> {
    for (index, burst) in workload.bursts.iter().enumerate() {
        for (slot, request) in burst.iter().enumerate() {
            let prepared = seed(request)?;
            let mut bodies = vec![request.bytes.as_slice(), prepared];
            if ram {
                let displacement = eviction(request)?;
                if bodies.contains(&displacement) {
                    return Err("L2 eviction must differ from its seed and replay".into());
                }
                bodies.push(displacement);
            } else if request.eviction.is_some() {
                return Err("only L2 bursts can specify an eviction".into());
            }
            for bytes in bodies {
                let body: Value = serde_json::from_slice(bytes)?;
                if body["id_slot"] != slot || body["cache_prompt"] != true {
                    return Err(format!("restore burst {} must pin id_slot {slot} and enable cache_prompt in all slot requests", index + 1).into());
                }
            }
        }
    }
    Ok(())
}

pub(super) fn capture(
    engine: Engine,
    workload: &Workload,
    output: &Path,
    cancellation: &Cancellation,
    state: CacheState,
    run: impl Fn(&[Vec<Request>], &Path, usize) -> DynResult<()>,
) -> DynResult<()> {
    let scope = match state {
        CacheState::WarmL1 => "resident_bursts",
        CacheState::PostRestartL3 => "post_restart_bursts",
        CacheState::L2Restore => "l2_restore_bursts",
        _ => return Err("expected a restore schedule".into()),
    };
    let slots = workload
        .bursts
        .iter()
        .map(Vec::len)
        .max()
        .ok_or("restore workload has no bursts")?;
    fs::create_dir_all(output)?;
    let mut rows = Vec::new();
    for (index, burst) in workload.bursts.iter().enumerate() {
        if cancellation.is_cancelled() {
            return Err("restore workload interrupted".into());
        }
        let ordinal = index + 1;
        let directory = format!("burst-{ordinal:03}");
        let path = output.join(&directory);
        let result = run(std::slice::from_ref(burst), &path, slots);
        let cleanup = remove_private_state(&path);
        let error = result.and(cleanup).err();
        rows.push(
            json!({"ordinal":ordinal,"directory":directory,"requests":burst.len(),
            "error":error.as_ref().map(ToString::to_string)}),
        );
        write_json_file(
            &output.join("trace.json"),
            &json!({
                "schema_version":1,"scope":scope,"cache_state":state,"engine":engine,"bursts":rows,
            }),
        )?;
    }
    if rows.iter().any(|row| !row["error"].is_null()) {
        return Err(format!("one or more {scope} failed; see trace.json").into());
    }
    Ok(())
}
