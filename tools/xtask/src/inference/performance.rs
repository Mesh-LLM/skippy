use super::*;
use crate::execution;
pub fn run(
    profile: &str,
    key: &str,
    context: u32,
    out: &Path,
    fixture: &super::artifacts::Fixture,
) -> DynResult<()> {
    let tokenizer = execution::capture_for(
        "hf",
        &[
            "download".into(),
            fixture.tokenizer_repo.clone(),
            "--revision".into(),
            fixture.tokenizer_revision.clone(),
            "--include".into(),
            "*token*".into(),
            "*config.json".into(),
            "*.jinja".into(),
        ],
        &[],
        600,
    )?;
    let mut aiperf = vec![
        "tool".into(),
        "run".into(),
        "--python".into(),
        "3.12".into(),
        "--constraints".into(),
        "ci/aiperf-requirements.lock".into(),
        "--from".into(),
        "aiperf==0.13.0".into(),
        "aiperf".into(),
        "profile".into(),
        "--ui".into(),
        "simple".into(),
        "--scenario".into(),
        "inferencex-agentx-mvp".into(),
        "--url".into(),
        "http://127.0.0.1:9337".into(),
        "--model".into(),
        key.into(),
        "--tokenizer".into(),
        super::artifacts::downloaded_path(&tokenizer)?
            .display()
            .to_string(),
        "--max-context-length".into(),
        context.to_string(),
        "--endpoint-type".into(),
        "chat".into(),
        "--public-dataset".into(),
        "semianalysis_cc_traces_weka_062126_256k".into(),
        "--concurrency".into(),
        "1".into(),
        "--use-server-token-count".into(),
        "--random-seed".into(),
        "42".into(),
        "--benchmark-duration".into(),
        if profile == "pr" {
            "120"
        } else if profile == "nightly" {
            "900"
        } else {
            "1800"
        }
        .into(),
        "--artifact-dir".into(),
        out.join("agentx").display().to_string(),
    ];
    if profile == "pr" {
        aiperf.extend([
            "--unsafe-override".into(),
            "--num-dataset-entries".into(),
            "4".into(),
        ]);
    } else {
        aiperf.extend([
            "--num-profile-runs".into(),
            "2".into(),
            "--no-profile-run-disable-warmup-after-first".into(),
        ]);
    }
    let result = execution::run("uv", &aiperf, &[], 15000);
    let retained = super::retention::archive(&out.join("agentx"), out);
    if let Err(error) = &retained {
        eprintln!("AgentX evidence retention failed: {error}");
    }
    result?;
    retained?;
    if validate_exports(&out.join("agentx"), profile != "pr")? == 0 {
        return Err("AgentX produced no result exports".into());
    }
    Ok(())
}

fn validate_exports(directory: &Path, official: bool) -> DynResult<usize> {
    let mut found = 0;
    for entry in std::fs::read_dir(directory)? {
        let path = entry?.path();
        if path.is_dir() {
            found += validate_exports(&path, official)?;
        } else if path
            .file_name()
            .is_some_and(|name| name == "profile_export_aiperf.json")
        {
            let export: serde_json::Value = serde_json::from_slice(&std::fs::read(path)?)?;
            if export["request_count"]["avg"]
                .as_f64()
                .is_none_or(|n| n <= 0.0)
                || export["error_request_count"]["avg"].as_f64().unwrap_or(0.0) > 0.0
                || export["is_complete"] != true
            {
                return Err("AgentX produced empty, incomplete or failed request evidence".into());
            }
            if official && export["metadata"]["submission_valid"] != true {
                return Err("AgentX rejected extended-run validity".into());
            }
            found += 1;
        }
    }
    Ok(found)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn invalid_official_results_fail_but_smoke_is_explicit() {
        let d = tempfile::tempdir().unwrap();
        write_json_file(
            &d.path().join("profile_export_aiperf.json"),
            &serde_json::json!({"metadata":{"submission_valid":false},"is_complete":true,"request_count":{"avg":4}}),
        )
        .unwrap();
        assert!(validate_exports(d.path(), true).is_err());
        assert_eq!(validate_exports(d.path(), false).unwrap(), 1);
    }
}
