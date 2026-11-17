//! Native JY workload; never invoked inside the Actions orchestration container.
use super::*;
use crate::execution;
use serde_json::{Value, json};
use std::{
    fs,
    io::{Read, Write},
    net::TcpListener,
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

struct Server(Child);
#[cfg(unix)]
static SERVER_PID: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(0);
#[cfg(unix)]
extern "C" fn terminate_server_group(signal: libc::c_int) {
    let pid = SERVER_PID.load(std::sync::atomic::Ordering::Relaxed);
    unsafe {
        if pid > 0 {
            libc::kill(-pid, libc::SIGKILL);
        }
        libc::_exit(128 + signal);
    }
}
impl Drop for Server {
    fn drop(&mut self) {
        // All descendants share this process group, including on error paths.
        #[cfg(unix)]
        unsafe {
            libc::kill(-(self.0.id() as i32), libc::SIGTERM);
        }
        let until = Instant::now() + Duration::from_secs(20);
        while Instant::now() < until {
            if self.0.try_wait().ok().flatten().is_some() {
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        #[cfg(unix)]
        unsafe {
            libc::kill(-(self.0.id() as i32), libc::SIGKILL);
        }
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
fn spawn(args: &[String], log: &Path) -> DynResult<Server> {
    let file = fs::File::create(log)?;
    let mut command = Command::new("target/debug/skippy");
    command
        .args(args)
        .stdout(file.try_clone()?)
        .stderr(file)
        .stdin(Stdio::null());
    for (key, _) in std::env::vars() {
        if key.contains("TOKEN")
            || key.contains("SECRET")
            || key.contains("PASSWORD")
            || key == "JIANYANG_API_KEY"
        {
            command.env_remove(key);
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    let child = command.spawn()?;
    #[cfg(unix)]
    unsafe {
        SERVER_PID.store(child.id() as i32, std::sync::atomic::Ordering::Relaxed);
        libc::signal(
            libc::SIGTERM,
            terminate_server_group as *const () as libc::sighandler_t,
        );
        libc::signal(
            libc::SIGINT,
            terminate_server_group as *const () as libc::sighandler_t,
        );
    }
    Ok(Server(child))
}
fn get(url: &str) -> DynResult<String> {
    execution::capture(
        "curl",
        &["-fsS".into(), "--max-time".into(), "5".into(), url.into()],
        &[],
    )
}
fn ready(server: &mut Server, url: &str) -> DynResult<()> {
    let deadline = Instant::now() + Duration::from_secs(900);
    while Instant::now() < deadline {
        if server.0.try_wait()?.is_some() {
            return Err("inference server exited before readiness; inspect server.log".into());
        }
        if get(url).is_ok() {
            return Ok(());
        }
        thread::sleep(Duration::from_secs(2));
    }
    Err("server readiness timed out".into())
}
fn score(base: &str, model: &str, out: &Path) -> DynResult<()> {
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("../../../../ci/inference-quality.json"))?;
    let mut evidence = Vec::new();
    for case in cases {
        let payload = json!({"model":model,"messages":[{"role":"user","content":case["prompt"]}],"temperature":0,"seed":42,"max_tokens":256,"chat_template_kwargs":{"enable_thinking":false}});
        let data = tempfile::NamedTempFile::new()?;
        write_json_file(data.path(), &payload)?;
        let args = vec![
            "-fsS".into(),
            "--max-time".into(),
            "300".into(),
            "-H".into(),
            "Content-Type: application/json".into(),
            "--data-binary".into(),
            format!("@{}", data.path().display()),
            format!("{base}/v1/chat/completions"),
        ];
        let first: Value = serde_json::from_str(&execution::capture_for("curl", &args, &[], 310)?)?;
        let second: Value =
            serde_json::from_str(&execution::capture_for("curl", &args, &[], 310)?)?;
        let text = first["choices"][0]["message"]["content"]
            .as_str()
            .ok_or("missing response text")?;
        let passed = text.trim() == case["expected"].as_str().ok_or("missing expected answer")?
            && first["choices"][0]["message"]["content"]
                == second["choices"][0]["message"]["content"]
            && first["usage"]["completion_tokens"]
                .as_u64()
                .is_some_and(|n| n > 0);
        evidence.push(json!({"case":case,"response":first,"repeat":second,"passed":passed}));
        write_json_file(out, &evidence)?;
        if !passed {
            return Err("scored quality or deterministic repeat check failed".into());
        }
    }
    Ok(())
}

pub fn run(profile: &str, key: &str, mode: &str) -> DynResult<()> {
    let sha = std::env::var("JIANYANG_COMMIT")?;
    let ladder: Ladder =
        serde_json::from_str(include_str!("../../../../ci/inference-ladder.json"))?;
    let plan = plan(&ladder, profile, &sha)?;
    if !plan.rows.iter().any(|r| r.id == format!("{key}-{mode}")) {
        return Err("configuration not admitted by ladder".into());
    }
    if !cfg!(target_os = "linux") || !cfg!(target_arch = "aarch64") {
        return Err("DGX Spark Linux aarch64 required".into());
    }
    let gpu = execution::capture(
        "nvidia-smi",
        &["--query-gpu=name".into(), "--format=csv,noheader".into()],
        &[],
    )?;
    if !gpu.contains("GB10") {
        return Err("NVIDIA GB10 required".into());
    }
    let out = std::path::PathBuf::from(std::env::var("JIANYANG_ARTIFACT_DIR")?);
    fs::create_dir_all(&out)?;
    let fixture = super::artifacts::load(key)?;
    super::numerical::capacity(fixture.files.iter().map(|f| f.size).sum())?;
    let model = super::artifacts::admit(key)?;
    let coordinate = format!("{}@{}", fixture.repo, fixture.revision);
    let context = if profile == "pr" { 32768 } else { 65536 };
    let worker = std::env::var("JIANYANG_ROLE").as_deref() == Ok("worker");
    if !worker {
        super::numerical::run(&model, &coordinate, fixture.layers, &out)?;
    }
    let mut args = vec![
        "serve".into(),
        "--speculative-strategy".into(),
        "disabled".into(),
        "--ctx-size".into(),
        context.to_string(),
        "--n-gpu-layers".into(),
        "999".into(),
        "--bind-addr".into(),
        "0.0.0.0:9337".into(),
    ];
    let control;
    if mode == "split" {
        let peers: Vec<Value> = serde_json::from_str(&std::env::var("JIANYANG_PEERS")?)?;
        if peers.len() != 2 || peers[0]["node"] == peers[1]["node"] {
            return Err("split requires two distinct reserved machines".into());
        }
        let host = |role: &str| -> DynResult<String> {
            Ok(peers
                .iter()
                .find(|p| p["role"] == role)
                .and_then(|p| p["host"].as_str())
                .ok_or("formation peer missing")?
                .parse::<std::net::IpAddr>()?
                .to_string())
        };
        let leader = host("leader")?;
        let follower = host("worker")?;
        control = format!("http://{leader}:9912");
        let temp = tempfile::tempdir()?;
        let plan_dir = temp.path().join("plan");
        execution::run(
            "target/debug/skippy",
            &vec![
                "plan-split".into(),
                "--model-path".into(),
                model.display().to_string(),
                "--model-id".into(),
                coordinate.clone(),
                "--worker".into(),
                format!("{leader}:9400"),
                "--worker".into(),
                format!("{follower}:9401"),
                "--ctx-size".into(),
                context.to_string(),
                "--n-gpu-layers".into(),
                "999".into(),
                "--output-dir".into(),
                plan_dir.display().to_string(),
            ],
            &[],
            900,
        )?;
        let local = out.join("stage.json");
        fs::copy(
            plan_dir.join(if worker {
                "stage-1.json"
            } else {
                "stage-0.json"
            }),
            &local,
        )?;
        args.extend([
            "--config".into(),
            local.display().to_string(),
            "--stage-transport".into(),
            "binary".into(),
        ]);
        if worker {
            args.push("--worker-only".into());
        }
    } else {
        control = String::new();
        args.extend([
            "--model-path".into(),
            model.display().to_string(),
            "--model-id".into(),
            coordinate.clone(),
        ]);
    }
    let mut server = spawn(&args, &out.join("server.log"))?;
    if worker {
        let deadline = Instant::now() + Duration::from_secs(18000);
        while Instant::now() < deadline {
            if server.0.try_wait()?.is_some() {
                return Err("split worker server exited".into());
            }
            if get(&control).is_ok_and(|s| s == "qualification-complete") {
                return Ok(());
            }
            thread::sleep(Duration::from_secs(5));
        }
        return Err("split leader completion timed out".into());
    }
    ready(&mut server, "http://127.0.0.1:9337/v1/models")?;
    score(
        "http://127.0.0.1:9337",
        &coordinate,
        &out.join("quality.json"),
    )?;
    super::performance::run(profile, &coordinate, context, &out, &fixture)?;
    write_json_file(
        &out.join("qualification.json"),
        &json!({"source_sha":sha,"model":key,"mode":mode,"profile":profile,"status":"passed","performance_claim":plan.performance_claim,"gpu":gpu,"artifact_manifest_sha256":crate::artifact::sha256(Path::new("ci/inference-artifacts.json"))?,"layers":fixture.layers,"correctness_scope":"native chain parity, state handoff, deterministic endpoint repeat; not an independent implementation reference"}),
    )?;
    if mode == "split" {
        let listener = TcpListener::bind("0.0.0.0:9912")?;
        listener.set_nonblocking(true)?;
        let until = Instant::now() + Duration::from_secs(60);
        while Instant::now() < until {
            if let Ok((mut stream, _)) = listener.accept() {
                stream.set_read_timeout(Some(Duration::from_secs(2)))?;
                let mut buf = [0; 1024];
                let _ = stream.read(&mut buf);
                stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 22\r\nConnection: close\r\n\r\nqualification-complete")?;
                break;
            }
            thread::sleep(Duration::from_millis(100));
        }
    }
    Ok(())
}
