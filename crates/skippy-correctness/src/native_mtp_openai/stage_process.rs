use std::{
    fs::{self, File},
    net::SocketAddr,
    path::Path,
    process::Stdio,
    thread,
    time::{Duration, Instant},
};

use anyhow::{Context, Result, bail};
use reqwest::blocking::Client;
use serde_json::Value;

use crate::{
    cli::NativeMtpOpenAiAbArgs,
    support::{ChildGuard, connect_ready, connect_ready_child},
};

use super::{
    MtpCaseMode,
    remote::{RemoteStageGuard, spawn_remote_stage1},
};

pub(super) struct Stage1LaunchReport {
    pub(super) launch_mode: &'static str,
    pub(super) remote_config: Option<String>,
    pub(super) remote_topology: Option<String>,
    pub(super) remote_log: Option<String>,
}

pub(super) enum StageHandle {
    Local(ChildGuard),
    Remote(RemoteStageGuard),
    External,
}

impl StageHandle {
    pub(super) fn stop_and_collect(self, stage1_log: &Path) -> Result<()> {
        match self {
            StageHandle::Local(guard) => {
                drop(guard);
                Ok(())
            }
            StageHandle::External => Ok(()),
            StageHandle::Remote(mut guard) => guard.stop_and_collect(stage1_log),
        }
    }
}

pub(super) fn start_stage1(
    args: &NativeMtpOpenAiAbArgs,
    run_id: &str,
    config_path: &Path,
    topology_path: &Path,
    log_path: &Path,
    mode: MtpCaseMode,
) -> Result<(StageHandle, Stage1LaunchReport)> {
    if args.external_stage1 {
        fs::write(log_path, "stage 1 was externally managed by the caller\n")
            .with_context(|| format!("failed to create {}", log_path.display()))?;
        return Ok((
            StageHandle::External,
            Stage1LaunchReport {
                launch_mode: "external",
                remote_config: None,
                remote_topology: None,
                remote_log: None,
            },
        ));
    }

    if let Some(host) = args.stage1_ssh_host.as_deref() {
        let (remote, report) =
            spawn_remote_stage1(args, host, run_id, config_path, topology_path, mode)?;
        return Ok((StageHandle::Remote(remote), report));
    }

    let local = spawn_stage(args, config_path, None, topology_path, log_path, mode)?;
    Ok((
        StageHandle::Local(local),
        Stage1LaunchReport {
            launch_mode: "local",
            remote_config: None,
            remote_topology: None,
            remote_log: None,
        },
    ))
}

pub(super) fn spawn_stage(
    args: &NativeMtpOpenAiAbArgs,
    config_path: &Path,
    openai_bind_addr: Option<SocketAddr>,
    topology_path: &Path,
    log_path: &Path,
    mode: MtpCaseMode,
) -> Result<ChildGuard> {
    let log = File::create(log_path)
        .with_context(|| format!("failed to create {}", log_path.display()))?;
    let mut command = crate::stage_process::command(
        &args.server.stage_server_bin,
        args.server.startup_timeout_secs,
    );
    command.args([
        "--stage-transport",
        "binary",
        "--config",
        config_path
            .to_str()
            .context("stage config path is not valid UTF-8")?,
        "--topology",
        topology_path
            .to_str()
            .context("topology path is not valid UTF-8")?,
        "--telemetry-level",
        "debug",
    ]);
    if let Some(openai_bind_addr) = openai_bind_addr {
        command.args(["--bind-addr", &openai_bind_addr.to_string()]);
    } else {
        command.arg("--worker-only");
    }
    command.env("SKIPPY_TELEMETRY_STDERR", "1");
    command.args(if openai_bind_addr.is_some() {
        mode.frontend_cli_args()
    } else {
        mode.worker_cli_args()
    });
    command.stdout(Stdio::from(log.try_clone()?));
    command.stderr(Stdio::from(log));
    ChildGuard::spawn(command)
}

pub(super) fn wait_stage1_ready(
    stage1: &mut StageHandle,
    addr: SocketAddr,
    timeout_secs: u64,
) -> Result<()> {
    match stage1 {
        StageHandle::Local(child) => {
            drop(connect_ready_child(addr, timeout_secs, child)?);
            Ok(())
        }
        StageHandle::External => {
            drop(connect_ready(addr, timeout_secs)?);
            Ok(())
        }
        StageHandle::Remote(guard) => guard.wait_ready(timeout_secs),
    }
}

pub(super) fn wait_openai_ready(
    client: &Client,
    addr: SocketAddr,
    timeout_secs: u64,
    child: &mut ChildGuard,
    log_path: &Path,
) -> Result<()> {
    let started = Instant::now();
    let timeout = Duration::from_secs(timeout_secs.max(1));
    let url = format!("http://{addr}/v1/models");
    let mut last_error = None;
    while started.elapsed() < timeout {
        if let Some(status) = child.try_wait()? {
            let bytes = fs::read(log_path)
                .unwrap_or_else(|error| format!("could not read child log: {error}").into_bytes());
            let tail = String::from_utf8_lossy(&bytes[bytes.len().saturating_sub(8192)..]);
            bail!(
                "stage 0 exited before OpenAI readiness with {status}; {}:\n{tail}",
                log_path.display()
            );
        }
        let remaining = timeout.saturating_sub(started.elapsed());
        match client
            .get(&url)
            .timeout(remaining.min(Duration::from_secs(1)))
            .send()
        {
            Ok(response) if response.status().is_success() => return Ok(()),
            Ok(response) => last_error = Some(format!("HTTP {}", response.status())),
            Err(error) => last_error = Some(error.to_string()),
        }
        thread::sleep(Duration::from_millis(250).min(timeout.saturating_sub(started.elapsed())));
    }
    bail!(
        "timed out waiting for {url}; see {}: {}",
        log_path.display(),
        last_error.unwrap_or_else(|| "no attempts made".to_string())
    );
}

pub(super) fn case_addr(addr: SocketAddr, port_offset: u16, case_index: u16) -> Result<SocketAddr> {
    let offset = port_offset
        .checked_mul(case_index)
        .context("case port offset exceeds u16")?;
    offset_port(addr, offset)
}

fn offset_port(mut addr: SocketAddr, offset: u16) -> Result<SocketAddr> {
    let port = addr
        .port()
        .checked_add(offset)
        .context("batched port offset exceeds u16")?;
    addr.set_port(port);
    Ok(addr)
}

pub(super) fn write_model_stage_config(
    path: &Path,
    value: &Value,
    source: &Path,
    source_digest: &str,
) -> Result<()> {
    let mut value = value.clone();
    value["source_model_path"] = serde_json::json!(source);
    value["source_model_sha256"] = serde_json::json!(source_digest);
    write_stage_config(path, &value)
}

pub(super) fn write_stage_config(path: &Path, value: &Value) -> Result<()> {
    fs::write(path, serde_json::to_vec_pretty(value)?)
        .with_context(|| format!("failed to write {}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn model_configs_bind_tokenizer_to_canonical_source_not_stage_slice() -> Result<()> {
        let root =
            std::env::temp_dir().join(format!("mtp-config-{}", crate::support::generate_run_id()));
        fs::create_dir_all(&root)?;
        let source = root.join("source.gguf");
        fs::write(&source, b"abc")?;
        let digest = crate::runner::source_model_sha256(&source)?;
        assert_eq!(
            digest,
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        for index in 0..2 {
            let path = root.join(format!("stage{index}.json"));
            let value = serde_json::json!({
                "run_id": "test", "topology_id": "test", "model_id": "test/model",
                "model_path": "stage-slice.gguf", "stage_id": format!("stage-{index}"),
                "stage_index": index, "layer_start": index, "layer_end": index + 1,
                "ctx_size": 128, "bind_addr": "127.0.0.1:9000", "execution_contract": "ab".repeat(32), "load_mode": "runtime-slice"
            });
            write_model_stage_config(&path, &value, &source, &digest)?;
            let config: skippy_protocol::StageConfig = serde_json::from_slice(&fs::read(path)?)?;
            assert_eq!(config.source_model_sha256.as_deref(), Some(digest.as_str()));
            assert_eq!(config.source_model_path.as_deref(), source.to_str());
            assert_eq!(config.model_path.as_deref(), Some("stage-slice.gguf"));
        }
        fs::remove_dir_all(root)?;
        Ok(())
    }

    #[test]
    fn openai_readiness_reports_child_exit_and_log_before_deadline() -> Result<()> {
        let root =
            std::env::temp_dir().join(format!("mtp-exit-{}", crate::support::generate_run_id()));
        fs::create_dir_all(&root)?;
        let log = root.join("stage0.log");
        fs::write(&log, "tokenizer identity unavailable")?;
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let addr = listener.local_addr()?;
        drop(listener);
        let mut command = std::process::Command::new(std::env::current_exe()?);
        command
            .arg("--list")
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        let mut child = ChildGuard::spawn(command)?;
        let started = Instant::now();
        let client = Client::builder()
            .tls_certs_only([])
            .build()
            .expect("build plain-HTTP test client");
        let error = wait_openai_ready(&client, addr, 5, &mut child, &log).unwrap_err();
        assert!(started.elapsed() < Duration::from_secs(5));
        let message = error.to_string();
        assert!(
            message.contains("exited before OpenAI readiness"),
            "{message}"
        );
        assert!(
            message.contains("tokenizer identity unavailable"),
            "{message}"
        );
        assert!(message.contains("stage0.log"), "{message}");
        fs::remove_dir_all(root)?;
        Ok(())
    }

    #[test]
    fn case_addr_applies_per_case_port_offset() {
        let addr: SocketAddr = "127.0.0.1:9000".parse().unwrap();

        let shifted = case_addr(addr, 10, 2).unwrap();

        assert_eq!(shifted.to_string(), "127.0.0.1:9020");
    }

    #[test]
    fn case_addr_rejects_port_overflow() {
        let addr: SocketAddr = "127.0.0.1:65530".parse().unwrap();

        let error = case_addr(addr, 10, 1).unwrap_err();

        assert!(
            error
                .to_string()
                .contains("batched port offset exceeds u16")
        );
    }
}
