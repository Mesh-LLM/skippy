use super::{
    artifacts, execution,
    matrix::{Host, Matrix, require_host},
    producers::validate_native_identity,
};
use crate::{command::DynResult, huggingface};
use process_supervision::{
    Cancellation, ObservedLine, OutputFiles, Probe, ProbeContext, ProbeDecision, ReadinessProbe,
};
use std::{
    fs,
    net::TcpListener,
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    time::{Duration, Instant},
};

struct InferenceProbe {
    result: mpsc::Receiver<Result<serde_json::Value, String>>,
    response: Option<serde_json::Value>,
    gpu_required: bool,
    gpu_selected: bool,
}
impl ReadinessProbe for InferenceProbe {
    type Rejection = String;
    fn line(&mut self, line: ObservedLine<'_>) -> ProbeDecision<String> {
        if let Ok(event) = serde_json::from_slice::<serde_json::Value>(line.bytes)
            && event["type"] == "backend_device_selected"
            && event["data"]["detail"]
                .as_str()
                .is_some_and(|device| device.starts_with("CUDA") || device.starts_with("MTL"))
        {
            self.gpu_selected = true;
        }
        ProbeDecision::Pending
    }
    fn tick(&mut self, _: ProbeContext) -> ProbeDecision<String> {
        match self.result.try_recv() {
            Ok(Ok(value)) => self.response = Some(value),
            Ok(Err(error)) => return ProbeDecision::Rejected(error),
            Err(mpsc::TryRecvError::Disconnected) if self.response.is_none() => {
                return ProbeDecision::Rejected("inference observer disconnected".into());
            }
            Err(_) => {}
        }
        if self.response.is_some() && (!self.gpu_required || self.gpu_selected) {
            ProbeDecision::Candidate
        } else {
            ProbeDecision::Pending
        }
    }
}

pub fn run(id: &str, input: &Path) -> DynResult<()> {
    let matrix = Matrix::load()?;
    let row = matrix.runtime(id)?;
    let host = matrix.host(&row.host)?;
    let temp = tempfile::tempdir()?;
    let product = temp.path().join("product");
    verify_product(input, &product, host, id)?;
    let model = std::env::var("SKIPPY_CI_MODEL")
        .map_err(|_| "SKIPPY_CI_MODEL must point at the pinned Qwen3 dense fixture")?;
    SmokeFixture::load()?.verify(Path::new(&model))?;
    let binary = artifacts::binary(&product, &host.os);
    let port = TcpListener::bind("127.0.0.1:0")?.local_addr()?.port();
    let endpoint = format!("http://127.0.0.1:{port}/v1");
    let args = execution::strings(&[
        "--runtime-bundle",
        &product.join("native-runtimes").to_string_lossy(),
        "--runtime-selection",
        &row.backend,
        "--runtime-cache",
        &temp.path().join("cache").to_string_lossy(),
        "serve",
        "--model-path",
        &model,
        "--model-id",
        "qwen3-ci",
        "--bind-addr",
        &format!("127.0.0.1:{port}"),
        "--ctx-size",
        "2048",
        "--n-gpu-layers",
        if row.backend == "cpu" { "0" } else { "999" },
    ]);
    let environment = [
        ("HF_HUB_OFFLINE", "1".into()),
        ("HOME", temp.path().to_string_lossy().into_owned()),
        ("USERPROFILE", temp.path().to_string_lossy().into_owned()),
    ];
    let output = input.join("evidence");
    fs::create_dir_all(&output)?;
    let response = supervise(
        &execution::specification(&binary.to_string_lossy(), &args, &environment)?,
        &endpoint,
        row.backend != "cpu",
        &output,
    )?;
    artifacts::write_json(
        &output.join("smoke.json"),
        &serde_json::json!({
            "source": artifacts::source()?, "variant": id, "backend": row.backend, "gpu_offload_required": row.backend != "cpu",
            "model_sha256": artifacts::digest(Path::new(&model))?, "response": response
        }),
    )?;
    Ok(())
}

pub fn artifact(id: &str, input: &Path) -> DynResult<()> {
    let matrix = Matrix::load()?;
    let row = matrix.runtime(id)?;
    let host = matrix.host(&row.host)?;
    let temp = tempfile::tempdir()?;
    let product = temp.path().join("product");
    verify_product(input, &product, host, id)?;
    let runtime = product.join("native-runtimes").join(id);
    let architectures = (row.backend == "cuda").then_some(row.gpu_architectures.as_str());
    validate_native_identity(&runtime, host, row, architectures)?;
    crate::native::run(&execution::strings(&[
        "verify-runtime-package",
        &runtime.to_string_lossy(),
    ]))?;
    artifacts::write_json(
        &input.join("evidence/artifact-smoke.json"),
        &serde_json::json!({
            "source": artifacts::source()?,
            "variant": id,
            "backend": row.backend,
        }),
    )
}

fn verify_product(input: &Path, product: &Path, host: &Host, id: &str) -> DynResult<()> {
    require_host(host)?;
    artifacts::unpack(input, product)?;
    artifacts::verify(product, "product", &host.target, id)?;
    let binary = artifacts::binary(product, &host.os);
    let mut spec = execution::specification(
        &binary.to_string_lossy(),
        &execution::strings(&["--version"]),
        &[],
    )?;
    for key in [
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "DYLD_LIBRARY_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
    ] {
        spec.environment.remove(std::ffi::OsStr::new(key));
    }
    spec.cwd = product.canonicalize()?;
    let report = process_supervision::supervise_inherited(
        &spec,
        &execution::limits(60),
        &Cancellation::default(),
    )?;
    if report.outcome != process_supervision::Outcome::Exited
        || !report.status.is_some_and(|status| status.success())
        || !report.cleanup.complete
    {
        return Err(format!(
            "packaged CLI startup failed: {:?}, {:?}",
            report.outcome, report.status
        )
        .into());
    }
    Ok(())
}

fn supervise(
    spec: &process_supervision::ProcessSpec,
    endpoint: &str,
    gpu_required: bool,
    output: &Path,
) -> DynResult<serde_json::Value> {
    let stop = Arc::new(AtomicBool::new(false));
    let (sender, receiver) = mpsc::channel();
    std::thread::scope(|scope| {
        let stop_worker = stop.clone();
        scope.spawn(move || {
            let _ = sender
                .send(request_inference(endpoint, &stop_worker).map_err(|error| error.to_string()));
        });
        let mut probe = InferenceProbe {
            result: receiver,
            response: None,
            gpu_required,
            gpu_selected: false,
        };
        let result = process_supervision::supervise_with_probe(
            spec,
            &execution::limits(900),
            &Cancellation::default(),
            OutputFiles {
                stdout: Some(output.join("stdout.log")),
                stderr: Some(output.join("stderr.log")),
            },
            Probe {
                observer: &mut probe,
                deadline: Duration::from_secs(850),
            },
        );
        stop.store(true, Ordering::Relaxed);
        let report = result?;
        if !report.process.success() || report.process.cleanup.forced || report.rejection.is_some()
        {
            return Err(format!(
                "packaged inference failed: {:?}, {:?}, forced shutdown: {}",
                report.process.outcome, report.rejection, report.process.cleanup.forced
            )
            .into());
        }
        probe
            .response
            .ok_or_else(|| "missing successful inference response".into())
    })
}

fn request_inference(endpoint: &str, stop: &AtomicBool) -> DynResult<serde_json::Value> {
    let deadline = Instant::now() + Duration::from_secs(600);
    loop {
        if stop.load(Ordering::Relaxed) || Instant::now() >= deadline {
            return Err("server readiness deadline or cancellation".into());
        }
        let ready = execution::capture(
            "curl",
            &execution::strings(&[
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                "2",
                &format!("{endpoint}/models"),
            ]),
            &[],
        );
        if ready.is_ok() {
            break;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    let request = serde_json::json!({"model":"qwen3-ci","messages":[{"role":"user","content":"Say hello."}],"max_tokens":4,"temperature":0}).to_string();
    let body = execution::capture(
        "curl",
        &execution::strings(&[
            "--fail",
            "--silent",
            "--show-error",
            "--max-time",
            "120",
            "--header",
            "content-type: application/json",
            "--data-binary",
            &request,
            &format!("{endpoint}/chat/completions"),
        ]),
        &[],
    )?;
    let response: serde_json::Value = serde_json::from_str(&body)?;
    if response["object"] != "chat.completion"
        || response["choices"][0]["message"]["role"] != "assistant"
        || response["usage"]["completion_tokens"].as_u64().unwrap_or(0) == 0
    {
        return Err("invalid or empty OpenAI completion".into());
    }
    Ok(response)
}

struct SmokeFixture {
    repo: String,
    revision: String,
    filename: String,
    size: u64,
    sha256: String,
}

impl SmokeFixture {
    fn load() -> DynResult<Self> {
        let manifest: serde_json::Value =
            serde_json::from_slice(&fs::read("ci/llama-canary/family-certified.json")?)?;
        let artifact = &manifest["models"]
            .as_array()
            .ok_or("missing family roster")?
            .iter()
            .find(|row| row["family"] == "qwen3-dense")
            .ok_or("missing Qwen3 dense fixture")?["artifact"];
        let filename = artifact["files"][0]
            .as_str()
            .ok_or("missing fixture filename")?;
        let identity = &artifact["file_integrity"][filename];
        Ok(Self {
            repo: artifact["repo"]
                .as_str()
                .ok_or("missing fixture repo")?
                .into(),
            revision: artifact["revision"]
                .as_str()
                .ok_or("missing fixture revision")?
                .into(),
            filename: filename.into(),
            size: identity["size_bytes"]
                .as_u64()
                .ok_or("missing fixture size")?,
            sha256: identity["blob_id"]
                .as_str()
                .ok_or("missing fixture SHA-256")?
                .into(),
        })
    }

    fn verify(&self, path: &Path) -> DynResult<()> {
        if fs::metadata(path)?.len() != self.size || artifacts::digest(path)? != self.sha256 {
            return Err("Qwen3 fixture size or SHA-256 mismatch".into());
        }
        Ok(())
    }
}

pub fn prepare_model() -> DynResult<()> {
    use std::io::Write;
    let fixture = SmokeFixture::load()?;
    let destination = if let Some(path) = std::env::var_os("SKIPPY_CI_MODEL") {
        std::path::PathBuf::from(path)
    } else {
        huggingface::download(&fixture.repo, &fixture.revision, &[&fixture.filename], 960)?
    };
    fixture.verify(&destination)?;
    if let Some(path) = std::env::var_os("GITHUB_ENV") {
        writeln!(
            fs::OpenOptions::new().append(true).open(path)?,
            "SKIPPY_CI_MODEL={}",
            std::path::absolute(&destination)?.display()
        )?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn structured_gpu_device_event_admits_gpu_evidence() {
        let (_sender, receiver) = mpsc::channel();
        let mut probe = InferenceProbe {
            result: receiver,
            response: None,
            gpu_required: true,
            gpu_selected: false,
        };
        let line = br#"{"data":{"detail":"MTL0"},"type":"backend_device_selected"}"#;

        assert!(matches!(
            probe.line(ObservedLine {
                stream: process_supervision::Stream::Stdout,
                bytes: line,
                ending: process_supervision::LineEnding::Lf,
            }),
            ProbeDecision::Pending
        ));
        assert!(probe.gpu_selected);
    }
}
