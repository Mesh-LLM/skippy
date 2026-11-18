use super::*;
use serde_json::{Value, json};

#[test]
fn phase_one_requires_complete_distinct_immutable_identities() {
    let manifest = PhaseOneManifest::bundled().unwrap();
    assert_eq!(manifest.models.len(), 4);
    let source: Value = serde_json::from_str(PHASE_ONE_MANIFEST).unwrap();
    for (field, bad) in [
        ("revision", json!("main")),
        ("sha256", json!("0".repeat(64))),
        ("chat_template_sha256", Value::Null),
        ("quantization", json!("Q5_K_M")),
        (
            "required_sidecars",
            json!([{"file":"mtp.gguf", "size_bytes":12}]),
        ),
        ("file", json!("../model-Q4_K_M.gguf")),
    ] {
        let mut modified = source.clone();
        modified["models"][0][field] = bad;
        assert!(
            PhaseOneManifest::parse(&modified.to_string()).is_err(),
            "accepted {field}"
        );
    }
    let mut modified = source.clone();
    modified["models"][1]["sha256"] = source["models"][0]["sha256"].clone();
    assert!(PhaseOneManifest::parse(&modified.to_string()).is_err());
    let mut modified = source;
    modified["models"][3]["quantization"] = json!("Q4_K_M");
    assert!(PhaseOneManifest::parse(&modified.to_string()).is_err());
}

#[test]
fn architecture_targets_reference_real_canaries_and_keep_flash_next_experimental() {
    let canaries: Value = serde_json::from_str(include_str!(
        "../../../../ci/llama-canary/family-certified.json"
    ))
    .unwrap();
    let canaries = canaries["models"].as_array().unwrap();
    let profiles = bundled_architecture_profiles().unwrap();
    assert!(profiles.iter().all(|profile| {
        canaries.iter().any(|canary| {
            canary["family"] == profile.canary_fixture
                && canary["architecture"] == profile.architecture
                && canary["profile"] == "full"
        })
    }));
    assert_eq!(
        profiles
            .iter()
            .find(|profile| profile.architecture == "qwen4exp")
            .unwrap()
            .track,
        ArchitectureTrack::Experimental
    );
}
