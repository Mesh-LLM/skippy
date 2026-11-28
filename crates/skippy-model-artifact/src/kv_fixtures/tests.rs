use super::*;
use serde_json::{Value, json};

#[test]
fn kv_fixtures_require_complete_distinct_immutable_identities() {
    let manifest = KvFixtureManifest::bundled().unwrap();
    assert_eq!(manifest.models.len(), 4);
    let source: Value = serde_json::from_str(KV_FIXTURE_MANIFEST).unwrap();
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
            KvFixtureManifest::parse(&modified.to_string()).is_err(),
            "accepted {field}"
        );
    }
    let mut modified = source.clone();
    modified["models"][1]["sha256"] = source["models"][0]["sha256"].clone();
    assert!(KvFixtureManifest::parse(&modified.to_string()).is_err());
    let mut modified = source;
    modified["models"][3]["quantization"] = json!("Q4_K_M");
    assert!(KvFixtureManifest::parse(&modified.to_string()).is_err());
}
