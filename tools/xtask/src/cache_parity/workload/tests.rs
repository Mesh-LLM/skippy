use super::*;

fn inputs(root: &Path, state: CacheState, shape: Workload, width: usize) -> Value {
    write_workload("kv-parity", state, shape, width, root).unwrap();
    serde_json::from_slice(&fs::read(root.join("workload.json")).unwrap()).unwrap()
}

fn read(root: &Path, entry: &Value) -> Value {
    let bytes = fs::read(root.join(entry["request"].as_str().unwrap())).unwrap();
    assert_eq!(
        entry["request_sha256"],
        artifact::digest(&bytes[..]).unwrap()
    );
    serde_json::from_slice(&bytes).unwrap()
}

#[test]
fn generated_inputs_pin_the_full_schedule_and_cache_state() {
    let root = tempfile::tempdir().unwrap();
    let first = root.path().join("a");
    let second = root.path().join("b");
    let manifest = inputs(&first, CacheState::L2Restore, Workload::ExactReplay, 6);
    assert_eq!(
        manifest,
        inputs(&second, CacheState::L2Restore, Workload::ExactReplay, 6)
    );
    let bursts = manifest["bursts"].as_array().unwrap();
    assert_eq!(
        bursts
            .iter()
            .map(|burst| burst.as_array().unwrap().len())
            .sum::<usize>(),
        REQUESTS as usize
    );
    assert_eq!(bursts[0].as_array().unwrap().len(), 6);
    assert_eq!(bursts.last().unwrap().as_array().unwrap().len(), 2);
    for (slot, entry) in bursts[0].as_array().unwrap().iter().enumerate() {
        let measured = read(&first, entry);
        assert_eq!(measured["id_slot"], slot);
        assert_eq!(measured["cache_prompt"], true);
        assert_eq!(measured, read(&first, &entry["seed"]));
        assert_ne!(
            measured["messages"][0],
            read(&first, &entry["eviction"])["messages"][0]
        );
    }
    let cold = root.path().join("cold");
    let workload = inputs(&cold, CacheState::ColdPrefill, Workload::ExactReplay, 8);
    let entry = &workload["bursts"][0][0];
    assert!(entry.get("seed").is_none());
    assert_eq!(read(&cold, entry)["cache_prompt"], false);
}

#[test]
fn divergent_suffixes_preserve_the_prefix_and_conversations_grow() {
    let root = tempfile::tempdir().unwrap();
    let divergent = root.path().join("divergent");
    let manifest = inputs(&divergent, CacheState::WarmL1, Workload::DivergentTail, 8);
    let entry = &manifest["bursts"][0][0];
    let measured = read(&divergent, entry);
    let seed = read(&divergent, &entry["seed"]);
    assert_eq!(seed["messages"][1], measured["messages"][1]);
    assert_ne!(seed["messages"][3], measured["messages"][3]);

    let growing = root.path().join("growing");
    let manifest = inputs(
        &growing,
        CacheState::PostRestartL3,
        Workload::GrowingConversation,
        8,
    );
    let bursts = manifest["bursts"].as_array().unwrap();
    let last = &bursts.last().unwrap()[0];
    let first = read(&growing, &bursts[0][0]);
    let measured = read(&growing, last);
    let seed = read(&growing, &last["seed"]);
    let measured = measured["messages"].as_array().unwrap();
    assert!(measured.len() > first["messages"].as_array().unwrap().len());
    assert!(measured.starts_with(seed["messages"].as_array().unwrap()));
}
