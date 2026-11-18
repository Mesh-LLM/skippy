use super::*;
use serde_json::json;

fn fixture() -> (Plan<'static>, Value, Value, Value) {
    let ladder: Ladder =
        serde_json::from_str(include_str!("../../../../ci/inference-ladder.json")).unwrap();
    let plan = plan(&ladder, "pr", "1111111111111111111111111111111111111111").unwrap();
    let row = &plan.rows[0];
    let job = json!({"id":"job_test","repo_rev":plan.source_sha,"status":"succeeded","runs":[{"id":"run_test","status":"succeeded"}]});
    let qualification = json!({"source_sha":plan.source_sha,"model":row.id.strip_suffix("-standalone").unwrap(),"mode":row.mode,"profile":"pr","status":"passed"});
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("../../../../ci/inference-quality.json")).unwrap();
    let quality = cases.into_iter().enumerate().map(|(i, case)| json!({
        "case":case,"passed":true,
        "response":{"usage":{"prompt_tokens_details":{"cached_tokens":10}},"timings":{"prompt_n":110,"prompt_ms":100*(i+1),"predicted_n":10,"predicted_ms":100}},
        "repeat":{"timings":{"prompt_n":10000,"prompt_ms":1,"predicted_n":10000,"predicted_ms":1}}
    })).collect::<Vec<_>>();
    (plan, job, qualification, json!(quality))
}

#[test]
fn aggregates_phase_times_excludes_cached_tokens_and_ignores_repeats() {
    let (plan, job, qualification, quality) = fixture();
    let rates = summarize(
        &job,
        &plan.rows[0],
        plan.source_sha,
        &qualification,
        &quality,
    )
    .unwrap();
    assert_eq!(rates.requests, 3);
    assert_eq!(rates.prefill_tokens, 300);
    assert_eq!(rates.prefill_ms, 600.0);
    assert_eq!(rates.decode_tokens, 30);
    assert_eq!(rates.display(), ("500.0".into(), "100.0".into()));
}

#[test]
fn incomplete_or_invalid_timings_never_become_partial_rates() {
    let (plan, job, qualification, quality) = fixture();
    for field in ["prompt_n", "prompt_ms", "predicted_n", "predicted_ms"] {
        for bad in [Value::Null, json!(0), json!(-1), json!("oops")] {
            let mut quality = quality.clone();
            quality[1]["response"]["timings"][field] = bad;
            let rates = summarize(
                &job,
                &plan.rows[0],
                plan.source_sha,
                &qualification,
                &quality,
            )
            .unwrap();
            let (prefill, decode) = rates.display();
            if field.starts_with("prompt") {
                assert_eq!(prefill, "Unavailable");
                assert_eq!(decode, "100.0");
            } else {
                assert_eq!(decode, "Unavailable");
                assert_eq!(prefill, "500.0");
            }
        }
    }
    for cached in [Value::Null, json!(111), json!(110)] {
        let mut quality = quality.clone();
        quality[0]["response"]["usage"]["prompt_tokens_details"]["cached_tokens"] = cached;
        let rates = summarize(
            &job,
            &plan.rows[0],
            plan.source_sha,
            &qualification,
            &quality,
        )
        .unwrap();
        assert_eq!(rates.display().0, "Unavailable");
    }
    for ms in [f64::NAN, f64::INFINITY, -1.0, 0.0] {
        assert_eq!(rate(10, ms), "Unavailable");
    }
}

#[test]
fn rejects_cross_source_cross_configuration_and_incomplete_evidence() {
    let (plan, job, qualification, quality) = fixture();
    for field in ["source_sha", "model", "mode", "profile", "status"] {
        let mut qualification = qualification.clone();
        qualification[field] = json!("wrong");
        assert!(
            summarize(
                &job,
                &plan.rows[0],
                plan.source_sha,
                &qualification,
                &quality
            )
            .is_err()
        );
    }
    for field in ["repo_rev", "status"] {
        let mut job = job.clone();
        job[field] = json!("wrong");
        assert!(
            summarize(
                &job,
                &plan.rows[0],
                plan.source_sha,
                &qualification,
                &quality
            )
            .is_err()
        );
    }
    for bad in [
        json!([]),
        json!([quality[0]]),
        json!([quality[0], quality[0], quality[0]]),
    ] {
        assert!(summarize(&job, &plan.rows[0], plan.source_sha, &qualification, &bad).is_err());
    }
    let mut failed = quality.clone();
    failed[0]["passed"] = json!(false);
    assert!(
        summarize(
            &job,
            &plan.rows[0],
            plan.source_sha,
            &qualification,
            &failed
        )
        .is_err()
    );
}

#[test]
fn comment_receipts_are_bound_to_the_job_attempt_and_configuration() {
    let (plan, job, qualification, quality) = fixture();
    let row = &plan.rows[0];
    let directory = tempfile::tempdir().unwrap();
    assert_eq!(
        read(directory.path(), row, plan.source_sha).unwrap().0,
        "Unavailable"
    );
    write_json_file(&directory.path().join(format!("{}-job.json", row.id)), &job).unwrap();
    let rates = summarize(&job, row, plan.source_sha, &qualification, &quality).unwrap();
    let path = directory.path().join(format!("{}-throughput.json", row.id));
    write_json_file(&path, &rates).unwrap();
    assert_eq!(
        read(directory.path(), row, plan.source_sha).unwrap(),
        rates.display()
    );
    let good = serde_json::to_value(rates).unwrap();
    for field in ["source_sha", "configuration", "job_id", "run_id"] {
        let mut bad = good.clone();
        bad[field] = json!("wrong");
        write_json_file(&path, &bad).unwrap();
        assert!(read(directory.path(), row, plan.source_sha).is_err());
    }
    assert!(successful_run(&json!({"runs":[{"id":"run_bad/../","status":"succeeded"}]})).is_err());
    assert!(successful_run(&json!({"runs":[{"id":"run_ok","status":"failed"}]})).is_err());
}
