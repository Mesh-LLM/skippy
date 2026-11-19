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
    assert_eq!(rates.decode_ms, 300.0);
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
            if field.starts_with("prompt") {
                assert_eq!((rates.prefill_tokens, rates.prefill_ms), (0, 0.0));
                assert_eq!((rates.decode_tokens, rates.decode_ms), (30, 300.0));
            } else {
                assert_eq!((rates.decode_tokens, rates.decode_ms), (0, 0.0));
                assert_eq!((rates.prefill_tokens, rates.prefill_ms), (300, 600.0));
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
        assert_eq!((rates.prefill_tokens, rates.prefill_ms), (0, 0.0));
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
fn rejects_invalid_native_attempts() {
    assert!(successful_run(&json!({"runs":[{"id":"run_bad/../","status":"succeeded"}]})).is_err());
    assert!(successful_run(&json!({"runs":[{"id":"run_ok","status":"failed"}]})).is_err());
}
