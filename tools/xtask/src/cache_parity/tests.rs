use super::{
    DynResult,
    contract::*,
    decision::{self, Outcome, Summary},
    identity::Checkout,
};
use crate::cache_qualification::read_manifest;
use skippy_model_artifact::kv_fixtures::KvFixtureManifest;

fn verify(plan: &Plan, plan_sha: String, evidence: &Evidence) -> DynResult<Summary> {
    decision::verify(plan, &plan_sha, evidence, |_, _, _| Ok(String::new()))
}

fn fixture_plan() -> (KvFixtureManifest, Plan) {
    let manifest = read_manifest(include_str!(
        "../../../../crates/skippy-model-artifact/src/kv_fixtures/models.json"
    ))
    .unwrap();
    let host = |os: &str, arch: &str| Host {
        os: os.into(),
        arch: arch.into(),
        host_profile_sha256: "a".repeat(64),
        candidate_binary_sha256: "b".repeat(64),
        baseline_binary_sha256: "c".repeat(64),
    };
    let mut cells = Vec::new();
    for model in &manifest.models {
        for backend in [Backend::Metal, Backend::Cuda] {
            for cache in [
                CacheState::ColdPrefill,
                CacheState::WarmL1,
                CacheState::L2Restore,
                CacheState::PostRestartL3,
            ] {
                for workload in [
                    Workload::ExactReplay,
                    Workload::DivergentTail,
                    Workload::GrowingConversation,
                ] {
                    cells.push(PlannedCell {
                        cell: Cell {
                            family: model.family.as_str().into(),
                            backend,
                            cache,
                            workload,
                        },
                        model_sha256: model.sha256.clone(),
                        template_sha256: model.chat_template_sha256.clone(),
                        sidecars_sha256: EMPTY_SIDECARS_SHA256.into(),
                        workload_sha256: "e".repeat(64),
                        candidate_config_sha256: "f".repeat(64),
                        baseline_config_sha256: "1".repeat(64),
                    });
                }
            }
        }
    }
    let plan = Plan {
        schema_version: VERSION,
        source_sha: "2".repeat(40),
        llama_pin: "3".repeat(40),
        patch_queue_sha256: "4".repeat(64),
        model_manifest_sha256: "5".repeat(64),
        abi_version: "0.1.80".into(),
        cache_format_version: 1,
        harness_sha256: "6".repeat(64),
        agentx_corpus_sha256: "7".repeat(64),
        agentx_scorer_sha256: "8".repeat(64),
        backends: Hosts {
            metal: host("macos", "arm64"),
            cuda: host("linux", "x86_64"),
        },
        cells,
    };
    (manifest, plan)
}

fn fixture_checkout(plan: &Plan) -> Checkout {
    Checkout {
        source_sha: plan.source_sha.clone(),
        source_tree_clean: true,
        llama_pin: plan.llama_pin.clone(),
        patch_queue_sha256: plan.patch_queue_sha256.clone(),
        model_manifest_sha256: plan.model_manifest_sha256.clone(),
        abi_version: plan.abi_version.clone(),
        cache_format_version: plan.cache_format_version,
        harness_sha256: plan.harness_sha256.clone(),
        agentx_scorer_sha256: plan.agentx_scorer_sha256.clone(),
    }
}

fn measurement(plan: &Plan) -> Evidence {
    let mut artifact_index = 0;
    let cells = plan
        .cells
        .iter()
        .map(|row| {
            let host = plan.backends.get(row.cell.backend);
            let mut trial = |engine| {
                artifact_index += 1;
                let (binary, config) = match engine {
                    Engine::Candidate => {
                        (&host.candidate_binary_sha256, &row.candidate_config_sha256)
                    }
                    Engine::Baseline => (&host.baseline_binary_sha256, &row.baseline_config_sha256),
                };
                Trial {
                    engine,
                    binary_sha256: binary.clone(),
                    config_sha256: config.clone(),
                    host_profile_sha256: host.host_profile_sha256.clone(),
                    raw_artifact_sha256: format!("{artifact_index:064x}"),
                    requests: REQUESTS,
                    failed_requests: 0,
                    incorrect_outputs: 0,
                    requests_with_expected_cache_path: REQUESTS,
                    metrics: Metrics {
                        agentx_score: 100.0,
                        inference_ttft_ms: Percentiles {
                            p50: 100.0,
                            p90: 200.0,
                            p99: 300.0,
                        },
                        end_to_end_ttft_ms: Percentiles {
                            p50: 200.0,
                            p90: 300.0,
                            p99: 400.0,
                        },
                        prefill_tokens_per_second: 100.0,
                        decode_tokens_per_second: 100.0,
                        peak_host_bytes: 100_000,
                        peak_device_bytes: 100_000,
                    },
                }
            };
            let pairs = (0..PAIRS)
                .map(|index| {
                    let mut pair = [trial(Engine::Candidate), trial(Engine::Baseline)];
                    pair.rotate_left(index % 2);
                    pair
                })
                .collect();
            MeasuredCell {
                cell: row.cell.clone(),
                model_sha256: row.model_sha256.clone(),
                template_sha256: row.template_sha256.clone(),
                sidecars_sha256: row.sidecars_sha256.clone(),
                workload_sha256: row.workload_sha256.clone(),
                pairs,
            }
        })
        .collect();
    Evidence {
        schema_version: VERSION,
        plan_sha256: "9".repeat(64),
        cells,
    }
}

#[test]
fn exact_phase_one_matrix_and_boundaries_pass() {
    let (manifest, plan) = fixture_plan();
    plan.validate(&manifest, &fixture_checkout(&plan)).unwrap();
    let mut evidence = measurement(&plan);
    for (index, pair) in evidence.cells[0].pairs.iter_mut().enumerate() {
        let candidate = &mut pair[index % 2];
        candidate.metrics.agentx_score = 98.0;
        candidate.metrics.inference_ttft_ms.p50 = 102.0;
        candidate.metrics.decode_tokens_per_second = 98.0;
        candidate.metrics.peak_host_bytes = 102_000;
    }
    let summary = verify(&plan, evidence.plan_sha256.clone(), &evidence).unwrap();
    assert_eq!(summary.cells.len(), 96);
    assert_eq!(summary.outcome, Outcome::Pass);
}

#[test]
fn regressing_and_inconclusive_cells_prevent_a_pass() {
    let (_, plan) = fixture_plan();
    let mut evidence = measurement(&plan);
    for (index, pair) in evidence.cells[0].pairs.iter_mut().enumerate().take(6) {
        pair[index % 2].metrics.inference_ttft_ms.p50 = 103.0;
    }
    let summary = verify(&plan, evidence.plan_sha256.clone(), &evidence).unwrap();
    assert_eq!(summary.outcome, Outcome::Inconclusive);
    assert_eq!(summary.cells[0].outcome, Outcome::Inconclusive);
    assert!(
        summary.cells[1..]
            .iter()
            .all(|cell| cell.outcome == Outcome::Pass)
    );

    evidence.cells[1].pairs[0][0].incorrect_outputs = 1;
    let summary = verify(&plan, evidence.plan_sha256.clone(), &evidence).unwrap();
    assert_eq!(summary.outcome, Outcome::Regression);
    assert_eq!(summary.cells[1].outcome, Outcome::Regression);
}

#[test]
fn missing_duplicate_and_stale_rows_are_rejected() {
    let (manifest, mut plan) = fixture_plan();
    let mut evidence = measurement(&plan);
    assert!(verify(&plan, "0".repeat(64), &evidence).is_err());
    let original = evidence.cells.pop().unwrap();
    assert!(verify(&plan, evidence.plan_sha256.clone(), &evidence).is_err());
    evidence.cells.push(original);
    evidence.cells[0].cell = evidence.cells[1].cell.clone();
    assert!(verify(&plan, evidence.plan_sha256.clone(), &evidence).is_err());

    let checkout = fixture_checkout(&plan);
    plan.cells[0].template_sha256 = "0".repeat(64);
    assert!(plan.validate(&manifest, &checkout).is_err());
}

#[test]
fn plan_identities_must_match_the_independently_observed_checkout() {
    let (manifest, plan) = fixture_plan();
    let checkout = fixture_checkout(&plan);
    let original = serde_json::to_value(&plan).unwrap();
    for (field, other) in [
        ("source_sha", serde_json::json!("a".repeat(40))),
        ("llama_pin", serde_json::json!("a".repeat(40))),
        ("patch_queue_sha256", serde_json::json!("a".repeat(64))),
        ("model_manifest_sha256", serde_json::json!("a".repeat(64))),
        ("abi_version", serde_json::json!("0.1.81")),
        ("cache_format_version", serde_json::json!(2)),
        ("harness_sha256", serde_json::json!("a".repeat(64))),
        ("agentx_scorer_sha256", serde_json::json!("a".repeat(64))),
    ] {
        let mut changed = original.clone();
        changed[field] = other;
        let changed: Plan = serde_json::from_value(changed).unwrap();
        let error = changed.validate(&manifest, &checkout).unwrap_err();
        assert!(error.to_string().contains(field), "{error}");
    }
}

#[test]
fn forged_decision_inconsistent_samples_and_reordered_pairs_are_rejected() {
    let (_, plan) = fixture_plan();
    let mut evidence = measurement(&plan);
    evidence.cells[0].pairs[0].swap(0, 1);
    assert!(verify(&plan, evidence.plan_sha256.clone(), &evidence).is_err());
    evidence.cells[0].pairs[0].swap(0, 1);
    evidence.cells[0].pairs[0][0].requests_with_expected_cache_path -= 1;
    assert!(verify(&plan, evidence.plan_sha256.clone(), &evidence).is_err());
    let mut raw = serde_json::to_value(&evidence).unwrap();
    raw["decision"] = "pass".into();
    assert!(serde_json::from_value::<Evidence>(raw).is_err());
}

#[test]
fn matching_cache_cells_must_replay_the_same_ordered_requests() {
    let (_, plan) = fixture_plan();
    let evidence = measurement(&plan);
    let error = decision::verify(&plan, &evidence.plan_sha256, &evidence, |_, _, trial| {
        Ok(format!("{:?}", trial.engine))
    })
    .unwrap_err();
    assert!(error.to_string().contains("different request sequences"));
}
