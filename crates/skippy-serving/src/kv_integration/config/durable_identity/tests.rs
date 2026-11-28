use super::*;
use crate::kv_integration::identity::durable_namespace;
use skippy_cache::{
    ExactStatePayload, L3CacheManager, StoreLimits, numerical_model_identity_for_stage,
    prefix_namespace_hash,
};

const ABI: [u32; 3] = [0, 1, 80];
const PAYLOAD: &str = "full-state";

fn device(name: &str, id: &str) -> BackendDevice {
    BackendDevice {
        name: name.into(),
        description: None,
        device_id: Some(id.into()),
        memory_free: 100,
        memory_total: 200,
        device_type: BackendDeviceType::Gpu,
        caps: 0,
    }
}

#[test]
fn auto_placement_uses_stable_device_identity_and_order() {
    let config = StageConfig::default();
    assert!(config.selected_device.is_none());
    let mut devices = [device("CUDA0", "pcie:0"), device("CUDA1", "pcie:1")];
    let identity =
        |config, devices: &[BackendDevice]| state_identity(config, PAYLOAD, ABI, devices).unwrap();
    let first = identity(&config, &devices);
    devices[0].memory_free /= 2;
    assert_eq!(first, identity(&config, &devices));
    devices.swap(0, 1);
    assert_ne!(first, identity(&config, &devices));
    devices.swap(0, 1);
    devices[0].device_id = Some("pcie:4".into());
    assert_ne!(first, identity(&config, &devices));
}

#[test]
fn stale_abi_backend_model_and_placement_miss_on_one_store() {
    let directory = tempfile::tempdir().unwrap();
    let limits = StoreLimits::new(1 << 20, 0);
    let config = StageConfig {
        model_id: "same-model-alias".into(),
        source_model_sha256: Some("a".repeat(64)),
        ..StageConfig::default()
    };
    let other_model = StageConfig {
        source_model_sha256: Some("b".repeat(64)),
        ..config.clone()
    };
    let other_placement = StageConfig {
        kv_offload: Some(false),
        ..config.clone()
    };
    let metal = [device("MTL0", "device:0")];
    let cuda = [device("CUDA0", "device:0")];
    let tokens: Vec<i32> = (0..128).collect();
    let payload = ExactStatePayload::full_state(vec![7; 1024]);
    let namespace = |config: &StageConfig, state_identity: &str| {
        durable_namespace(&prefix_namespace_hash(config, 0, None), state_identity)
    };
    let original_state = state_identity(&config, PAYLOAD, ABI, &metal).unwrap();
    let original_model = numerical_model_identity_for_stage(&config);
    let original_namespace = namespace(&config, &original_state);

    {
        let publisher = L3CacheManager::acquire(directory.path(), limits).unwrap();
        let legacy_state = exact_state_identity_for_stage(&config, PAYLOAD);
        let legacy = publisher.tier_for_model(original_model.clone(), legacy_state, 4096);
        let legacy_namespace = prefix_namespace_hash(&config, 0, None);
        let unrelated_tokens: Vec<i32> = (128..256).collect();
        legacy
            .spill(&legacy_namespace, &unrelated_tokens, &payload, None, None)
            .unwrap();
        let tier = publisher.tier_for_model(original_model.clone(), original_state.clone(), 4096);
        tier.spill(&original_namespace, &tokens, &payload, None, None)
            .unwrap();
    }
    let replacement = L3CacheManager::acquire(directory.path(), limits).unwrap();
    let warm_tier = replacement.tier_for_model(original_model, original_state.clone(), 4096);
    let reads_before = replacement.activity().unwrap().bytes_read;
    assert!(
        warm_tier
            .fill_longest(&original_namespace, &(128..256).collect::<Vec<_>>(), 4)
            .unwrap()
            .is_none()
    );
    for (variant, abi, devices) in [
        (&config, [0, 1, 79], &metal),
        (&config, ABI, &cuda),
        (&other_model, ABI, &metal),
        (&other_placement, ABI, &metal),
    ] {
        let state = state_identity(variant, PAYLOAD, abi, devices).unwrap();
        assert_ne!(state, original_state);
        let prefix = namespace(variant, &state);
        let tier =
            replacement.tier_for_model(numerical_model_identity_for_stage(variant), state, 4096);
        assert!(tier.fill_longest(&prefix, &tokens, 4).unwrap().is_none());
    }
    assert_eq!(replacement.activity().unwrap().bytes_read, reads_before);
    let hit = warm_tier
        .fill_longest(&original_namespace, &tokens, 4)
        .unwrap()
        .unwrap();
    assert_eq!(hit.token_count, 128);
    assert_eq!(hit.payload_bytes, 1024);
}
