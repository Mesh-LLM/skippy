#![cfg(test)]
use super::*;
const SHA: &str = "0123456789abcdef0123456789abcdef01234567";

fn ladder() -> Ladder {
    serde_json::from_str(include_str!("../../../../ci/inference-ladder.json")).unwrap()
}

#[test]
fn pr_covers_each_architecture_and_splits_only_large_models() {
    let plan = plan(&ladder(), "pr", SHA).unwrap();
    assert_eq!(plan.rows.len(), 7);
    let architectures: BTreeSet<_> = plan.rows.iter().map(|r| r.architecture.as_str()).collect();
    assert_eq!(architectures.len(), 4);
    assert!(plan.rows.iter().any(|r| r.model == "Qwen/Qwen3.5-4B"));
    assert!(!plan.rows.iter().any(|r| r.model == "Qwen/Qwen3.5-0.8B"));
    assert!(!plan.rows.iter().any(|r| r.id == "qwen35-4b-split"));
    assert_eq!(plan.rows.iter().filter(|r| r.mode == "split").count(), 3);
    for row in &plan.rows {
        assert_eq!(row.machines, if row.mode == "split" { 2 } else { 1 });
    }
    assert_eq!(plan.performance_claim, "smoke-only");
    assert!(
        plan.rows
            .iter()
            .all(|row| row.checks == ["gpu-startup", "scored-quality", "streaming-smoke"])
    );
    assert_eq!(plan.status, "planned-not-executed");
}

#[test]
fn extended_profiles_have_identical_complete_ladders() {
    for profile in ["nightly", "release"] {
        let plan = plan(&ladder(), profile, SHA).unwrap();
        assert_eq!(plan.rows.len(), 12);
        let ids: BTreeSet<_> = plan.rows.iter().map(|r| &r.id).collect();
        assert_eq!(ids.len(), 12);
        let split_ids: BTreeSet<_> = plan
            .rows
            .iter()
            .filter(|r| r.mode == "split")
            .map(|r| r.id.as_str())
            .collect();
        assert_eq!(
            split_ids,
            BTreeSet::from([
                "qwen38-27b-split",
                "qwen3-coder-30b-a3b-split",
                "qwen36-35b-a3b-split",
                "qwen38-flash-next-split",
            ])
        );
        assert_eq!(
            plan.rows.iter().filter(|r| r.mode == "standalone").count(),
            8
        );
    }
}

#[test]
fn invalid_sources_profiles_and_ladders_fail_closed() {
    for sha in [
        "main",
        "pr:1",
        "",
        "$(whoami)",
        "000000000000000000000000000000000000000z",
    ] {
        assert!(plan(&ladder(), "pr", sha).is_err());
    }
    assert!(plan(&ladder(), "quick", SHA).is_err());
    let mut duplicate = ladder();
    duplicate.models.push(duplicate.models[0].clone());
    assert!(plan(&duplicate, "pr", SHA).is_err());
    let mut empty = ladder();
    empty.models.clear();
    assert!(plan(&empty, "pr", SHA).is_err());
    let mut empty_pr = ladder();
    for model in &mut empty_pr.models {
        model.pr = false;
    }
    assert!(plan(&empty_pr, "pr", SHA).is_err());
}
