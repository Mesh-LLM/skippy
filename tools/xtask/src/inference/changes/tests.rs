use super::*;
use serde_json::json;
mod configuration;
mod contracts;
use std::collections::BTreeMap;

#[derive(Default)]
struct Files(BTreeMap<(String, String), String>);
impl Source for Files {
    fn read(&mut self, revision: &str, path: &str) -> DynResult<Option<String>> {
        Ok(self.0.get(&(revision.into(), path.into())).cloned())
    }
}
impl Files {
    fn set(&mut self, revision: &str, path: &str, text: &str) {
        self.0.insert((revision.into(), path.into()), text.into());
    }
    fn both(&mut self, path: &str, text: &str) {
        for rev in ["base", "head"] {
            self.set(rev, path, text);
        }
    }
}

fn fixture() -> Files {
    let mut files = Files::default();
    files.both("Cargo.toml", "[workspace]\nresolver='2'\n");
    files.both("Cargo.lock", "version=4\npackage=[]\n");
    files.both(
        "crates/skippy-cli/Cargo.toml",
        "[package]\nname='skippy-cli'\nversion='1.0.0'\n",
    );
    files.both("crates/skippy-cli/src/main.rs", "fn main() {}\n");
    files.both(
        "tools/xtask/Cargo.toml",
        "[package]\nname='xtask'\nversion='1.0.0'\n",
    );
    for path in dependencies::WORKER_FILES {
        files.both(path, "");
    }
    files
}

fn decision(files: &mut Files, path: &str) -> Decision {
    assess(&json!([[{"filename":path}]]), files, "base", "head").unwrap()
}

#[test]
fn pr29_agents_reporting_and_unrelated_tools_do_not_require_gpus() {
    let mut files = fixture();
    for path in [
        ".gi/agents/code-review.yaml",
        ".gi/templates.yaml.bak",
        "ci/README.md",
        "tools/xtask/src/inference/changes.rs",
        "tools/xtask/src/inference/changes/mod.rs",
        "tools/xtask/src/inference/admission.rs",
        "tools/xtask/src/inference/comment.rs",
        "tools/xtask/src/inference/throughput.rs",
        "tools/xtask/src/native/package.rs",
        "crates/skippy-quantize/src/lib.rs",
        "crates/metrics-server/src/main.rs",
        "crates/skippy-cli/tests/startup.rs",
        "crates/skippy-cli/examples/demo.rs",
        ".github/workflows/inference.yml",
        "ci/aiperf-requirements.lock",
    ] {
        assert!(decision(&mut files, path).reasons.is_empty(), "{path}");
    }
}

#[test]
fn inline_and_nested_test_edits_do_not_change_production_but_runtime_edits_do() {
    let path = "crates/skippy-cli/src/main.rs";
    let mut files = fixture();
    files.set(
        "base",
        path,
        "fn main() { let n = 1; } #[cfg(test)] mod tests { fn check() { panic!(\"old\") } }",
    );
    files.set("head", path, "/// docs\nfn main() { let n = 1; } #[cfg(test)] mod tests { fn check() { panic!(\"new\") } }");
    assert!(decision(&mut files, path).reasons.is_empty());
    files.set(
        "head",
        path,
        "fn main() { let n = 2; } #[cfg(test)] mod tests { fn check() {} }",
    );
    assert!(!decision(&mut files, path).reasons.is_empty());
    assert_eq!(
        rust::projection("fn main() { #[cfg(test)] fn check() {} }").unwrap(),
        rust::projection("fn main() {}").unwrap()
    );
}

#[test]
fn test_modules_are_excluded_by_cfg_rather_than_a_filename_guess() {
    let mut files = fixture();
    files.both(
        "crates/skippy-cli/src/main.rs",
        "#[cfg(test)] mod tests; mod requests; fn main() {}",
    );
    files.both("crates/skippy-cli/src/requests.rs", "pub fn serve() {}");
    assert!(
        decision(&mut files, "crates/skippy-cli/src/tests.rs")
            .reasons
            .is_empty()
    );
    files.set(
        "head",
        "crates/skippy-cli/src/requests.rs",
        "pub fn serve() { panic!(); }",
    );
    assert!(
        !decision(&mut files, "crates/skippy-cli/src/requests.rs")
            .reasons
            .is_empty()
    );
}

#[test]
fn literals_are_preserved_and_unknown_cfg_is_not_assumed_to_be_test_only() {
    assert_ne!(
        rust::projection("const PROMPT: &str = \"hello world\";").unwrap(),
        rust::projection("const PROMPT: &str = \"helloworld\";").unwrap()
    );
    assert!(
        !rust::projection("#[cfg(any(test, feature=\"cuda\"))] fn gpu() {}")
            .unwrap()
            .is_empty()
    );
    assert!(
        rust::projection("#[cfg(all(test, feature=\"cuda\"))] fn test() {}")
            .unwrap()
            .is_empty()
    );
    assert!(
        rust::projection("#![cfg(test)] fn only_tests() {}")
            .unwrap()
            .is_empty()
    );
    assert!(rust::projection("fn broken(").is_err());
}

#[test]
fn renames_deletions_and_later_pages_keep_runtime_changes_required() {
    let mut files = fixture();
    let path = "crates/skippy-cli/src/main.rs";
    files.set("head", path, "fn main() { panic!(); }");
    let page = vec![json!({"filename":"README.md"}); 100];
    let pages = json!([page, [{"filename":"docs/archived.md", "previous_filename":path}]]);
    assert!(
        !assess(&pages, &mut files, "base", "head")
            .unwrap()
            .reasons
            .is_empty()
    );
    let pages = json!([[{"filename":path,"status":"removed"}]]);
    assert!(
        !assess(&pages, &mut files, "base", "head")
            .unwrap()
            .reasons
            .is_empty()
    );
}

#[test]
fn malformed_paths_and_truncated_lists_fail_admission() {
    for pages in [
        Value::Null,
        json!([]),
        json!([null]),
        json!([[{}]]),
        json!([[{"filename":"README.md","previous_filename":null}]]),
        json!([[{"filename":"../outside.rs"}]]),
    ] {
        assert!(paths(&pages).is_err());
    }
    let page = vec![json!({"filename":"README.md"}); 100];
    assert!(paths(&json!(vec![page; 30])).is_err());
}

#[test]
fn runtime_and_build_inputs_still_require_qualification() {
    let mut files = fixture();
    for path in [
        "llama_cpp/upstream.txt",
        "llama_cpp/patches/model_support/series",
        "skippy_api/src/llama/staged.cpp",
        "skippy_api/include/skippy.h",
        "scripts/build-llama.sh",
        "scripts/cargo-linker-linux-aarch64",
    ] {
        assert!(!decision(&mut files, path).reasons.is_empty(), "{path}");
    }
    files.set(
        "head",
        "tools/xtask/src/inference/execute.rs",
        "fn execute() { panic!(); }",
    );
    assert!(
        !decision(&mut files, "tools/xtask/src/inference/execute.rs")
            .reasons
            .is_empty()
    );
    files.both("ci/inference-quality.json", "{\"expected\":\"old\"}");
    files.set(
        "head",
        "ci/inference-quality.json",
        "{\"expected\":\"new\"}",
    );
    assert!(
        !decision(&mut files, "ci/inference-quality.json")
            .reasons
            .is_empty()
    );
}

#[test]
fn lockfile_ignores_unrelated_packages_but_tracks_the_runtime_transitive_closure() {
    let mut files = fixture();
    for rev in ["base", "head"] {
        let original = files
            .read(rev, "crates/skippy-cli/Cargo.toml")
            .unwrap()
            .unwrap();
        files.set(
            rev,
            "crates/skippy-cli/Cargo.toml",
            &format!("{original}[dependencies]\nserde='1'\n"),
        );
    }
    let lock = "version=4\n[[package]]\nname='serde'\nversion='1.0.0'\ndependencies=['serde_core']\n[[package]]\nname='serde_core'\nversion='1.0.0'\nchecksum='old'\n[[package]]\nname='unused'\nversion='1.0.0'\nchecksum='old'\n";
    files.both("Cargo.lock", lock);
    files.set(
        "head",
        "Cargo.lock",
        &lock.replace(
            "name='unused'\nversion='1.0.0'\nchecksum='old'",
            "name='unused'\nversion='2.0.0'\nchecksum='new'",
        ),
    );
    assert!(decision(&mut files, "Cargo.lock").reasons.is_empty());
    files.set(
        "head",
        "Cargo.lock",
        &lock.replace(
            "name='serde_core'\nversion='1.0.0'\nchecksum='old'",
            "name='serde_core'\nversion='1.0.0'\nchecksum='new'",
        ),
    );
    assert!(!decision(&mut files, "Cargo.lock").reasons.is_empty());
}

#[test]
fn controller_routing_changes_do_not_change_the_candidate_worker_projection() {
    let original = "fn run(args: &[&str]) { match args { [\"comment\"] => report(), [\"execute\"] => serve(), _ => help() } } fn plan() {}";
    assert_eq!(
        rust::planner_projection(original).unwrap(),
        rust::planner_projection(&original.replace("report()", "other_report()")).unwrap()
    );
    assert_ne!(
        rust::planner_projection(original).unwrap(),
        rust::planner_projection(&original.replace("serve()", "other_serve()")).unwrap()
    );
}

#[test]
fn actual_repository_dependency_and_module_graph_resolves() {
    struct Checkout;
    impl Source for Checkout {
        fn read(&mut self, _: &str, path: &str) -> DynResult<Option<String>> {
            let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
            match std::fs::read_to_string(root.join(path)) {
                Ok(text) => Ok(Some(text)),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
                Err(error) => Err(error.into()),
            }
        }
    }
    let mut checkout = Checkout;
    let graph = dependencies::graph(&mut checkout, "current").unwrap();
    assert!(graph.sources.contains("crates/skippy-runtime/src/lib.rs"));
    assert!(
        graph
            .sources
            .contains("tools/xtask/src/inference/execute/split_plan.rs")
    );
    assert!(
        !graph
            .sources
            .contains("tools/xtask/src/inference/changes/mod.rs")
    );
    assert!(!graph.directories.contains("crates/skippy-quantize"));
    dependencies::lock_projection(&mut checkout, "current", &graph).unwrap();
}
