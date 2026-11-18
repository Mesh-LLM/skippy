use super::*;

fn raw_trial(cache: CacheState) -> RawTrial {
    let duration_ns = 88_888_888_888;
    RawTrial {
        schema_version: RAW_VERSION,
        plan_sha256: "a".repeat(64),
        workload_sha256: "b".repeat(64),
        cell: Cell {
            family: "dense-attention".into(),
            backend: Backend::Metal,
            cache,
            workload: Workload::ExactReplay,
        },
        pair: 1,
        engine: Engine::Candidate,
        binary_sha256: "c".repeat(64),
        config_sha256: "d".repeat(64),
        host_profile_sha256: "e".repeat(64),
        duration_ns,
        requests: (1..=REQUESTS)
            .map(|index| Request {
                request_sha256: format!("{index:064x}"),
                prompt_tokens: 100,
                result: RequestResult::Completed {
                    observed_cache: cache,
                    cached_tokens: if cache == CacheState::ColdPrefill {
                        0
                    } else {
                        90
                    },
                    output_tokens: 18,
                    output_correct: index != 2,
                    inference_ttft_ns: u64::from(index) * 1_000_000,
                    end_to_end_ttft_ns: u64::from(index + 5) * 1_000_000,
                    prefill_ns: 1_000_000,
                    decode_ns: 333_000_000,
                },
            })
            .collect(),
        memory: vec![
            MemorySample {
                offset_ns: 0,
                host_bytes: 14_000,
                device_bytes: 9000,
            },
            MemorySample {
                offset_ns: duration_ns / 2,
                host_bytes: 28_000,
                device_bytes: 6000,
            },
            MemorySample {
                offset_ns: duration_ns,
                host_bytes: 17_000,
                device_bytes: 5000,
            },
        ],
    }
}

#[test]
fn observations_compute_percentiles_cache_rates_and_peaks() {
    let mut trial = raw_trial(CacheState::L2Restore);
    trial.requests[199].result = RequestResult::Failed;
    let observed = trial.measure().unwrap();
    assert_eq!(observed.failed_requests, 1);
    assert_eq!(observed.incorrect_outputs, 1);
    let actual = observed.metrics;
    assert_eq!(
        (
            actual.inference_ttft_ms.p50,
            actual.inference_ttft_ms.p90,
            actual.inference_ttft_ms.p99
        ),
        (100.0, 180.0, 198.0)
    );
    assert_eq!(actual.end_to_end_ttft_ms.p99, 203.0);
    assert_eq!(actual.prefill_tokens_per_second, 10_000.0);
    assert!((actual.decode_tokens_per_second - 17.0 / 0.333).abs() < 1.0e-10);
    assert_eq!(
        (actual.peak_host_bytes, actual.peak_device_bytes),
        (28_000, 9000)
    );
    assert!((actual.agentx_score - 199.0 * 118.0 / 88.888_888_888).abs() < 1.0e-10);
}

#[test]
fn cache_path_required_and_failing_memory_coverage_is_rejected() {
    let mut trial = raw_trial(CacheState::L2Restore);
    let first = &mut trial.requests[0].result;
    if let RequestResult::Completed { observed_cache, .. } = first {
        *observed_cache = CacheState::WarmL1;
    }
    assert_eq!(
        trial.measure().err().unwrap().to_string(),
        "request 1 has inconsistent cache, token or timing observations"
    );
    let mut trial = raw_trial(CacheState::ColdPrefill);
    trial.memory.pop();
    assert_eq!(
        trial.measure().err().unwrap().to_string(),
        "raw trial memory samples must be ordered and span the measured interval"
    );
}

#[test]
fn edited_summary_transplanted_pair_and_modified_bytes_fail() {
    let directory = tempfile::tempdir().unwrap();
    let raw = raw_trial(CacheState::PostRestartL3);
    let bytes = serde_json::to_vec(&raw).unwrap();
    let digest = artifact::digest(bytes.as_slice()).unwrap();
    let path = directory.path().join(format!("{digest}.json"));
    fs::write(&path, &bytes).unwrap();
    let plan_cell = PlannedCell {
        cell: raw.cell,
        model_sha256: "f".repeat(64),
        template_sha256: "1".repeat(64),
        sidecars_sha256: "2".repeat(64),
        workload_sha256: raw.workload_sha256,
        candidate_config_sha256: raw.config_sha256,
        baseline_config_sha256: "3".repeat(64),
    };
    let source = ArtifactSource {
        directory: directory.path(),
        plan_sha: &raw.plan_sha256,
    };
    let mut summary =
        serde_json::from_slice::<Trial>(&serde_json::to_vec(&summarize(&path).unwrap()).unwrap())
            .unwrap();
    assert!(source.check_trial(&plan_cell, 1, &summary).is_ok());
    assert!(
        source
            .check_trial(&plan_cell, 2, &summary)
            .unwrap_err()
            .to_string()
            .contains("mismatched")
    );
    summary.metrics.agentx_score += 0.001;
    assert!(
        source
            .check_trial(&plan_cell, 1, &summary)
            .unwrap_err()
            .to_string()
            .contains("differs")
    );
    let mut bytes = bytes;
    bytes.push(b' ');
    fs::write(path, bytes).unwrap();
    assert!(
        source
            .check_trial(&plan_cell, 1, &summary)
            .unwrap_err()
            .to_string()
            .contains("SHA-256")
    );
}
