use super::DynResult;
use http_body_util::{BodyExt, Full};
use hyper::{
    Request, Response,
    body::{Bytes, Incoming},
};
use hyper_util::rt::TokioIo;
use std::{
    net::SocketAddr,
    time::{Duration, Instant},
};

pub(super) async fn post<T>(
    address: SocketAddr,
    path: &str,
    accept: &str,
    body: Vec<u8>,
    receive: impl AsyncFnOnce(Response<Incoming>) -> DynResult<T>,
) -> DynResult<T> {
    let socket = tokio::net::TcpStream::connect(address).await?;
    socket.set_nodelay(true)?;
    let (mut sender, connection) =
        hyper::client::conn::http1::handshake(TokioIo::new(socket)).await?;
    let request = Request::post(path)
        .header("host", address.to_string())
        .header("content-type", "application/json")
        .header("accept", accept)
        .header("connection", "close")
        .body(Full::new(Bytes::from(body)))?;
    let response = async { receive(sender.send_request(request).await?).await };
    tokio::pin!(response);
    tokio::select! {
        result = &mut response => result,
        result = connection => { result?; response.await }
    }
}

pub(super) fn post_json(
    address: SocketAddr,
    path: &str,
    body: Vec<u8>,
) -> DynResult<(serde_json::Value, u64)> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(post_json_async(address, path, body))
}

pub(super) async fn post_json_async(
    address: SocketAddr,
    path: &str,
    body: Vec<u8>,
) -> DynResult<(serde_json::Value, u64)> {
    let started = Instant::now();
    let bytes = tokio::time::timeout(
        Duration::from_secs(90),
        post(address, path, "application/json", body, async |response| {
            let status = response.status();
            let bytes = http_body_util::Limited::new(response.into_body(), 64 * 1024)
                .collect()
                .await
                .map_err(|error| format!("could not read slot response: {error}"))?
                .to_bytes();
            if !status.is_success() {
                return Err(format!(
                    "slot request {path} failed with {status}: {}",
                    String::from_utf8_lossy(&bytes)
                )
                .into());
            }
            Ok(bytes)
        }),
    )
    .await??;
    let elapsed = started.elapsed().as_nanos().try_into()?;
    Ok((serde_json::from_slice(&bytes)?, elapsed))
}
