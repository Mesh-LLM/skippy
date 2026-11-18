use super::*;
use crate::{
    cache_parity::{capture::replay_transcript, raw::ArtifactSource},
    command::write_json_file,
};
use serde_json::{Value, json};

const REQUEST: &[u8] = br#"{"stream":true,"stream_options":{"include_usage":true}}"#;
const WINDOW: u64 = 5_000_000;

#[test]
fn metal_export_requires_the_host_pinned_probe_and_complete_window() {
    let sha256 = "a".repeat(64);
    let mut memory = Memory {
        schema_version: 1,
        profile: "metal".into(),
        duration_ns: WINDOW,
        pid: None,
        probe_sha256: Some(sha256.clone()),
        samples: Vec::new(),
    };
    let probe = ProbePin {
        metal_memory_probe_sha256: Some(sha256),
    };
    assert!(validate_memory(&memory, Backend::Metal, WINDOW, &probe).is_ok());
    assert!(validate_memory(&memory, Backend::Metal, WINDOW + 1, &probe).is_err());
    assert!(validate_memory(&memory, Backend::Cuda, WINDOW, &probe).is_err());
    memory.probe_sha256 = Some("b".repeat(64));
    assert!(validate_memory(&memory, Backend::Metal, WINDOW, &probe).is_err());
}

fn response(answer: &str) -> String {
    let delta = json!({
        "choices":[{"delta":{"content":answer}}],
        "timings":{"predicted_n":1,"prompt_ms":1.0,"predicted_ms":0.5},
    });
    let finish = json!({
        "choices":[{"delta":{"content":""}, "finish_reason":"length"}],
        "timings":{
            "predicted_n":2,"prompt_ms":1.0,"predicted_ms":1.25,
            "sampled_ttft_ms":1.0,"sampled_decode_ms":0.75,"cache_n":0,
        },
        "usage":{"prompt_tokens":21,"completion_tokens":2,"total_tokens":23,
            "prompt_tokens_details":{"cached_tokens":0}},
    });
    format!("data: {delta}\n\ndata: {finish}\n\ndata: [DONE]\n\n")
}

fn write_leg(root: &Path, name: &str, engine: Engine, request_sha: &str) {
    let leg = root.join(name);
    let duration = WINDOW * u64::from(REQUESTS);
    let mut rows = Vec::new();
    for ordinal in 1..=REQUESTS {
        let key = leg.join(format!("request-{ordinal:03}"));
        fs::create_dir_all(&key).unwrap();
        let correct = ordinal != 2 || engine == Engine::Baseline;
        let response = response(if correct { "42" } else { "43" });
        let mut observation = replay_transcript(response.as_bytes(), engine).unwrap();
        observation["first_token_ns"] = json!(2_000_000);
        observation["last_token_ns"] = json!(3_000_000);
        fs::write(key.join("request.json"), REQUEST).unwrap();
        fs::write(key.join("response.sse"), &response).unwrap();
        write_json_file(
            &key.join("observation.json"),
            &json!({
                "schema_version":3,"engine":engine,"request_sha256":request_sha,
                "response_sha256":artifact::digest(response.as_bytes()).unwrap(),
                "observation":observation,"error":null,
            }),
        )
        .unwrap();
        rows.push(json!({
            "ordinal":ordinal,"burst":ordinal,"request_sha256":request_sha,
            "started_ns":u64::from(ordinal-1)*WINDOW,"finished_ns":u64::from(ordinal)*WINDOW,
            "output_matches":correct,"request_error":null,
        }));
    }
    write_json_file(
        &leg.join("trace.json"),
        &json!({
            "schema_version":1,"engine":engine,"requests":rows,"duration_ns":duration,"error":null,
        }),
    )
    .unwrap();
    write_json_file(
        &leg.join("memory.json"),
        &json!({
            "schema_version":1,"profile":"cuda","duration_ns":duration,"samples":[
                {"offset_ns":0,"host_bytes":100_000,"device_bytes":200_000},
                {"offset_ns":duration,"host_bytes":110_000,"device_bytes":210_000},
            ],
        }),
    )
    .unwrap();
}

fn fixture(root: &Path) -> (PlannedCell, Host) {
    let request_sha = artifact::digest(REQUEST).unwrap();
    fs::create_dir(root.join("inputs")).unwrap();
    fs::write(
        root.join("inputs").join(format!("{request_sha}.json")),
        REQUEST,
    )
    .unwrap();
    let entry = json!({"request":"input.json","request_sha256":request_sha,"expected_content":"42","prompt_tokens":21});
    let bursts = vec![vec![entry]; REQUESTS as usize];
    write_json_file(
        &root.join("workload.json"),
        &json!({"schema_version":1,"bursts":bursts}),
    )
    .unwrap();
    fs::write(
        root.join("host-profile.json"),
        br#"{"os":"linux","arch":"x86_64"}"#,
    )
    .unwrap();
    fs::write(root.join("configuration.json"), b"{}").unwrap();
    let digest = |name| artifact::sha256(&root.join(name)).unwrap();
    let host = Host {
        os: "linux".into(),
        arch: "x86_64".into(),
        host_profile_sha256: digest("host-profile.json"),
        candidate_binary_sha256: "a".repeat(64),
        baseline_binary_sha256: "b".repeat(64),
    };
    let row = PlannedCell {
        cell: Cell {
            family: "dense-attention".into(),
            backend: Backend::Cuda,
            cache: CacheState::ColdPrefill,
            workload: Workload::ExactReplay,
        },
        model_sha256: "c".repeat(64),
        template_sha256: "d".repeat(64),
        sidecars_sha256: EMPTY_SIDECARS_SHA256.into(),
        workload_sha256: digest("workload.json"),
        candidate_config_sha256: digest("configuration.json"),
        baseline_config_sha256: digest("configuration.json"),
    };
    write_json_file(&root.join("pairs.json"), &json!({
        "schema_version":1,"scope":"diagnostic_workload_capture","configuration_sha256":row.candidate_config_sha256,
        "workload_sha256":row.workload_sha256,"cache_state":"cold_prefill","requested_pairs":1,"binary_sha256":{
            "skippy":host.candidate_binary_sha256,"llama_server":host.baseline_binary_sha256,
        },
        "legs":[
            {"pair":1,"engine":"candidate","directory":"pair-01-skippy","error":null},
            {"pair":1,"engine":"baseline","directory":"pair-01-llama_server","error":null},
        ]
    })).unwrap();
    write_json_file(
        &root.join("capture-identity.json"),
        &TraceIdentity {
            schema_version: 1,
            pairs_sha256: digest("pairs.json"),
            host_profile_sha256: host.host_profile_sha256.clone(),
            model_sha256: row.model_sha256.clone(),
        },
    )
    .unwrap();
    for (name, engine) in [
        ("pair-01-skippy", Engine::Candidate),
        ("pair-01-llama_server", Engine::Baseline),
    ] {
        write_leg(root, name, engine, &request_sha);
    }
    (row, host)
}

#[test]
fn exports_two_raw_trials_and_rejects_host_only_or_forged_observations() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let (row, host) = fixture(root);
    let plan_sha = "e".repeat(64);
    let output = root.join("raw");
    let measured = assemble(root, &row, &host, &plan_sha, &output).unwrap();
    let [candidate, baseline] = &measured.pairs[0];
    assert_eq!(candidate.incorrect_outputs, 1);
    assert_eq!(baseline.incorrect_outputs, 0);
    assert_eq!(candidate.metrics.peak_device_bytes, 210_000);
    let source = ArtifactSource {
        directory: &output,
        plan_sha: &plan_sha,
    };
    assert_eq!(
        source.check_trial(&row, 1, candidate).unwrap(),
        source.check_trial(&row, 1, baseline).unwrap()
    );
    assert_eq!(fs::read_dir(&output).unwrap().count(), 2);

    let leg = root.join("pair-01-skippy");
    let memory = leg.join("memory.json");
    let memory_bytes = fs::read(&memory).unwrap();
    let mut host_only: Value = serde_json::from_slice(&memory_bytes).unwrap();
    host_only["profile"] = json!("host");
    for sample in host_only["samples"].as_array_mut().unwrap() {
        sample["device_bytes"] = Value::Null;
    }
    write_json_file(&memory, &host_only).unwrap();
    let error = assemble(root, &row, &host, &plan_sha, &root.join("invalid"))
        .err()
        .unwrap();
    assert!(error.to_string().contains("memory evidence"), "{error}");
    assert!(!root.join("invalid").exists());
    fs::write(&memory, memory_bytes).unwrap();

    let path = leg.join("request-002/observation.json");
    let mut forged: Value = load_json(&path).unwrap();
    forged["observation"]["content"] = json!("42");
    write_json_file(&path, &forged).unwrap();
    let error = assemble(root, &row, &host, &plan_sha, &root.join("invalid"))
        .err()
        .unwrap();
    assert!(
        error
            .to_string()
            .contains("content differs from the response transcript"),
        "{error}"
    );
    assert!(!root.join("invalid").exists());
}
