use super::*;

pub fn run() -> DynResult<()> {
    let sha = std::env::var("SOURCE_SHA")?;
    if sha.len() != 40 || !sha.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("invalid qualification source SHA".into());
    }
    let (passed, description) = verdict(
        &std::env::var("PLAN").unwrap_or_default(),
        &std::env::var("REQUIRED").unwrap_or_default(),
        &std::env::var("RESULT").unwrap_or_default(),
    );
    let state = if passed { "success" } else { "failure" };
    crate::execution::run(
        "gh",
        &[
            "api".into(),
            "--method".into(),
            "POST".into(),
            format!("repos/general-inference/skippy/statuses/{sha}"),
            "-f".into(),
            format!("state={state}"),
            "-f".into(),
            "context=Inference qualification".into(),
            "-f".into(),
            format!("target_url={}", std::env::var("RUN_URL")?),
            "-f".into(),
            format!("description={description}"),
        ],
        &[("GH_TOKEN", std::env::var("GH_TOKEN")?)],
        60,
    )?;
    if !passed {
        return Err("inference qualification gate failed".into());
    }
    Ok(())
}

pub(super) fn verdict(plan: &str, required: &str, result: &str) -> (bool, &'static str) {
    match (plan, required, result) {
        ("success", "true", "success") => (true, "Native JY Spark qualification"),
        ("success", "false", "skipped") => (
            true,
            "No inference inputs changed; model tests not required",
        ),
        _ => (
            false,
            "Inference admission or qualification incomplete or failed",
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_completed_required_runs_or_admitted_noops_satisfy_the_gate() {
        assert!(verdict("success", "true", "success").0);
        let noop = verdict("success", "false", "skipped");
        assert!(noop.0);
        assert!(noop.1.contains("not required"));
        for plan in ["success", "failure", "cancelled", "skipped", ""] {
            for required in ["true", "false", ""] {
                for result in ["success", "failure", "cancelled", "skipped", ""] {
                    let expected = plan == "success"
                        && matches!(
                            (required, result),
                            ("true", "success") | ("false", "skipped")
                        );
                    assert_eq!(verdict(plan, required, result).0, expected);
                }
            }
        }
    }
}
