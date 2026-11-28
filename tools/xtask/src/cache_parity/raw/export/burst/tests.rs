use super::*;
use crate::{
    artifact,
    cache_parity::{capture::replay_transcript, raw::RequestResult},
    command::write_json_file,
};
use serde_json::{Value, json};
use std::fs;

const WINDOW: u64 = 5_000_000;

fn stream(cached: u32, disk_payload: Option<u64>) -> String {
    let delta = json!({"choices":[{"delta":{"content":"42"}}],
        "timings":{"predicted_n":1,"prompt_ms":1.0,"predicted_ms":0.5}});
    let mut finish = json!({"choices":[{"delta":{"content":""},"finish_reason":"length"}],
        "timings":{"predicted_n":2,"prompt_ms":1.0,"predicted_ms":1.25,
            "sampled_ttft_ms":1.0,"sampled_decode_ms":0.75,"cache_n":cached},
        "usage":{"prompt_tokens":21,"completion_tokens":2,"total_tokens":23,
            "prompt_tokens_details":{"cached_tokens":cached}}});
    if let Some(disk_payload) = disk_payload {
        finish["timings"]["cache_disk_payload_bytes_read_total"] = json!(disk_payload);
        if cached > 0 {
            finish["timings"]["cache_source"] = json!("l2");
        }
    }
    format!("data: {delta}\n\ndata: {finish}\n\ndata: [DONE]\n\n")
}

#[test]
fn l2_export_rechecks_the_preparation_counter_against_every_measured_stream() {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path();
    fs::create_dir(root.join("inputs")).unwrap();
    let burst = root.join("burst-001");
    let mut inputs = Vec::new();
    let mut requests = Vec::new();
    for slot in 0..2 {
        let preparation = burst.join(format!("slot-{slot:03}"));
        let replay = burst.join(format!("replay/request-{:03}", slot + 1));
        let freeze = |word| {
            let bytes = serde_json::to_vec(&json!({"id_slot":slot,"cache_prompt":true,
                "messages":[{"role":"user","content":word}]}))
            .unwrap();
            let sha = artifact::digest(&bytes[..]).unwrap();
            fs::write(root.join("inputs").join(format!("{sha}.json")), &bytes).unwrap();
            (sha, bytes)
        };
        let (sha, bytes) = freeze("seed");
        let (eviction, other) = freeze("displace");
        save_exchange(&preparation.join("seed"), &bytes, &sha, 0, Some(0));
        save_exchange(&preparation.join("eviction"), &other, &eviction, 0, Some(0));
        save_exchange(
            &preparation.join("final-eviction"),
            &other,
            &eviction,
            0,
            Some(120),
        );
        save_exchange(&replay, &bytes, &sha, 20, Some(120));
        inputs.push(
            serde_json::from_value(json!({"request_sha256":sha,"seed":{"request_sha256":sha},
            "eviction":{"request_sha256":eviction},"expected_content":"42","prompt_tokens":21}))
            .unwrap(),
        );
        requests.push(
            json!({"ordinal":slot+1,"burst":1,"started_ns":slot,"finished_ns":WINDOW,
            "request_sha256":sha,"output_matches":true}),
        );
    }
    write_json_file(
        &root.join("trace.json"),
        &json!({"schema_version":1,"scope":"l2_restore_bursts",
        "engine":"candidate","bursts":[{"ordinal":1,"directory":"burst-001","requests":2}]}),
    )
    .unwrap();
    write_json_file(
        &burst.join("l2.json"),
        &json!({"pid":9,"slots":2,"disk_payload_before":120}),
    )
    .unwrap();
    write_json_file(
        &burst.join("replay/trace.json"),
        &json!({"schema_version":1,"engine":"candidate",
        "duration_ns":WINDOW,"requests":requests}),
    )
    .unwrap();
    write_json_file(
        &burst.join("replay/memory.json"),
        &json!({"schema_version":1,"profile":"cuda",
        "pid":9,"duration_ns":WINDOW,"samples":[{"offset_ns":0,"host_bytes":100,"device_bytes":200},
        {"offset_ns":WINDOW,"host_bytes":110,"device_bytes":210}]}),
    )
    .unwrap();
    let export = || {
        measure(
            root,
            root,
            std::slice::from_ref(&inputs),
            Engine::Candidate,
            Backend::Cuda,
            &ProbePin {
                metal_memory_probe_sha256: None,
            },
            CacheState::L2Restore,
        )
    };
    let (rows, _, _) = export().unwrap();
    assert_eq!(rows.len(), 2);
    assert!(rows.iter().all(|row| matches!(
        row.result,
        RequestResult::Completed {
            observed_cache: CacheState::L2Restore,
            output_correct: true,
            cached_tokens: 20,
            ..
        }
    )));
    let replay = burst.join("replay/request-002");
    let request = fs::read(replay.join("request.json")).unwrap();
    let digest = artifact::digest(&request[..]).unwrap();
    save_exchange(&replay, &request, &digest, 20, Some(121));
    assert!(
        export()
            .err()
            .unwrap()
            .to_string()
            .contains("read disk payload bytes")
    );
}

fn save_exchange(path: &Path, request: &[u8], sha: &str, cached: u32, disk_payload: Option<u64>) {
    fs::create_dir_all(path).unwrap();
    let response = stream(cached, disk_payload);
    let engine = if disk_payload.is_some() {
        Engine::Candidate
    } else {
        Engine::Baseline
    };
    let mut observation = replay_transcript(response.as_bytes(), engine).unwrap();
    observation["first_token_ns"] = json!(2_000_000);
    observation["last_token_ns"] = json!(3_000_000);
    fs::write(path.join("request.json"), request).unwrap();
    fs::write(path.join("response.sse"), &response).unwrap();
    write_json_file(
        &path.join("observation.json"),
        &json!({"schema_version":3,"engine":engine,
        "request_sha256":sha,"response_sha256":artifact::digest(response.as_bytes()).unwrap(),
        "observation":observation,"error":null}),
    )
    .unwrap();
}

fn write_capture(root: &Path, ordinal: usize) -> Vec<request::Input> {
    let path = root.join(format!("burst-{ordinal:03}"));
    let mut rows = Vec::new();
    let mut inputs = Vec::new();
    for slot in 0..2 {
        let request = serde_json::to_vec(&json!({"id_slot":slot,"cache_prompt":true,
            "stream":true,"stream_options":{"include_usage":true}}))
        .unwrap();
        let sha = artifact::digest(&request[..]).unwrap();
        fs::write(root.join("inputs").join(format!("{sha}.json")), &request).unwrap();
        let seed = path.join(format!("publish/seed-{:03}", slot + 1));
        let replay = path.join(format!("replay/request-{:03}", slot + 1));
        save_exchange(&seed, &request, &sha, 0, None);
        save_exchange(&replay, &request, &sha, 20, None);
        let name = format!("slot-{slot}.bin");
        write_json_file(
            &seed.join("slot-save.json"),
            &json!({"response":{
            "id_slot":slot,"filename":name,"n_saved":21,"n_written":1024}}),
        )
        .unwrap();
        write_json_file(
            &seed.join("snapshot.json"),
            &json!({"bytes":1024,"sha256":"a".repeat(64)}),
        )
        .unwrap();
        write_json_file(&replay.join("slot-restore.json"), &json!({"client_ns":1_000_000,"response":{
            "id_slot":slot,"filename":name,"n_restored":21,"n_read":1024,"timings":{"restore_ms":0.5}}})).unwrap();
        rows.push(
            json!({"ordinal":slot+1,"burst":1,"started_ns":slot,"finished_ns":WINDOW,
            "request_sha256":sha,"output_matches":true,"request_error":null}),
        );
        inputs.push(
            serde_json::from_value(json!({"request_sha256":sha,"seed":{"request_sha256":sha},
            "expected_content":"42","prompt_tokens":21}))
            .unwrap(),
        );
    }
    write_json_file(
        &path.join("replay/trace.json"),
        &json!({"schema_version":1,"engine":"baseline",
        "duration_ns":WINDOW,"requests":rows}),
    )
    .unwrap();
    write_json_file(
        &path.join("restart.json"),
        &json!({"publisher_pid":2,"replay_pid":3,"slots":2}),
    )
    .unwrap();
    write_json_file(
        &path.join("replay/memory.json"),
        &json!({"schema_version":1,"profile":"cuda",
        "pid":3,"duration_ns":WINDOW,"samples":[
            {"offset_ns":0,"host_bytes":ordinal*100,"device_bytes":50},
            {"offset_ns":WINDOW,"host_bytes":100,"device_bytes":ordinal*100}]}),
    )
    .unwrap();
    inputs
}

#[test]
fn post_restart_export_charges_slot_restore_and_rejects_forged_receipts() {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path();
    fs::create_dir(root.join("inputs")).unwrap();
    let workload = [write_capture(root, 1), write_capture(root, 2)];
    write_json_file(
        &root.join("trace.json"),
        &json!({"schema_version":1,"scope":"post_restart_bursts",
        "engine":"baseline","bursts":[
            {"ordinal":1,"directory":"burst-001","requests":2},
            {"ordinal":2,"directory":"burst-002","requests":2}]}),
    )
    .unwrap();
    let export = || {
        measure(
            root,
            root,
            &workload,
            Engine::Baseline,
            Backend::Cuda,
            &ProbePin {
                metal_memory_probe_sha256: None,
            },
            CacheState::PostRestartL3,
        )
    };
    let (requests, duration, memory) = export().unwrap();
    assert_eq!(duration, 2 * WINDOW);
    assert_eq!(memory.len(), 3);
    assert_eq!(memory[1].offset_ns, WINDOW);
    assert_eq!((memory[1].host_bytes, memory[1].device_bytes), (200, 100));
    assert_eq!(requests.len(), 4);
    assert!(requests.iter().all(|row| matches!(
        row.result,
        RequestResult::Completed {
            observed_cache: CacheState::PostRestartL3,
            cached_tokens: 20,
            inference_ttft_ns: 1_500_000,
            end_to_end_ttft_ns: 3_000_000,
            output_correct: true,
            ..
        }
    )));
    let restore = root.join("burst-002/replay/request-002/slot-restore.json");
    let mut receipt: Value = load_json(&restore).unwrap();
    receipt["response"]["id_slot"] = json!(0);
    write_json_file(&restore, &receipt).unwrap();
    assert!(
        export()
            .err()
            .unwrap()
            .to_string()
            .contains("restore does not match")
    );
}
