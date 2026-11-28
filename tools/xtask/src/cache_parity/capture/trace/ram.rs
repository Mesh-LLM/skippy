use super::{DynResult, Engine, Request, Workload, burst, write_json_file};
use crate::{
    cache_parity::{
        capture::{capture as capture_request, host_ram, restart::observation, server::Server},
        contract::CacheState,
    },
    execution,
};
use process_supervision::Cancellation;
use serde_json::{Value, json};
use std::{fs, net::SocketAddr, path::Path};

pub(in crate::cache_parity::capture) fn validate(
    workload: &Workload,
    skippy: &Server,
) -> DynResult<()> {
    burst::validate(workload, true)?;
    for request in workload.bursts.iter().flatten() {
        host_ram::validate(
            skippy,
            &request.bytes,
            burst::eviction(request)?,
            Some(burst::seed(request)?),
        )?;
    }
    Ok(())
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
        CacheState::L2Restore,
        |requests, path, slots| capture_burst(engine, server, requests, path, cancellation, slots),
    )
}

fn capture_burst(
    engine: Engine,
    server: &Server,
    requests: &[Vec<Request>],
    output: &Path,
    cancellation: &Cancellation,
    slots: usize,
) -> DynResult<()> {
    let state = output
        .parent()
        .ok_or("missing L2 burst parent")?
        .canonicalize()?
        .join(output.file_name().ok_or("missing L2 burst name")?)
        .join("state");
    let args = match engine {
        Engine::Candidate => execution::strings(&[
            "--prefix-cache",
            "on",
            "--prefix-cache-max-entries",
            "1",
            "--prefix-cache-ram",
            "2GiB",
            "--kv-cache-disk",
            "auto",
            "--kv-cache-disk-dir",
            state.to_str().ok_or("non-UTF-8 cache path")?,
        ]),
        Engine::Baseline => execution::strings(&[
            "--cache-ram",
            "2048",
            "--no-cache-idle-slots",
            "--parallel",
            &slots.to_string(),
            "--verbosity",
            "5",
        ]),
    };
    let measured = output.join("replay");
    let mut disk_before = None;
    let pid = server.with_ready(
        engine,
        &args,
        output,
        cancellation,
        |address, memory, stop| {
            let prep = Preparation {
                engine,
                address,
                output,
                state: &state,
                stop,
            };
            prep.seed_and_evict(&requests[0])?;
            if engine == Engine::Candidate {
                disk_before = Some(prep.condition(&requests[0])?);
            }
            fs::create_dir(&measured)?;
            super::capture(address, engine, requests, memory, stop, &measured, &[])
        },
    )?;
    let count = requests[0].len();
    write_json_file(
        &output.join("l2.json"),
        &json!({"pid":pid,"slots":slots,"disk_payload_before":disk_before}),
    )?;
    verify(engine, output, &measured, count, disk_before)
}

struct Preparation<'a> {
    engine: Engine,
    address: SocketAddr,
    output: &'a Path,
    state: &'a Path,
    stop: &'a Cancellation,
}

impl Preparation<'_> {
    fn request(&self, bytes: &[u8], path: &Path) -> DynResult<Value> {
        if self.stop.is_cancelled() {
            return Err("L2 preparation interrupted".into());
        }
        capture_request(self.address, bytes, path, Some(self.engine))?;
        observation(path)
    }

    fn seed_and_evict(&self, requests: &[Request]) -> DynResult<()> {
        for (slot, request) in requests.iter().enumerate() {
            let path = self.output.join(format!("slot-{slot:03}"));
            fs::create_dir(&path)?;
            if self.request(burst::seed(request)?, &path.join("seed"))?["cached_tokens"] != 0 {
                return Err(format!("L2 slot {slot} seed reused part of an earlier slot").into());
            }
            if self.engine == Engine::Candidate {
                host_ram::await_published_prefixes(self.state, slot + 1, self.stop)?;
            }
        }
        for (slot, request) in requests.iter().enumerate() {
            let path = self.output.join(format!("slot-{slot:03}/eviction"));
            self.request(burst::eviction(request)?, &path)?;
        }
        Ok(())
    }

    fn condition(&self, requests: &[Request]) -> DynResult<u64> {
        let mut before = None;
        for (slot, request) in requests.iter().enumerate() {
            let path = self.output.join(format!("slot-{slot:03}"));
            let evict = burst::eviction(request)?;
            let seed = burst::seed(request)?;
            let mut promoted = false;
            for attempt in 1..=5 {
                self.request(
                    evict,
                    &path.join(format!("conditioning-eviction-{attempt:02}")),
                )?;
                let observed = self.request(
                    seed,
                    &path.join(format!("conditioning-replay-{attempt:02}")),
                )?;
                if observed["final_server_timings"]["cache_source"] == "l2" {
                    promoted = true;
                    break;
                }
            }
            if !promoted {
                return Err(format!("L2 slot {slot} did not prove host-RAM preparation").into());
            }
            let observed = self.request(evict, &path.join("final-eviction"))?;
            before = Some(host_ram::disk_payload_counter(&observed)?);
        }
        before.ok_or_else(|| "missing L2 preparation counter".into())
    }
}

fn verify(
    engine: Engine,
    output: &Path,
    measured: &Path,
    count: usize,
    disk_before: Option<u64>,
) -> DynResult<()> {
    if engine == Engine::Baseline {
        host_ram::native::slot_restores(output, count)?;
    }
    for slot in 0..count {
        let replay = observation(&measured.join(format!("request-{:03}", slot + 1)))?;
        let cached = replay["cached_tokens"]
            .as_u64()
            .filter(|n| *n > 0)
            .ok_or("L2 replay did not cache any tokens")?;
        let native = &replay["final_server_timings"];
        let valid = match engine {
            Engine::Candidate => {
                native["cache_source"] == "l2"
                    && Some(host_ram::disk_payload_counter(&replay)?) == disk_before
            }
            Engine::Baseline => {
                let eviction = observation(&output.join(format!("slot-{slot:03}/eviction")))?;
                native["cache_n"] == cached
                    && eviction["cached_tokens"]
                        .as_u64()
                        .is_some_and(|n| n < cached)
            }
        };
        if !valid {
            return Err(format!(
                "burst slot {slot} did not prove RAM reuse without disk reads, source {:?}",
                native["cache_source"]
            )
            .into());
        }
    }
    Ok(())
}
