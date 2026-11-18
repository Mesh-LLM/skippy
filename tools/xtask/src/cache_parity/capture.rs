use super::{DynResult, contract::Engine};
use crate::{artifact, command::write_json_file};
use http_body_util::BodyExt;
use serde_json::{Value, json};
use std::{
    fs,
    net::SocketAddr,
    path::Path,
    time::{Duration, Instant},
};

mod accounting;
mod host_ram;
mod http;
mod memory;
pub(super) mod pair;
mod phases;
mod restart;
mod server;
mod sse;
mod trace;

pub(super) use host_ram::native::slot_restores;
pub(super) use restart::validate_managed_args;
pub(super) use trace::identity::Identity as TraceIdentity;

const MAX_BYTES: usize = 16 * 1024 * 1024;
const TIMEOUT: Duration = Duration::from_secs(240);

pub(super) fn run(address: SocketAddr, request: &Path, output: &Path) -> DynResult<()> {
    capture(address, &request_bytes(request)?, output, None)
}

fn request_bytes(path: &Path) -> DynResult<Vec<u8>> {
    let request = fs::read(path)?;
    let parsed: Value = serde_json::from_slice(&request)?;
    if parsed["stream"] != true || parsed["stream_options"]["include_usage"] != true {
        return Err("cache parity requests must enable streaming and usage".into());
    }
    Ok(request)
}

fn capture(
    address: SocketAddr,
    request: &[u8],
    output: &Path,
    engine: Option<Engine>,
) -> DynResult<()> {
    if !address.ip().is_loopback() || address.port() == 0 {
        return Err("cache parity capture requires a loopback address and a nonzero port".into());
    }
    fs::create_dir(output)?;
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    let captured = runtime.block_on(capture_exchange(address, request.to_vec(), engine));
    captured.persist(request, output, engine)?;
    match captured.result {
        Ok(_) => Ok(()),
        Err(error) => Err(error.into()),
    }
}

struct CapturedExchange {
    response: Vec<u8>,
    result: Result<sse::Observation, String>,
}

impl CapturedExchange {
    fn persist(&self, request: &[u8], output: &Path, engine: Option<Engine>) -> DynResult<()> {
        fs::write(output.join("request.json"), request)?;
        fs::write(output.join("response.sse"), &self.response)?;
        write_json_file(
            &output.join("observation.json"),
            &json!({
                "schema_version": 3,
                "engine": engine,
                "request_sha256": artifact::digest(request)?,
                "response_sha256": artifact::digest(self.response.as_slice())?,
                "observation": self.result.as_ref().ok(),
                "error": self.result.as_ref().err(),
            }),
        )
    }
}

async fn capture_exchange(
    address: SocketAddr,
    request: Vec<u8>,
    engine: Option<Engine>,
) -> CapturedExchange {
    let mut response = Vec::new();
    let started = Instant::now();
    let result = tokio::time::timeout(
        TIMEOUT,
        exchange(address, request, &mut response, started, engine),
    )
    .await
    .map_err(|error| error.to_string())
    .and_then(|result| result.map_err(|error| error.to_string()));
    CapturedExchange { response, result }
}

pub(super) fn replay_transcript(response: &[u8], engine: Engine) -> DynResult<Value> {
    let mut stream = sse::Capture::default();
    // The concatenated transcript has no transport arrival clock. Derive
    // content, usage and native timings here, and check the retained client
    // timestamps separately against the measured request interval.
    stream.push(response, 1)?;
    Ok(serde_json::to_value(stream.finish(Some(engine))?)?)
}

async fn exchange(
    address: SocketAddr,
    request: Vec<u8>,
    response: &mut Vec<u8>,
    started: Instant,
    engine: Option<Engine>,
) -> DynResult<sse::Observation> {
    http::post(
        address,
        "/v1/chat/completions",
        "text/event-stream",
        request,
        async |mut result| {
            let event_stream = result
                .headers()
                .get("content-type")
                .and_then(|header| header.to_str().ok())
                .is_some_and(|header| {
                    header.split(';').next().map(str::trim) == Some("text/event-stream")
                });
            let successful = result.status().is_success() && event_stream;
            let mut capture = sse::Capture::default();
            while let Some(frame) = result.body_mut().frame().await {
                let frame = frame?;
                if let Some(data) = frame.data_ref() {
                    let observed_ns: u64 = started.elapsed().as_nanos().try_into()?;
                    if data.len() > MAX_BYTES - response.len() {
                        return Err("stream capture exceeded 16 MiB".into());
                    }
                    response.extend_from_slice(data);
                    if successful {
                        capture.push(data, observed_ns)?;
                    }
                }
            }
            if !successful {
                return Err(format!(
                    "expected a successful chat event stream, received {}",
                    result.status()
                )
                .into());
            }
            capture.finish(engine)
        },
    )
    .await
}
