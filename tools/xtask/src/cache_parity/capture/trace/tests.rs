use super::*;
use serde_json::{Value, json};

const REQUEST: &[u8] = br#"{"stream":true,"stream_options":{"include_usage":true}}"#;

fn write_trace(root: &Path, bursts: Value) -> PathBuf {
    let path = root.join("workload.json");
    write_json_file(&path, &json!({"schema_version":1,"bursts":bursts})).unwrap();
    path
}

#[test]
fn workload_preserves_order_and_exact_request_bytes() {
    let temp = tempfile::tempdir().unwrap();
    fs::write(temp.path().join("request.json"), REQUEST).unwrap();
    let digest = artifact::digest(REQUEST).unwrap();
    let entry = json!({"request":"request.json","request_sha256":digest,"expected_content":"42"});
    let path = write_trace(temp.path(), json!([[entry, entry], [entry]]));
    let workload = Workload::load(&path).unwrap();
    assert_eq!(
        workload.bursts.iter().map(Vec::len).collect::<Vec<_>>(),
        [2, 1]
    );
    for request in workload.bursts.iter().flatten() {
        assert_eq!(request.bytes, REQUEST);
        assert_eq!(request.sha256, digest);
        assert_eq!(request.expected_content.as_deref(), Some("42"));
    }
    let retained = temp.path().join("retained");
    fs::create_dir(&retained).unwrap();
    workload.retain_inputs(&retained).unwrap();
    assert_eq!(
        fs::read(retained.join("workload.json")).unwrap(),
        workload.manifest
    );
    let inputs = retained.join("inputs");
    assert_eq!(fs::read_dir(&inputs).unwrap().count(), 1);
    assert_eq!(
        fs::read(inputs.join(format!("{digest}.json"))).unwrap(),
        REQUEST
    );
    fs::write(temp.path().join("request.json"), [REQUEST, b"\n"].concat()).unwrap();
    assert!(
        Workload::load(&path)
            .err()
            .unwrap()
            .to_string()
            .contains("digest changed")
    );
}

#[test]
fn workload_rejects_invalid_schedule_and_nonlocal_inputs() {
    let temp = tempfile::tempdir().unwrap();
    let digest = artifact::digest(REQUEST).unwrap();
    let entry = json!({"request":"../request.json","request_sha256":digest});
    let path = write_trace(temp.path(), json!([[entry]]));
    let error = Workload::load(&path).err().unwrap();
    assert!(error.to_string().contains("relative input"));
    let path = write_trace(temp.path(), json!([vec![entry; 33]]));
    let error = Workload::load(&path).err().unwrap();
    assert!(error.to_string().contains("bursts of 1–32"));
}

#[test]
fn concurrent_post_restart_requires_frozen_slot_assignments_and_admitted_replays() {
    let temp = tempfile::tempdir().unwrap();
    let bytes = br#"{"stream":true,"stream_options":{"include_usage":true},"cache_prompt":true}"#;
    fs::write(temp.path().join("replay.json"), bytes).unwrap();
    let entry = json!({
        "request":"replay.json",
        "request_sha256":artifact::digest(bytes.as_slice()).unwrap(),
    });
    let path = write_trace(temp.path(), json!([[entry], [entry]]));
    let server = super::super::server::Server {
        binary: temp.path().join("test-engine"),
        args: Vec::new(),
        memory_sampling: None,
        metal_memory_probe: None,
    };
    let mode = durable::Restore::L3 { seed: bytes };
    assert!(
        mode.validate(&Workload::load(&path).unwrap(), &server, &server)
            .is_ok()
    );
    let path = write_trace(temp.path(), json!([[entry, entry]]));
    assert!(
        restarted::validate(&Workload::load(&path).unwrap())
            .unwrap_err()
            .to_string()
            .contains("own pinned seed")
    );
    let slots = [0, 1].map(|slot| {
        let path = format!("slot-{slot}.json");
        let mut request: Value = serde_json::from_slice(bytes).unwrap();
        request["id_slot"] = json!(slot);
        let request = serde_json::to_vec(&request).unwrap();
        fs::write(temp.path().join(&path), &request).unwrap();
        let input =
            json!({"request":path,"request_sha256":artifact::digest(request.as_slice()).unwrap()});
        let mut entry = input.clone();
        entry["seed"] = input;
        entry
    });
    let path = write_trace(temp.path(), json!([slots]));
    assert!(restarted::validate(&Workload::load(&path).unwrap()).is_ok());
    let l2 = durable::Restore::L2 {
        seed: bytes,
        eviction: REQUEST,
    };
    assert!(
        l2.validate(&Workload::load(&path).unwrap(), &server, &server)
            .unwrap_err()
            .to_string()
            .contains("one request per burst")
    );
    let path = write_trace(temp.path(), json!([[slots[1], slots[0]]]));
    assert!(
        restarted::validate(&Workload::load(&path).unwrap())
            .unwrap_err()
            .to_string()
            .contains("id_slot 0")
    );
    fs::write(temp.path().join("replay.json"), REQUEST).unwrap();
    let path = write_trace(
        temp.path(),
        json!([[{
            "request":"replay.json",
            "request_sha256":artifact::digest(REQUEST).unwrap(),
        }]]),
    );
    assert!(
        mode.validate(&Workload::load(&path).unwrap(), &server, &server)
            .unwrap_err()
            .to_string()
            .contains("cache_prompt")
    );
}
