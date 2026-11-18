use super::{
    artifacts, execution,
    matrix::{Matrix, require_host},
};
use crate::command::DynResult;
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
    require_host(host)?;
    let temp = tempfile::tempdir()?;
    let product = temp.path().join("product");
    artifacts::unpack(input, &product)?;
    artifacts::verify(&product, "product", &host.target, id)?;
    let model = std::env::var("SKIPPY_CI_MODEL")
        .map_err(|_| "SKIPPY_CI_MODEL must point at the pinned Qwen3 dense fixture")?;
    verify_model(Path::new(&model))?;
    let binary = artifacts::binary(&product, &host.os);
    execution::run(
        &binary.to_string_lossy(),
        &execution::strings(&["--version"]),
        &[],
        60,
    )?;
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

fn verify_model(path: &Path) -> DynResult<()> {
    let manifest: serde_json::Value =
        serde_json::from_slice(&fs::read("ci/llama-canary/family-certified.json")?)?;
    let model = manifest["models"]
        .as_array()
        .ok_or("missing family roster")?
        .iter()
        .find(|row| row["family"] == "qwen3-dense")
        .ok_or("missing Qwen3 dense fixture")?;
    let filename = model["artifact"]["files"][0]
        .as_str()
        .ok_or("missing fixture filename")?;
    let identity = &model["artifact"]["file_integrity"][filename];
    if Some(fs::metadata(path)?.len()) != identity["size_bytes"].as_u64()
        || Some(artifacts::digest(path)?.as_str()) != identity["blob_id"].as_str()
    {
        return Err("Qwen3 fixture size or SHA-256 mismatch".into());
    }
    Ok(())
}

pub fn prepare_model() -> DynResult<()> {
    use std::io::Write;
    let destination = if let Some(path) = std::env::var_os("SKIPPY_CI_MODEL") {
        std::path::PathBuf::from(path)
    } else {
        let root = Path::new("target/ci-models");
        fs::create_dir_all(root)?;
        let destination = root.join("Qwen3-0.6B-Q8_0.gguf");
        if !destination.exists() {
            let input = tempfile::NamedTempFile::new_in(root)?;
            execution::run(
                "curl",
                &execution::strings(&[
                    "--fail",
                    "--location",
                    "--proto",
                    "=https",
                    "--max-time",
                    "900",
                    "--output",
                    &input.path().to_string_lossy(),
                    "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/23749fefcc72300e3a2ad315e1317431b06b590a/Qwen3-0.6B-Q8_0.gguf",
                ]),
                &[],
                960,
            )?;
            verify_model(input.path())?;
            input.persist(&destination)?;
        }
        destination
    };
    verify_model(&destination)?;
    if let Some(path) = std::env::var_os("GITHUB_ENV") {
        writeln!(
            fs::OpenOptions::new().append(true).open(path)?,
            "SKIPPY_CI_MODEL={}",
            destination.canonicalize()?.display()
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
