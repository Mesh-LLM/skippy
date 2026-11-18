use super::{
    CapturedExchange, DynResult, Engine, capture_exchange, memory, request_bytes, restart::slot,
};
use crate::{
    artifact,
    cache_parity::contract::{REQUESTS, WorkloadDesign, is_digest},
    command::write_json_file,
};
use process_supervision::Cancellation;
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    fs,
    net::SocketAddr,
    path::{Component, Path, PathBuf},
    time::{Duration, Instant},
};
use tokio::task::{JoinSet, LocalSet};

mod burst;
pub(super) mod durable;
pub(super) mod identity;
pub(super) mod ram;
pub(super) mod resident;
pub(super) mod restarted;

const MAX_BURST: usize = 32;
const MAX_CAPTURE_BYTES: usize = 256 * 1024 * 1024;
const CANCELLATION_POLL: Duration = Duration::from_millis(100);

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema_version: u32,
    design: Option<WorkloadDesign>,
    bursts: Vec<Vec<Entry>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry {
    request: PathBuf,
    request_sha256: String,
    expected_content: Option<String>,
    prompt_tokens: Option<u32>,
    seed: Option<Input>,
    eviction: Option<Input>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    request: PathBuf,
    request_sha256: String,
}

pub(super) struct Workload {
    pub(super) manifest: Vec<u8>,
    pub(super) design: Option<WorkloadDesign>,
    pub(super) bursts: Vec<Vec<Request>>,
}

pub(super) struct Request {
    sha256: String,
    bytes: Vec<u8>,
    expected_content: Option<String>,
    pub(super) seed: Option<(String, Vec<u8>)>,
    pub(super) eviction: Option<(String, Vec<u8>)>,
}

impl Workload {
    pub(super) fn load(path: &Path) -> DynResult<Self> {
        let path = path.canonicalize()?;
        let root = path
            .parent()
            .ok_or("workload requires a parent directory")?;
        let manifest = fs::read(&path)?;
        let spec: Manifest = serde_json::from_slice(&manifest)?;
        if spec.schema_version != 1
            || spec.bursts.is_empty()
            || spec
                .bursts
                .iter()
                .any(|burst| !(1..=MAX_BURST).contains(&burst.len()))
            || spec.bursts.iter().map(Vec::len).sum::<usize>() > REQUESTS as usize
        {
            return Err("expected workload schema 1 and 1–200 requests in bursts of 1–32".into());
        }
        let mut input_bytes = 0usize;
        let mut bursts = Vec::with_capacity(spec.bursts.len());
        for burst in spec.bursts {
            let mut loaded = Vec::with_capacity(burst.len());
            for entry in burst {
                let request = entry.load(root)?;
                for (_, bytes) in request.inputs() {
                    input_bytes = input_bytes.saturating_add(bytes.len());
                }
                if input_bytes > MAX_CAPTURE_BYTES {
                    return Err("workload requests exceed the aggregate input byte limit".into());
                }
                loaded.push(request);
            }
            bursts.push(loaded);
        }
        Ok(Self {
            manifest,
            design: spec.design,
            bursts,
        })
    }

    pub(super) fn retain_inputs(&self, output: &Path) -> DynResult<()> {
        fs::write(output.join("workload.json"), &self.manifest)?;
        let inputs = output.join("inputs");
        fs::create_dir(&inputs)?;
        let mut retained = BTreeSet::new();
        for request in self.bursts.iter().flatten() {
            for (sha, bytes) in request.inputs() {
                if retained.insert(sha) {
                    fs::write(inputs.join(format!("{sha}.json")), bytes)?;
                }
            }
        }
        Ok(())
    }
}

impl Request {
    fn inputs(&self) -> impl Iterator<Item = (&String, &Vec<u8>)> {
        std::iter::once((&self.sha256, &self.bytes)).chain(
            self.seed
                .iter()
                .chain(&self.eviction)
                .map(|(digest, bytes)| (digest, bytes)),
        )
    }
}

impl Input {
    fn load(self, root: &Path) -> DynResult<(String, Vec<u8>)> {
        let bytes = load_input(root, &self.request, &self.request_sha256)?;
        Ok((self.request_sha256, bytes))
    }
}

impl Entry {
    fn load(self, root: &Path) -> DynResult<Request> {
        if self.prompt_tokens == Some(0) {
            return Err("pinned prompt token count must be positive".into());
        }
        let bytes = load_input(root, &self.request, &self.request_sha256)?;
        Ok(Request {
            sha256: self.request_sha256,
            bytes,
            expected_content: self.expected_content,
            seed: self.seed.map(|input| input.load(root)).transpose()?,
            eviction: self.eviction.map(|input| input.load(root)).transpose()?,
        })
    }
}

fn load_input(root: &Path, request: &Path, sha256: &str) -> DynResult<Vec<u8>> {
    if request.as_os_str().is_empty()
        || request
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
        || !is_digest(sha256, 64)
    {
        return Err("workload request must name a relative input with an exact SHA-256".into());
    }
    let path = root.join(request).canonicalize()?;
    if !path.starts_with(root) || !path.is_file() {
        return Err("workload request must be a file inside the workload directory".into());
    }
    let bytes = request_bytes(&path)?;
    if artifact::digest(bytes.as_slice())? != sha256 {
        return Err(format!("workload request digest changed for {}", request.display()).into());
    }
    Ok(bytes)
}

struct Measured {
    ordinal: usize,
    burst: usize,
    started_ns: u64,
    finished_ns: u64,
    exchange: CapturedExchange,
    slot_restore: Option<slot::Receipt>,
}

#[derive(Serialize)]
struct Receipt<'a> {
    ordinal: usize,
    burst: usize,
    started_ns: u64,
    finished_ns: u64,
    request_sha256: &'a str,
    output_matches: Option<bool>,
    request_error: Option<&'a str>,
}

impl Measured {
    fn persist<'a>(
        &'a self,
        bursts: &'a [Vec<Request>],
        engine: Engine,
        root: &Path,
    ) -> DynResult<Receipt<'a>> {
        let request = bursts
            .iter()
            .flatten()
            .nth(self.ordinal - 1)
            .ok_or("captured an unplanned request")?;
        let directory = root.join(format!("request-{:03}", self.ordinal));
        fs::create_dir(&directory)?;
        self.exchange
            .persist(&request.bytes, &directory, Some(engine))?;
        if let Some(restore) = &self.slot_restore {
            restore.retain(&directory)?;
        }
        let output_matches = self
            .exchange
            .result
            .as_ref()
            .ok()
            .zip(request.expected_content.as_ref())
            .map(|(observed, expected)| observed.content == *expected);
        Ok(Receipt {
            ordinal: self.ordinal,
            burst: self.burst,
            started_ns: self.started_ns,
            finished_ns: self.finished_ns,
            request_sha256: &request.sha256,
            output_matches,
            request_error: self.exchange.result.as_ref().err().map(String::as_str),
        })
    }
}

pub(super) fn capture(
    address: SocketAddr,
    engine: Engine,
    bursts: &[Vec<Request>],
    memory: &memory::Monitor<'_>,
    stop: &Cancellation,
    output: &Path,
    snapshots: &[slot::Snapshot],
) -> DynResult<()> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    let local = LocalSet::new();
    let mut captured = Vec::new();
    let mut duration_ns = 0;
    let measured = memory.record_window(&output.join("memory.json"), |started| {
        let result = runtime.block_on(local.run_until(measure(
            address,
            engine,
            bursts,
            stop,
            started,
            &mut captured,
            snapshots,
        )));
        duration_ns = elapsed(started)?;
        result
    });
    captured.sort_by_key(|row| row.ordinal);
    let requests = captured
        .iter()
        .map(|row| row.persist(bursts, engine, output))
        .collect::<DynResult<Vec<_>>>()?;
    write_json_file(
        &output.join("trace.json"),
        &serde_json::json!({
            "schema_version": 1,
            "engine": engine,
            "duration_ns": duration_ns,
            "requests": requests,
            "error": measured.as_ref().err().map(ToString::to_string),
        }),
    )?;
    measured?;
    if requests.iter().any(|row| row.request_error.is_some()) {
        return Err("one or more measured workload requests failed; see trace.json".into());
    }
    Ok(())
}

async fn measure(
    address: SocketAddr,
    engine: Engine,
    bursts: &[Vec<Request>],
    stop: &Cancellation,
    started: Instant,
    captured: &mut Vec<Measured>,
    snapshots: &[slot::Snapshot],
) -> DynResult<()> {
    let mut ordinal = 0;
    let mut response_bytes = 0usize;
    for (burst_index, burst) in bursts.iter().enumerate() {
        if stop.is_cancelled() {
            return Err("workload capture interrupted".into());
        }
        let mut running = JoinSet::new();
        for (slot, request) in burst.iter().enumerate() {
            ordinal += 1;
            let ordinal = ordinal;
            // Each task owns its posted bytes while the prepared workload
            // retains the exact inputs used to hash and persist both legs.
            let bytes = request.bytes.clone();
            let snapshot = snapshots.get(slot).copied();
            running.spawn_local(async move {
                let started_ns = elapsed(started)?;
                let (slot_restore, exchange) =
                    request_exchange(address, engine, bytes, snapshot).await;
                Ok::<_, String>(Measured {
                    ordinal,
                    burst: burst_index + 1,
                    started_ns,
                    finished_ns: elapsed(started)?,
                    exchange,
                    slot_restore,
                })
            });
        }
        while !running.is_empty() {
            tokio::select! {
                next = running.join_next() => {
                    let row = next.ok_or("measured workload task disappeared")???;
                    response_bytes = response_bytes.saturating_add(row.exchange.response.len());
                    captured.push(row);
                    if response_bytes > MAX_CAPTURE_BYTES {
                        return Err("workload responses exceed the aggregate capture limit".into());
                    }
                }
                () = tokio::time::sleep(CANCELLATION_POLL) => {
                    if stop.is_cancelled() {
                        return Err("workload capture interrupted".into());
                    }
                }
            }
        }
    }
    Ok(())
}

async fn request_exchange(
    address: SocketAddr,
    engine: Engine,
    bytes: Vec<u8>,
    snapshot: Option<slot::Snapshot>,
) -> (Option<slot::Receipt>, CapturedExchange) {
    let restore = match snapshot {
        Some(snapshot) => match snapshot.restore(address).await {
            Ok(receipt) => Some(receipt),
            Err(error) => {
                return (
                    None,
                    CapturedExchange {
                        response: Vec::new(),
                        result: Err(error.to_string()),
                    },
                );
            }
        },
        None => None,
    };
    (
        restore,
        capture_exchange(address, bytes, Some(engine)).await,
    )
}

fn elapsed(start: Instant) -> Result<u64, String> {
    u64::try_from(start.elapsed().as_nanos()).map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests;
