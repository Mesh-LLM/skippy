use super::*;
use skippy_cache::{ExactStatePayload, StoreLimits};

const LIMIT: u64 = 1024 * 1024;

fn owner(temporary: &tempfile::TempDir) -> L3CacheManager {
    L3CacheManager::acquire(temporary.path(), StoreLimits::new(LIMIT, 0)).unwrap()
}

#[tokio::test]
async fn authorized_control_uses_live_counters_and_preserves_pinned_state() {
    let directory = tempfile::tempdir().unwrap();
    let manager = owner(&directory);
    let admin = start(Some(&manager)).await.unwrap().unwrap();
    let tier = manager.tier_for_model("model-a".into(), "state".into(), 1024);
    let key = tier
        .spill(
            "namespace",
            &[10, 20, 30],
            &ExactStatePayload::full_state(vec![42; 8192]),
            None,
            None,
        )
        .unwrap();
    let pin = tier.store().pin(&key);
    let status = forward(directory.path(), &Operation::Status)
        .await
        .unwrap()
        .unwrap();
    let status = serde_json::to_value(status).unwrap();
    assert_eq!(status["activity"]["writes"], 1);
    assert_eq!(status["models"][0]["model_identity"], "model-a");
    assert_eq!(status["budget_bytes"], LIMIT);

    let prune = forward(
        directory.path(),
        &Operation::Prune {
            model_identity: Some("model-a".into()),
            target_bytes: Some(1),
        },
    )
    .await
    .unwrap()
    .unwrap();
    let prune = serde_json::to_value(prune).unwrap();
    assert_eq!(prune["target_reached"], false);
    assert_eq!(prune["freed_bytes"], 0);
    let clear = Operation::Clear {
        model_identity: None,
    };
    let result = forward(directory.path(), &clear).await.unwrap().unwrap();
    assert_eq!(serde_json::to_value(result).unwrap()["freed_bytes"], 0);
    assert!(tier.store().load_manifest(&key).is_ok());

    drop(pin);
    let result = forward(directory.path(), &clear).await.unwrap().unwrap();
    assert!(
        serde_json::to_value(result).unwrap()["freed_bytes"]
            .as_u64()
            .unwrap()
            > 0
    );
    assert!(manager.inventory().unwrap().is_empty());
    assert!(manager.root().join(ENDPOINT_FILE).exists());
    drop(admin);
    assert!(!manager.root().join(ENDPOINT_FILE).exists());
}

#[tokio::test]
async fn unauthorized_control_cannot_clear_the_live_cache() {
    let directory = tempfile::tempdir().unwrap();
    let manager = owner(&directory);
    let _admin = start(Some(&manager)).await.unwrap().unwrap();
    let tier = manager.tier("state".into(), 1024);
    tier.spill(
        "namespace",
        &[1, 2, 3],
        &ExactStatePayload::full_state(b"test".to_vec()),
        None,
        None,
    )
    .unwrap();
    let endpoint = discover(directory.path()).unwrap().unwrap();
    let mut token = endpoint.token;
    token[0] ^= 1;
    let mut stream = TcpStream::connect(endpoint.address).await.unwrap();
    write_frame(
        &mut stream,
        &Request {
            token,
            command: Operation::Clear {
                model_identity: None,
            },
        },
        REQUEST_LIMIT,
    )
    .await
    .unwrap();
    assert!(read_frame::<Reply>(&mut stream, REPLY_LIMIT).await.is_err());
    assert_eq!(manager.inventory().unwrap().len(), 1);
}

#[tokio::test]
async fn stale_owner_endpoint_allows_a_locked_offline_reopen() {
    let directory = tempfile::tempdir().unwrap();
    let manager = owner(&directory);
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let std::net::SocketAddr::V4(address) = listener.local_addr().unwrap() else {
        unreachable!()
    };
    publish(
        manager.root(),
        &Endpoint {
            version: ENDPOINT_VERSION,
            address,
            token: [0; 16],
        },
    )
    .unwrap();
    drop(listener);
    drop(manager);
    assert!(
        forward(directory.path(), &Operation::Status)
            .await
            .unwrap()
            .is_none()
    );
    assert_eq!(owner(&directory).limits().budget_bytes, LIMIT);
}

#[tokio::test]
async fn degraded_owner_exposes_the_storage_error_and_uncapped_prune_requires_a_target() {
    let directory = tempfile::tempdir().unwrap();
    let manager = L3CacheManager::acquire(directory.path(), StoreLimits::new(0, 0)).unwrap();
    let _admin = start(Some(&manager)).await.unwrap().unwrap();
    let tier = manager.tier("state".into(), 1024);
    let hashed = skippy_cache::l3_namespace_key("namespace");
    let blocker = manager
        .root()
        .join("prefixes")
        .join(hashed.trim_start_matches("blake3:"));
    fs::write(blocker, b"blocks index publication").unwrap();
    assert!(
        tier.spill(
            "namespace",
            &[1, 2, 3],
            &ExactStatePayload::full_state(b"test".to_vec()),
            None,
            None,
        )
        .is_err()
    );
    let status = forward(directory.path(), &Operation::Status)
        .await
        .unwrap()
        .unwrap();
    let status = serde_json::to_value(status).unwrap();
    assert_eq!(status["state"], "degraded");
    assert_eq!(status["reason"], "storage_error");
    assert!(status["activity"]["last_error"].is_string());
    let error = forward(
        directory.path(),
        &Operation::Prune {
            target_bytes: None,
            model_identity: None,
        },
    )
    .await
    .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("uncapped cache requires an explicit --target")
    );
}
