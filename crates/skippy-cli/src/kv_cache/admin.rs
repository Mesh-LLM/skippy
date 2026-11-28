//! Local administration for the process that owns an L3 store.

use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    net::{Ipv4Addr, SocketAddrV4},
    path::Path,
    time::Duration,
};

use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use skippy_cache::{L3CacheManager, fsinfo};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    task::{JoinHandle, JoinSet},
    time::timeout,
};

use super::{Operation, Report};

#[cfg(test)]
mod tests;

const ENDPOINT_FILE: &str = ".cache-admin.json";
const ENDPOINT_VERSION: u32 = 1;
const REQUEST_LIMIT: u32 = 16 * 1024;
const REPLY_LIMIT: u32 = 32 * 1024 * 1024;
const MAX_CONNECTIONS: usize = 8;
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(2);
const REPLY_TIMEOUT: Duration = Duration::from_secs(120);

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Endpoint {
    version: u32,
    address: SocketAddrV4,
    token: [u8; 16],
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Request {
    token: [u8; 16],
    command: Operation,
}

#[derive(Deserialize, Serialize)]
enum Reply {
    Report(Box<Report>),
    Error(String),
}

pub(crate) struct Admin {
    task: JoinHandle<()>,
    owner: L3CacheManager,
}

impl Drop for Admin {
    fn drop(&mut self) {
        self.task.abort();
        // The manager still holds the exclusive root lock.
        let _ = fs::remove_file(self.owner.root().join(ENDPOINT_FILE));
    }
}

pub(crate) async fn start(manager: Option<&L3CacheManager>) -> Result<Option<Admin>> {
    let Some(owner) = manager else {
        return Ok(None);
    };
    let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0))
        .await
        .context("bind the local disk KV cache administration listener")?;
    let std::net::SocketAddr::V4(address) = listener.local_addr()? else {
        unreachable!("bound an IPv4 loopback address");
    };
    let token = *uuid::Uuid::new_v4().as_bytes();
    publish(
        owner.root(),
        &Endpoint {
            version: ENDPOINT_VERSION,
            address,
            token,
        },
    )?;
    let task = tokio::spawn(accept(listener, owner.clone(), token));
    Ok(Some(Admin {
        task,
        owner: owner.clone(),
    }))
}

fn publish(root: &Path, endpoint: &Endpoint) -> Result<()> {
    let destination = root.join(ENDPOINT_FILE);
    fsinfo::refuse_symlink(&destination)?;
    let temporary = root.join(format!(".cache-admin-{}.tmp", uuid::Uuid::new_v4()));
    let mut open = OpenOptions::new();
    open.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        open.mode(0o600);
    }
    let result = (|| -> Result<()> {
        let mut file = open.open(&temporary)?;
        fsinfo::restrict_to_owner(&temporary, 0o600)?;
        serde_json::to_writer(&mut file, endpoint)?;
        file.flush()?;
        drop(file);
        fsinfo::replace_file(&temporary, &destination)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result.context("publish the local disk KV cache administration endpoint")
}

fn discover(root: &Path) -> Result<Option<Endpoint>> {
    fsinfo::refuse_symlink(root)?;
    if !root.try_exists()? {
        return Ok(None);
    }
    // Apply the cache's directory traversal rules before reading an endpoint.
    fsinfo::create_dir_all_without_links(root)?;
    let path = root.join(ENDPOINT_FILE);
    fsinfo::refuse_symlink(&path)?;
    let file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    require_private_file(&file)?;
    let mut bytes = Vec::new();
    file.take(u64::from(REQUEST_LIMIT) + 1)
        .read_to_end(&mut bytes)?;
    ensure!(
        bytes.len() <= REQUEST_LIMIT as usize,
        "cache owner endpoint metadata is too large"
    );
    let endpoint: Endpoint = serde_json::from_slice(&bytes)?;
    ensure!(
        endpoint.version == ENDPOINT_VERSION,
        "disk KV cache owner uses an incompatible administration protocol"
    );
    ensure!(
        endpoint.address.ip().is_loopback() && endpoint.address.port() != 0,
        "cache owner endpoint is not a loopback listener"
    );
    Ok(Some(endpoint))
}

fn require_private_file(file: &File) -> Result<()> {
    let metadata = file.metadata()?;
    ensure!(
        metadata.is_file(),
        "cache owner endpoint must be a regular file"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        ensure!(
            metadata.permissions().mode() & 0o077 == 0,
            "cache owner endpoint permissions are not private"
        );
    }
    Ok(())
}

pub(super) async fn forward(root: &Path, command: &Operation) -> Result<Option<Report>> {
    let Some(endpoint) = discover(root).context("locate the disk KV cache owner")? else {
        return Ok(None);
    };
    let mut stream = match timeout(HANDSHAKE_TIMEOUT, TcpStream::connect(endpoint.address)).await? {
        Ok(stream) => stream,
        // An owner interrupted before cleanup can leave a stale endpoint.
        // Offline execution still has to acquire the store's exclusive lock.
        Err(error) if error.kind() == io::ErrorKind::ConnectionRefused => return Ok(None),
        Err(error) => return Err(error).context("connect to the disk KV cache owner"),
    };
    let request = Request {
        token: endpoint.token,
        command: command.clone(),
    };
    timeout(
        HANDSHAKE_TIMEOUT,
        write_frame(&mut stream, &request, REQUEST_LIMIT),
    )
    .await??;
    let response: Reply = timeout(REPLY_TIMEOUT, read_frame(&mut stream, REPLY_LIMIT))
        .await
        .context(
            "cache owner response timed out; check cache status before retrying maintenance",
        )??;
    match response {
        Reply::Report(report) => {
            ensure!(
                report.schema_version == Report::VERSION,
                "incompatible cache report"
            );
            Ok(Some(*report))
        }
        Reply::Error(error) => anyhow::bail!("{error}"),
    }
}

async fn accept(listener: TcpListener, owner: L3CacheManager, token: [u8; 16]) {
    let mut connections = JoinSet::new();
    loop {
        tokio::select! {
            connection = listener.accept(), if connections.len() < MAX_CONNECTIONS => {
                let Ok((stream, peer)) = connection else { return };
                if peer.ip().is_loopback() {
                    let manager = owner.clone();
                    connections.spawn(async move {
                        let _ = handle(stream, manager, token).await;
                    });
                }
            }
            _ = connections.join_next(), if !connections.is_empty() => {}
        }
    }
}

async fn handle(mut stream: TcpStream, manager: L3CacheManager, token: [u8; 16]) -> Result<()> {
    let request: Request =
        timeout(HANDSHAKE_TIMEOUT, read_frame(&mut stream, REQUEST_LIMIT)).await??;
    ensure!(request.token == token, "cache owner authorization failed");
    let result = tokio::task::spawn_blocking(move || request.command.execute(&manager)).await?;
    let reply = match result {
        Ok(report) => Reply::Report(Box::new(report)),
        Err(error) => Reply::Error(format!("{error:#}")),
    };
    timeout(
        HANDSHAKE_TIMEOUT,
        write_frame(&mut stream, &reply, REPLY_LIMIT),
    )
    .await??;
    Ok(())
}

async fn read_frame<T: DeserializeOwned>(stream: &mut TcpStream, limit: u32) -> Result<T> {
    let size = stream.read_u32().await?;
    ensure!(
        size <= limit,
        "cache administration message exceeds the size limit"
    );
    let mut bytes = vec![0; size as usize];
    stream.read_exact(&mut bytes).await?;
    Ok(serde_json::from_slice(&bytes)?)
}

async fn write_frame<T: Serialize>(stream: &mut TcpStream, value: &T, limit: u32) -> Result<()> {
    let bytes = serde_json::to_vec(value)?;
    let size = u32::try_from(bytes.len())?;
    ensure!(
        size <= limit,
        "cache administration message exceeds the size limit"
    );
    stream.write_u32(size).await?;
    stream.write_all(&bytes).await?;
    Ok(())
}
