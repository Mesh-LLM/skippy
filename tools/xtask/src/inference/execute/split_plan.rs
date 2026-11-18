use super::*;

pub(super) struct Request<'a> {
    pub model: &'a Path,
    pub coordinate: &'a str,
    pub profile: &'a str,
    pub context: u32,
    pub output: &'a Path,
    pub worker: bool,
}

pub(super) struct Prepared {
    pub args: Vec<String>,
    pub control: String,
    pub certification: String,
}

pub(super) fn prepare(request: Request<'_>) -> DynResult<Prepared> {
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
    let temp = tempfile::tempdir()?;
    let directory = temp.path().join("plan");
    execution::run(
        "target/debug/skippy",
        &planner_args(&request, &leader, &follower, &directory),
        &[],
        900,
    )?;
    let admissions = fs::read(directory.join("admissions.json"))?;
    fs::write(request.output.join("split-admissions.json"), &admissions)?;
    let certification = admission_status(&serde_json::from_slice(&admissions)?, request.profile)?;
    let local = request.output.join("stage.json");
    fs::copy(
        directory.join(if request.worker {
            "stage-1.json"
        } else {
            "stage-0.json"
        }),
        &local,
    )?;
    let mut args = vec![
        "--config".into(),
        local.display().to_string(),
        "--stage-transport".into(),
        "binary".into(),
    ];
    if request.worker {
        args.push("--worker-only".into());
    } else {
        wait_for_worker(&follower)?;
    }
    Ok(Prepared {
        args,
        control: format!("http://{leader}:9912"),
        certification,
    })
}

fn planner_args(
    request: &Request<'_>,
    leader: &str,
    follower: &str,
    directory: &Path,
) -> Vec<String> {
    let mut args = vec![
        "plan-split".into(),
        "--model-path".into(),
        request.model.display().to_string(),
        "--model-id".into(),
        request.coordinate.into(),
        "--worker".into(),
        format!("{leader}:9400"),
        "--worker".into(),
        format!("{follower}:9401"),
        "--ctx-size".into(),
        request.context.to_string(),
        "--n-gpu-layers".into(),
        "999".into(),
        "--output-dir".into(),
        directory.display().to_string(),
    ];
    if request.profile == "pr" {
        args.push("--allow-uncertified-split".into());
    }
    args
}

fn admission_status(admissions: &Value, profile: &str) -> DynResult<String> {
    match admissions["certification"].as_str() {
        Some("certified") => Ok("certified".into()),
        Some("uncertified_override") if profile == "pr" => Ok("uncertified_override".into()),
        _ => Err("split plan lacks the profile's required certification admission".into()),
    }
}

fn wait_for_worker(follower: &str) -> DynResult<()> {
    let endpoint = format!("{follower}:9401").parse()?;
    let until = Instant::now() + Duration::from_secs(900);
    while std::net::TcpStream::connect_timeout(&endpoint, Duration::from_secs(1)).is_err() {
        if Instant::now() > until {
            return Err("split worker listener did not become ready".into());
        }
        thread::sleep(Duration::from_secs(1));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_pr_smoke_explicitly_opts_into_experimental_split_planning() {
        for profile in ["pr", "nightly", "release"] {
            let request = Request {
                model: Path::new("/models/model.gguf"),
                coordinate: "model@revision",
                profile,
                context: 4096,
                output: Path::new("evidence"),
                worker: false,
            };
            let args = planner_args(&request, "10.0.0.1", "10.0.0.2", Path::new("plan"));
            assert_eq!(
                args.iter().any(|arg| arg == "--allow-uncertified-split"),
                profile == "pr"
            );
            assert!(
                args.windows(2)
                    .any(|pair| pair == ["--n-gpu-layers", "999"])
            );
        }
    }

    #[test]
    fn experimental_admission_is_retained_and_never_promoted_to_release_evidence() {
        let experimental = json!({"certification":"uncertified_override"});
        assert_eq!(
            admission_status(&experimental, "pr").unwrap(),
            "uncertified_override"
        );
        for profile in ["nightly", "release"] {
            assert!(admission_status(&experimental, profile).is_err());
            assert_eq!(
                admission_status(&json!({"certification":"certified"}), profile).unwrap(),
                "certified"
            );
        }
        assert!(admission_status(&json!({}), "pr").is_err());
        assert!(admission_status(&json!({"certification":"passed"}), "pr").is_err());
    }
}
