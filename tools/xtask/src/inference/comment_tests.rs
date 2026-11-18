use super::*;

fn smoke_plan() -> Plan<'static> {
    let ladder: Ladder =
        serde_json::from_str(include_str!("../../../../ci/inference-ladder.json")).unwrap();
    plan(&ladder, "pr", "1111111111111111111111111111111111111111").unwrap()
}

#[test]
fn reports_each_configuration_without_turning_missing_work_into_a_pass() {
    let plan = smoke_plan();
    let directory = tempfile::tempdir().unwrap();
    let states = [
        "success",
        "failure",
        "cancelled",
        "skipped",
        "timed_out",
        "",
        "success",
    ];
    let jobs: Vec<_> = plan
        .rows
        .iter()
        .zip(states)
        .enumerate()
        .map(|(index, (row, state))| {
            json!({
                "id":index + 1,"name":format!("qualification ({})",row.id),"conclusion":state
            })
        })
        .collect();
    let row = &plan.rows[0];
    write_json_file(
        &directory.path().join(format!("{}-job.json", row.id)),
        &json!({"id":"job_example","repo_rev":plan.source_sha}),
    )
    .unwrap();
    let body = render(
        &plan,
        &json!({"jobs":jobs}),
        directory.path(),
        42,
        2,
        false,
        "Inference admission or qualification incomplete or failed",
    )
    .unwrap();
    assert!(body.contains("Failed or incomplete"));
    for row in &plan.rows {
        assert!(body.contains(&row.id));
    }
    for state in [
        "Passed",
        "Failed",
        "Cancelled",
        "Skipped (not qualified)",
        "Missing or incomplete",
    ] {
        assert!(body.contains(state));
    }
    assert!(body.contains("/attempts/2"));
    assert!(body.contains("/job/1"));
    assert!(body.contains("https://pilot.jianyang.cloud/jobs/job_example"));
    assert!(body.contains("do not establish an AgentX performance score"));
    assert!(body.contains("expire after 30 days"));
    let missing = render(
        &plan,
        &json!({"jobs":[]}),
        directory.path(),
        42,
        2,
        false,
        "Incomplete",
    )
    .unwrap();
    assert_eq!(missing.matches("Missing or incomplete").count(), 7);
    assert!(!missing.contains("| Passed |"));
    let aggregate_only = render(
        &plan,
        &json!({"jobs":[]}),
        directory.path(),
        42,
        2,
        true,
        "Native JY Spark qualification",
    )
    .unwrap();
    assert!(aggregate_only.contains("qualification: Failed or incomplete"));
}

#[test]
fn irrelevant_changes_explicitly_do_not_claim_model_qualification() {
    let directory = tempfile::tempdir().unwrap();
    let body = render(
        &smoke_plan(),
        &Value::Null,
        directory.path(),
        42,
        1,
        true,
        "No inference inputs changed; model tests not required",
    )
    .unwrap();
    assert!(body.contains("did not run"));
    assert!(!body.contains("| Passed |"));
    assert!(!body.contains("Configuration |"));
}

#[test]
fn only_updates_the_bot_owned_marker_and_preserves_newer_attempts() {
    let bot = json!({"id":2,"user":{"login":"github-actions[bot]","type":"Bot"},"body":format!("{MARKER}\n<!-- run:42:2 -->\nold result")});
    let forged = json!({"id":3,"user":{"login":"person","type":"User"},"body":bot["body"]});
    let unrelated = json!({"id":4,"user":bot["user"],"body":"another workflow's comment"});
    let pages = json!([[unrelated], [bot, forged]]);
    let owned = owned_comment(&pages).unwrap().unwrap();
    assert_eq!(owned["id"], 2);
    for (run, attempt, expected) in [
        (41, 9, true),
        (42, 1, true),
        (42, 2, false),
        (42, 3, false),
        (43, 1, false),
    ] {
        assert_eq!(newer(&owned["body"], run, attempt), expected);
    }
    assert!(owned_comment(&json!([[forged]])).unwrap().is_none());
    assert!(owned_comment(&json!({"message":"denied"})).is_err());
}

#[test]
fn rejects_mixed_source_receipts_and_unsafe_record_links() {
    let plan = smoke_plan();
    let directory = tempfile::tempdir().unwrap();
    let row = &plan.rows[0];
    let path = directory.path().join(format!("{}-job.json", row.id));
    for receipt in [
        json!({"id":"job_example","repo_rev":"wrong-source"}),
        json!({"id":"job_bad)[url]","repo_rev":plan.source_sha}),
    ] {
        write_json_file(&path, &receipt).unwrap();
        assert!(jy_record(directory.path(), row, plan.source_sha).is_err());
    }
}
