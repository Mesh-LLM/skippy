use super::*;
use crate::execution;
pub fn run(
    profile: &str,
    key: &str,
    context: u32,
    out: &Path,
    fixture: &super::artifacts::Fixture,
) -> DynResult<()> {
    let mut tokenizer_args = vec![
        "download".into(),
        fixture.tokenizer_repo.clone(),
        "--revision".into(),
        fixture.tokenizer_revision.clone(),
        "--quiet".into(),
    ];
    tokenizer_args.extend(fixture.tokenizer_files.iter().cloned());
    let tokenizer = execution::capture_for("hf", &tokenizer_args, &[], 600)?;
    let tokenizer = super::artifacts::downloaded_path(&tokenizer)?;
    for file in &fixture.tokenizer_files {
        if std::fs::metadata(tokenizer.join(file))?.len() == 0 {
            return Err("empty pinned tokenizer asset".into());
        }
    }
    let corpus = super::corpus::load()?;
    super::corpus::verify(out, "before")?;
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
        std::env::var("SKIPPY_AGENTX_ENDPOINT").unwrap_or_else(|_| "http://127.0.0.1:9337".into()),
        "--model".into(),
        key.into(),
        "--tokenizer".into(),
        tokenizer.display().to_string(),
        "--max-context-length".into(),
        context.to_string(),
        "--endpoint-type".into(),
        "chat".into(),
        "--streaming".into(),
        "--cache-bust".into(),
        "first-turn-prefix".into(),
        "--public-dataset".into(),
        corpus.alias,
        "--concurrency".into(),
        "1".into(),
        "--use-server-token-count".into(),
        "--random-seed".into(),
        "42".into(),
        "--benchmark-duration".into(),
        if profile == "pr" {
            "120"
        } else if profile == "nightly" {
            "600"
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
            "1".into(),
            "--unsafe-override".into(),
        ]);
    }
    let result = execution::run("uv", &aiperf, &[], 15000);
    let corpus = super::corpus::verify(out, "after");
    let retained = super::retention::archive(&out.join("agentx"), out);
    if let Err(error) = &retained {
        eprintln!("AgentX evidence retention failed: {error}");
    }
    result?;
    corpus?;
    retained?;
    validate_trials(&out.join("agentx"), profile)
}

fn validate_trials(directory: &Path, profile: &str) -> DynResult<()> {
    let expected = if profile == "pr" { 1 } else { 2 };
    if validate_exports(directory, profile != "pr")? != expected {
        return Err(format!("AgentX requires {expected} complete trial exports").into());
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
                || export["request_error_rate"]["avg"]
                    .as_f64()
                    .is_none_or(|n| n != 0.0)
                || export["error_summary"]
                    .as_array()
                    .is_none_or(|errors| !errors.is_empty())
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
            &serde_json::json!({"metadata":{"submission_valid":false},"is_complete":true,"request_count":{"avg":4},"request_error_rate":{"avg":0},"error_summary":[]}),
        )
        .unwrap();
        assert!(validate_exports(d.path(), true).is_err());
        assert_eq!(validate_exports(d.path(), false).unwrap(), 1);
    }
    #[test]
    fn complete_valid_runs_still_reject_failed_or_missing_request_evidence() {
        let d = tempfile::tempdir().unwrap();
        let path = d.path().join("profile_export_aiperf.json");
        let mut export = serde_json::json!({"metadata":{"submission_valid":true},"is_complete":true,"request_count":{"avg":4},"request_error_rate":{"avg":0},"error_summary":[]});
        write_json_file(&path, &export).unwrap();
        assert_eq!(validate_exports(d.path(), true).unwrap(), 1);
        export["request_error_rate"]["avg"] = 25.into();
        write_json_file(&path, &export).unwrap();
        assert!(validate_exports(d.path(), true).is_err());
        export["request_error_rate"]["avg"] = 0.into();
        export["error_summary"] = serde_json::json!([{"error":"request failed"}]);
        write_json_file(&path, &export).unwrap();
        assert!(validate_exports(d.path(), true).is_err());
        export["error_summary"] = serde_json::Value::Null;
        write_json_file(&path, &export).unwrap();
        assert!(validate_exports(d.path(), true).is_err());
    }
    #[test]
    fn extended_profiles_reject_a_partially_successful_multirun() {
        let d = tempfile::tempdir().unwrap();
        let export = serde_json::json!({"metadata":{"submission_valid":true},"is_complete":true,"request_count":{"avg":4},"request_error_rate":{"avg":0},"error_summary":[]});
        write_json_file(&d.path().join("profile_export_aiperf.json"), &export).unwrap();
        assert!(validate_trials(d.path(), "pr").is_ok());
        assert!(validate_trials(d.path(), "nightly").is_err());
        let second = d.path().join("trial-2");
        std::fs::create_dir(&second).unwrap();
        write_json_file(&second.join("profile_export_aiperf.json"), &export).unwrap();
        assert!(validate_trials(d.path(), "nightly").is_ok());
        assert!(validate_trials(d.path(), "release").is_ok());
    }
}
