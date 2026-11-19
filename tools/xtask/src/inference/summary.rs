pub(super) fn verdict(plan: &str, required: &str, result: &str) -> (bool, &'static str) {
    match (plan, required, result) {
        ("success", "true", "success") => (true, "Native Spark lab qualification"),
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
