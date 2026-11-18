//! Exercise the public cache operator commands across real process boundaries.

use std::{
    fs,
    path::Path,
    process::{Child, Command, Output, Stdio},
    thread,
    time::{Duration, Instant},
};

use serde_json::{Value, json};
use skippy_cache::{ExactStatePayload, L3CacheManager, StoreLimits};

const BUDGET: u64 = 32 * 1024 * 1024;
const RESERVE: u64 = 1024 * 1024 * 1024;

fn command(temporary: &Path, root: &Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_skippy"));
    command
        .env("SKIPPY_DATA_DIR", temporary.join("downloads"))
        .env("SKIPPY_SERVE_KV_CACHE_DISK", "32MiB")
        .env("SKIPPY_SERVE_KV_CACHE_MIN_FREE", "1GiB")
        .env("SKIPPY_SERVE_KV_CACHE_DISK_DIR", root)
        .stdin(Stdio::null());
    command
}

fn run(root: &Path, args: &[&str]) -> Output {
    command(root.parent().unwrap(), root)
        .args(["--output", "json", "kv-cache"])
        .args(args)
        .output()
        .unwrap()
}

fn report(output: Output) -> Value {
    assert!(output.status.success(), "{:?}", output);
    serde_json::from_slice(&output.stdout).unwrap()
}

fn seed(manager: &L3CacheManager, model: &str, marker: u8) {
    let tier = manager.tier_for_model(model.to_owned(), model.to_owned(), 64 * 1024);
    tier.spill(
        model,
        &[1, 2, 3],
        &ExactStatePayload::full_state(vec![marker; 32 * 1024]),
        None,
        None,
    )
    .unwrap();
}

#[test]
fn disabled_status_has_a_stable_document_and_output_modes() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("kv");
    let expected = json!({
        "schema_version": 1, "operation": "status", "enabled": false, "available": false,
        "directory": null, "state": "disabled", "reason": null, "detail": null,
        "budget_bytes": null, "minimum_free_bytes": null, "low_water_bytes": null,
        "usage": null, "reconciliation": null, "activity": null, "models": []
    });
    assert_eq!(
        report(run(&root, &["status", "--kv-cache-disk", "off"])),
        expected
    );
    let output = command(temporary.path(), &root)
        .args([
            "kv-cache",
            "status",
            "--kv-cache-disk",
            "off",
            "--output",
            "jsonl",
        ])
        .output()
        .unwrap();
    assert!(output.status.success());
    let event = String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .find(|event| event["type"] == "result")
        .unwrap();
    assert_eq!(event["data"], expected);
    let output = command(temporary.path(), &root)
        .args([
            "--output",
            "human",
            "kv-cache",
            "--kv-cache-disk",
            "off",
            "status",
        ])
        .output()
        .unwrap();
    assert!(output.status.success());
    assert!(
        String::from_utf8(output.stdout)
            .unwrap()
            .contains("Disk KV cache is disabled")
    );
    assert!(!root.exists());
}

#[test]
fn disk_cache_defaults_to_a_bounded_auto_budget() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("kv");
    let output = command(temporary.path(), &root)
        .env_remove("SKIPPY_SERVE_KV_CACHE_DISK")
        .env_remove("SKIPPY_KV_CACHE_DISK")
        .env_remove("SKIPPY_L3_DIR")
        .args(["--output", "json", "kv-cache", "status"])
        .output()
        .unwrap();
    let status = report(output);
    assert_eq!(status["enabled"], true);
    assert_eq!(status["minimum_free_bytes"], RESERVE);
    if let Some(budget) = status["budget_bytes"].as_u64() {
        assert!((1..=64 * 1024 * 1024 * 1024).contains(&budget));
    } else {
        assert_eq!(status["available"], false);
        assert!(
            status["detail"]
                .as_str()
                .unwrap()
                .contains("automatic cache budget is zero")
        );
    }
}

#[test]
fn serving_settings_environment_and_cli_share_precedence() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("environment-cache");
    let settings = temporary.path().join("serve.toml");
    fs::write(
        &settings,
        "[cache]\nkv_cache_disk = '8MiB'\nkv_cache_disk_dir = 'file-cache'\nkv_cache_min_free = '1GiB'\n",
    )
    .unwrap();
    let mut cli = command(temporary.path(), &root);
    cli.args(["--output", "json", "kv-cache", "--settings"])
        .arg(&settings);
    let mut file_only = command(temporary.path(), &root);
    let file_report = report(
        file_only
            .env_remove("SKIPPY_SERVE_KV_CACHE_DISK")
            .env_remove("SKIPPY_SERVE_KV_CACHE_DISK_DIR")
            .args(["--output", "json", "kv-cache", "--settings"])
            .arg(&settings)
            .arg("status")
            .output()
            .unwrap(),
    );
    assert_eq!(file_report["budget_bytes"], 8 * 1024 * 1024);
    assert!(temporary.path().join("file-cache/.owner.lock").is_file());
    let report = report(
        cli.args(["status", "--kv-cache-disk", "16MiB"])
            .output()
            .unwrap(),
    );
    assert_eq!(report["budget_bytes"], 16 * 1024 * 1024);
    assert_eq!(report["minimum_free_bytes"], RESERVE);
    assert_eq!(
        report["directory"],
        root.canonicalize().unwrap().display().to_string()
    );
}

#[test]
fn invalid_filters_sizes_and_unconfirmed_mutations_do_not_open_the_store() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("kv");
    for (args, message) in [
        (
            &["clear", "--model-identity", " "][..],
            "model identity must be non-empty",
        ),
        (&["prune", "--target", "32GB"], "IEC suffix"),
        (
            &["prune", "--target", "auto"],
            "target must be a positive IEC size",
        ),
        (&["prune", "--target", "0GiB"], "positive whole number"),
        (&["clear"], "interactive confirmation or --yes"),
        (
            &["prune", "--target", "1KiB"],
            "interactive confirmation or --yes",
        ),
        (
            &["clear", "--yes", "--kv-cache-disk", "off"],
            "disk KV cache is disabled",
        ),
    ] {
        let output = run(&root, args);
        assert!(!output.status.success(), "{args:?}");
        assert!(
            String::from_utf8_lossy(&output.stderr).contains(message),
            "{output:?}"
        );
    }
    assert!(!root.exists());
}

#[test]
fn offline_maintenance_respects_model_identity_and_whole_cache_targets() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("kv");
    let owner = L3CacheManager::acquire(&root, StoreLimits::new(BUDGET, RESERVE)).unwrap();
    for (model, marker) in [("model-b", 1), ("model-a-other", 2), ("model-a", 3)] {
        seed(&owner, model, marker);
    }
    drop(owner);

    let status = report(run(&root, &["status"]));
    assert_eq!(status["state"], "active");
    assert_eq!(status["usage"]["manifests"], 3);
    assert_eq!(status["models"][0]["model_identity"], "model-a");
    assert_eq!(status["models"][1]["model_identity"], "model-a-other");
    assert_eq!(status["models"][2]["model_identity"], "model-b");
    let default_prune = report(run(&root, &["prune", "--yes"]));
    assert_eq!(default_prune["target_bytes"], status["low_water_bytes"]);
    assert_eq!(default_prune["freed_bytes"], 0);

    let scoped = report(run(
        &root,
        &[
            "prune",
            "--target",
            "1KiB",
            "--model-identity",
            "model-a",
            "--yes",
        ],
    ));
    assert!(scoped["freed_bytes"].as_u64().unwrap() > 0);
    assert_eq!(scoped["target_reached"], false);
    let status = report(run(&root, &["status"]));
    assert_eq!(status["usage"]["manifests"], 2);
    assert_eq!(status["models"][0]["model_identity"], "model-a-other");

    let scoped = report(run(
        &root,
        &["clear", "--model-identity", "model-a-other", "--yes"],
    ));
    assert!(scoped["freed_bytes"].as_u64().unwrap() > 0);
    let prune = report(run(&root, &["prune", "--target", "1KiB", "--yes"]));
    assert!(prune["freed_bytes"].as_u64().unwrap() > 0);
    assert_eq!(prune["target_reached"], true);
    let owner = L3CacheManager::acquire(&root, StoreLimits::new(BUDGET, RESERVE)).unwrap();
    seed(&owner, "model-b", 4);
    drop(owner);
    let clear = report(run(&root, &["clear", "--yes"]));
    assert!(clear["freed_bytes"].as_u64().unwrap() > 0);
    assert_eq!(report(run(&root, &["status"]))["models"], json!([]));
}

#[test]
fn independent_cli_refuses_to_mutate_a_store_with_an_unreachable_owner() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("kv");
    let owner = L3CacheManager::acquire(&root, StoreLimits::new(BUDGET, RESERVE)).unwrap();
    seed(&owner, "model-a", 1);
    let status = report(run(&root, &["status"]));
    assert_eq!(status["enabled"], true);
    assert_eq!(status["available"], false);
    assert_eq!(status["state"], "unavailable");
    assert!(status["detail"].as_str().unwrap().contains("already owned"));
    let auto = report(run(&root, &["status", "--kv-cache-disk", "auto"]));
    assert_eq!(auto["state"], "unavailable");
    assert!(auto["detail"].as_str().unwrap().contains("already owned"));
    let output = run(&root, &["clear", "--yes"]);
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("stop the skippy serve process"));
    assert_eq!(owner.inventory().unwrap().len(), 1);
}

#[test]
fn offline_status_reports_low_space_and_startup_reconciliation() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("kv");
    let owner = L3CacheManager::acquire(&root, StoreLimits::new(BUDGET, RESERVE)).unwrap();
    drop(owner);
    fs::write(root.join("segments/.tmp-dead"), b"interrupted").unwrap();
    let report = report(run(&root, &["status", "--kv-cache-min-free", "1024TiB"]));
    assert_eq!(report["state"], "read_only_low_space");
    assert_eq!(report["available"], true);
    assert_eq!(report["reconciliation"]["removed_temporary_files"], 1);
}

struct CacheOwner(Child);

impl Drop for CacheOwner {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[test]
fn replacement_reconciles_a_killed_owner_and_reloads_its_committed_state() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("kv");
    let mut owner = CacheOwner(
        Command::new(std::env::current_exe().unwrap())
            .args(["--ignored", "--exact", "hold_crash_fixture"])
            .env("SKIPPY_TEST_L3_OWNER_ROOT", &root)
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .spawn()
            .unwrap(),
    );
    let ready = temporary.path().join("owner-ready");
    let deadline = Instant::now() + Duration::from_secs(10);
    while !ready.exists() {
        assert!(
            owner.0.try_wait().unwrap().is_none(),
            "cache owner exited before readiness"
        );
        assert!(
            Instant::now() < deadline,
            "cache owner did not report readiness"
        );
        thread::sleep(Duration::from_millis(10));
    }

    let blocked = report(run(&root, &["status", "--kv-cache-disk", "auto"]));
    assert_eq!(blocked["state"], "unavailable");
    assert!(
        blocked["detail"]
            .as_str()
            .unwrap()
            .contains("already owned")
    );
    assert!(root.join("segments/.tmp-abandoned").exists());
    owner.0.kill().unwrap();
    assert!(!owner.0.wait().unwrap().success());

    let recovered = report(run(&root, &["status"]));
    assert_eq!(recovered["reconciliation"]["removed_temporary_files"], 1);
    assert_eq!(recovered["usage"]["manifests"], 1);
    assert!(!root.join("segments/.tmp-abandoned").exists());
    let replacement = L3CacheManager::acquire(&root, StoreLimits::new(BUDGET, RESERVE)).unwrap();
    let tier = replacement.tier_for_model("model-a".into(), "model-a".into(), 64 * 1024);
    let fill = tier
        .fill_longest("model-a", &[1, 2, 3], 8)
        .unwrap()
        .unwrap();
    assert_eq!(
        fill.payload.full_state_bytes_timed().unwrap().0.as_ref(),
        &[42; 32 * 1024]
    );
}

#[test]
#[ignore = "spawned explicitly by the cache owner crash-recovery test"]
fn hold_crash_fixture() {
    let root = std::path::PathBuf::from(std::env::var_os("SKIPPY_TEST_L3_OWNER_ROOT").unwrap());
    let owner = L3CacheManager::acquire(&root, StoreLimits::new(BUDGET, RESERVE)).unwrap();
    seed(&owner, "model-a", 42);
    fs::write(root.join("segments/.tmp-abandoned"), b"uncommitted state").unwrap();
    fs::write(root.parent().unwrap().join("owner-ready"), []).unwrap();
    let mut line = String::new();
    std::io::stdin().read_line(&mut line).unwrap();
    drop(owner);
}
