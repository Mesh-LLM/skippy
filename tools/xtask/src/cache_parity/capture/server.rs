use super::{DynResult, Engine, memory};
use crate::{artifact, execution};
use process_supervision::{
    Cancellation, ObservedLine, OutputFiles, Probe, ProbeContext, ProbeDecision, ReadinessProbe,
};
use serde::Deserialize;
use std::{
    fs,
    net::{SocketAddr, TcpListener},
    path::{Path, PathBuf},
    sync::mpsc,
    time::{Duration, Instant},
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Server {
    pub(super) binary: PathBuf,
    pub(super) args: Vec<String>,
    pub(super) memory_sampling: Option<memory::Profile>,
    pub(super) metal_memory_probe: Option<PathBuf>,
}

impl Server {
    pub(super) fn probe_sha256(&self) -> DynResult<Option<String>> {
        match (self.memory_sampling, self.metal_memory_probe.as_deref()) {
            (Some(memory::Profile::Metal), Some(path)) if path.is_absolute() && path.is_file() => {
                Ok(Some(artifact::sha256(path)?))
            }
            (Some(memory::Profile::Metal), _) => {
                Err("Metal sampling requires an absolute path to a built probe".into())
            }
            (_, Some(_)) => Err("metal_memory_probe requires Metal sampling".into()),
            (_, None) => Ok(None),
        }
    }

    pub(super) fn with_ready(
        &self,
        engine: Engine,
        extra_args: &[String],
        output: &Path,
        cancellation: &Cancellation,
        work: impl FnOnce(SocketAddr, &memory::Monitor<'_>, &Cancellation) -> DynResult<()> + Send,
    ) -> DynResult<u32> {
        let address = TcpListener::bind("127.0.0.1:0")?.local_addr()?;
        let mut args = self.args.clone();
        args.extend_from_slice(extra_args);
        args.extend(bind(engine, address));
        let mut environment = vec![("HF_HUB_OFFLINE", "1".into())];
        let metal = self
            .metal_memory_probe
            .as_deref()
            .map(memory::metal::Probe::start)
            .transpose()?;
        if let Some(probe) = &metal {
            environment.extend(probe.environment()?);
        }
        let spec = execution::specification(
            self.binary.to_str().ok_or("non-UTF-8 engine path")?,
            &args,
            &environment,
        )?;
        fs::create_dir(output)?;
        let (sender, receiver) = mpsc::sync_channel(1);
        let (pid_sender, pid_receiver) = mpsc::sync_channel(1);
        let stop_worker = Cancellation::default();
        let metal = metal.as_ref();
        std::thread::scope(|scope| {
            let stop = &stop_worker;
            scope.spawn(move || {
                let result = (|| {
                    let pid = pid_receiver.recv()?;
                    wait_for_server(address, stop)?;
                    let memory = memory::Monitor {
                        profile: self.memory_sampling,
                        pid,
                        metal,
                    };
                    work(address, &memory, stop)
                })();
                let _ = sender.send(result.map_err(|error| error.to_string()));
            });
            let mut limits = execution::limits(870);
            if engine == Engine::Baseline && extra_args.iter().any(|arg| arg == "--verbosity") {
                limits.retained_bytes_per_stream = 16 * 1024 * 1024;
            }
            let report = process_supervision::supervise_with_probe(
                &spec,
                &limits,
                cancellation,
                OutputFiles {
                    stdout: Some(output.join("server.stdout.log")),
                    stderr: Some(output.join("server.stderr.log")),
                },
                Probe {
                    observer: &mut CompletionProbe {
                        result: receiver,
                        launch: Some(pid_sender),
                    },
                    deadline: Duration::from_secs(850),
                },
            );
            stop_worker.cancel();
            let report = report?;
            if !report.process.success() || report.rejection.is_some() {
                return Err(format!(
                    "capture ended with {:?}, request error {:?}, forced shutdown {}",
                    report.process.outcome, report.rejection, report.process.cleanup.forced
                )
                .into());
            }
            Ok(report.process.pid)
        })
    }
}

struct CompletionProbe {
    result: mpsc::Receiver<Result<(), String>>,
    launch: Option<mpsc::SyncSender<u32>>,
}

impl ReadinessProbe for CompletionProbe {
    type Rejection = String;

    fn line(&mut self, _: ObservedLine<'_>) -> ProbeDecision<String> {
        ProbeDecision::Pending
    }

    fn tick(&mut self, context: ProbeContext) -> ProbeDecision<String> {
        if let Some(launch) = self.launch.take() {
            let _ = launch.try_send(context.pid);
        }
        match self.result.try_recv() {
            Ok(Ok(())) => ProbeDecision::Candidate,
            Ok(Err(error)) => ProbeDecision::Rejected(error),
            Err(mpsc::TryRecvError::Empty) => ProbeDecision::Pending,
            Err(mpsc::TryRecvError::Disconnected) => {
                ProbeDecision::Rejected("capture thread disconnected".into())
            }
        }
    }
}

fn bind(engine: Engine, address: SocketAddr) -> Vec<String> {
    match engine {
        Engine::Candidate => vec!["--bind-addr".into(), address.to_string()],
        Engine::Baseline => vec![
            "--host".into(),
            address.ip().to_string(),
            "--port".into(),
            address.port().to_string(),
        ],
    }
}

fn wait_for_server(address: SocketAddr, stop: &Cancellation) -> DynResult<()> {
    let deadline = Instant::now() + Duration::from_secs(600);
    let args = execution::strings(&[
        "--noproxy",
        "*",
        "--silent",
        "--fail",
        "--max-time",
        "2",
        &format!("http://{address}/v1/models"),
    ]);
    while !stop.is_cancelled() && Instant::now() < deadline {
        if execution::capture_for("curl", &args, &[], 3).is_ok() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    Err("server did not become ready before its deadline or cancellation".into())
}
