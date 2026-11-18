use super::*;

#[test]
fn partial_frames_role_chunks_and_usage_preserve_token_times() {
    let mut stream = Capture::default();
    stream
        .push(
            b"data: {\"choices\":[{\"delta\":{\"role\":\"assistant\"}}]}\r\n\r\n",
            1,
        )
        .unwrap();
    stream
        .push(b"data: {\"choices\":[{\"delta\":{\"reason", 2)
        .unwrap();
    stream
        .push(b"ing_content\":\"thinking\"}}]}\n\n", 3)
        .unwrap();
    stream
        .push(
            b"data: {\"choices\":[{\"delta\":{\"content\":\"42\"}}],\n",
            4,
        )
        .unwrap();
    stream
        .push(b"data: \"timings\":{\"prompt_ms\":1.0}}\n\n", 5)
        .unwrap();
    stream
        .push(b"data: {\"choices\":[{\"finish_reason\":\"stop\"}]}\n\n", 7)
        .unwrap();
    stream.push(b"data: {\"usage\":{\"prompt_tokens\":20,\"completion_tokens\":3,\"total_tokens\":23,\"prompt_tokens_details\":{\"cached_tokens\":0}}}\n\ndata: [DONE]\n\n", 10).unwrap();
    let result = stream.finish(None).unwrap();
    assert_eq!((result.first_token_ns, result.last_token_ns), (3, 5));
    assert_eq!(result.content, "42");
    assert_eq!(result.cached_tokens, Some(0));
    assert_eq!(result.final_server_timings.unwrap()["prompt_ms"], 1.0);
}

#[test]
fn truncated_or_error_streams_are_not_valid_measurements() {
    let mut stream = Capture::default();
    stream.push(b"data: [DONE]", 1).unwrap();
    assert!(stream.finish(None).is_err());
    let mut stream = Capture::default();
    assert!(
        stream
            .push(
                b"event: error\ndata: {\"message\":\"generation failed\"}\n\n",
                1
            )
            .is_err()
    );
    let mut stream = Capture::default();
    assert!(stream.push(b"data: [DONE]\n\ndata: {}\n\n", 1).is_err());
}

fn completed_stream(reason: &str, native_stop_type: Option<&str>, sampled: u32) -> Capture {
    let mut capture = Capture::default();
    capture
        .push(
            b"data: {\"choices\":[{\"delta\":{\"content\":\"42\"}}]}\n\n",
            2,
        )
        .unwrap();
    let mut final_chunk = serde_json::json!({"choices":[{"finish_reason":reason}]});
    if let Some(stop) = native_stop_type {
        final_chunk["__verbose"] =
            serde_json::json!({ "stop_type": stop, "tokens_predicted": sampled });
    }
    capture
        .push(format!("data: {final_chunk}\n\n").as_bytes(), 3)
        .unwrap();
    capture.push(format!("data: {{\"usage\":{{\"prompt_tokens\":21,\"completion_tokens\":{sampled},\"total_tokens\":{}}}}}\n\ndata: [DONE]\n\n", 21 + sampled).as_bytes(), 4).unwrap();
    capture
}

#[test]
fn completed_token_accounting_excludes_only_a_native_end_token() {
    let candidate = completed_stream("stop", None, 2)
        .finish(Some(Engine::Candidate))
        .unwrap();
    let baseline = completed_stream("stop", Some("eos"), 3)
        .finish(Some(Engine::Baseline))
        .unwrap();
    assert_eq!(candidate.normalized_completion_tokens, Some(2));
    assert_eq!(baseline.normalized_completion_tokens, Some(2));
    assert_eq!(baseline.completion_tokens, 3);
    for (reason, stop) in [
        ("stop", "word"),
        ("length", "limit"),
        ("tool_calls", "word"),
    ] {
        let baseline = completed_stream(reason, Some(stop), 3)
            .finish(Some(Engine::Baseline))
            .unwrap();
        assert_eq!(baseline.normalized_completion_tokens, Some(3));
    }
    let baseline = completed_stream("stop", None, 3)
        .finish(Some(Engine::Baseline))
        .unwrap();
    assert_eq!(baseline.normalized_completion_tokens, None);
    let baseline = completed_stream("length", None, 3)
        .finish(Some(Engine::Baseline))
        .unwrap();
    assert_eq!(baseline.normalized_completion_tokens, Some(3));
    assert!(
        completed_stream("length", Some("eos"), 3)
            .finish(Some(Engine::Baseline))
            .is_err()
    );
}

#[test]
fn sampled_phases_use_the_last_nonterminal_baseline_sample() {
    let mut samples = NativeSamples::default();
    for (predicted_n, predicted_ms) in [(1, 0.001), (2, 13.001), (3, 25.001)] {
        samples.record(&serde_json::json!({"predicted_n":predicted_n,"predicted_ms":predicted_ms,"prompt_ms":40.0}));
    }
    let phases = samples
        .phases(Some(Engine::Baseline), Some(2), None)
        .unwrap();
    assert_eq!(phases.ttft_ms, 40.0);
    assert!((phases.decode_ms - 13.0).abs() < 1e-9);
    assert!(
        samples
            .phases(Some(Engine::Baseline), Some(4), None)
            .is_none()
    );
    let candidate =
        serde_json::json!({"predicted_n":2,"sampled_ttft_ms":41.0,"sampled_decode_ms":10.0});
    let phases = samples
        .phases(Some(Engine::Candidate), Some(2), Some(&candidate))
        .unwrap();
    assert_eq!((phases.ttft_ms, phases.decode_ms), (41.0, 10.0));
    assert!(
        samples
            .phases(Some(Engine::Candidate), Some(3), Some(&candidate))
            .is_none()
    );
}
