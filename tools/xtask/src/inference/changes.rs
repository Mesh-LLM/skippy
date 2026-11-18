//! PR input selection for the protected inference controller.
use crate::command::DynResult;
use serde_json::Value;

pub(super) fn requires_qualification(pages: &Value) -> DynResult<bool> {
    let pages = pages
        .as_array()
        .filter(|p| !p.is_empty())
        .ok_or("invalid files pages")?;
    let mut required = false;
    let mut count = 0;
    for page in pages {
        let files = page.as_array().ok_or("invalid files response")?;
        count += files.len();
        for file in files {
            let path = file["filename"]
                .as_str()
                .ok_or("missing changed-file path")?;
            required |= relevant(path);
            if let Some(previous) = file.get("previous_filename") {
                required |= relevant(previous.as_str().ok_or("invalid previous file path")?);
            }
        }
    }
    // GitHub's PR-files endpoint exposes at most 3,000 files, even with pagination.
    if count >= 3_000 {
        return Err("PR file list may be truncated; cannot determine inference inputs".into());
    }
    Ok(required)
}

fn relevant(path: &str) -> bool {
    let name = path.rsplit('/').next().unwrap_or(path).to_ascii_lowercase();
    if path.split('/').any(|part| matches!(part, "docs" | "doc"))
        || name.starts_with("readme")
        || [".md", ".mdx", ".rst", ".adoc"]
            .iter()
            .any(|ext| name.ends_with(ext))
    {
        return false;
    }
    // Product dependencies, native sources and the harness used by JY jobs.
    if [
        "crates/",
        "llama_cpp/",
        "skippy_api/",
        ".cargo/",
        "tools/process-supervision/",
        "tools/xtask/src/inference/",
        "tools/xtask/src/native/",
        "tools/xtask/src/artifact/",
        "tools/xtask/src/execution/",
        "scripts/cargo-linker",
        "scripts/lib/",
    ]
    .iter()
    .any(|prefix| path.starts_with(prefix))
    {
        return true;
    }
    matches!(
        path,
        "Cargo.toml"
            | "Cargo.lock"
            | "Justfile"
            | "justfile"
            | "rust-toolchain"
            | "rust-toolchain.toml"
            | "tools/xtask/Cargo.toml"
            | "tools/xtask/src/main.rs"
            | "tools/xtask/src/inference.rs"
            | "tools/xtask/src/command.rs"
            | "tools/xtask/src/artifact.rs"
            | "tools/xtask/src/execution.rs"
            | "scripts/build-llama.sh"
            | "scripts/prepare-llama.sh"
            | "scripts/prepare-whisper.sh"
            | ".gi/templates.yaml"
            | ".github/workflows/inference.yml"
            | ".github/actions/setup/action.yml"
            | ".github/actions/cache/action.yml"
            | ".github/actions/sccache/action.yml"
            | "ci/inference-ladder.json"
            | "ci/inference-artifacts.json"
            | "ci/inference-quality.json"
            | "ci/inference-corpus.json"
            | "ci/aiperf-requirements.lock"
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn documentation_and_repository_metadata_skip_native_jobs() {
        for path in [
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/ISSUE_TEMPLATE/bug.yml",
            ".github/CODEOWNERS",
            ".github/workflows/release.yml",
            "README.md",
            "docs/design/plan.md",
            "ci/README.md",
            "crates/skippy-serving/README.md",
            "crates/skippy-serving/AGENTS.md",
            "crates/skippy-serving/docs/example.rs",
            "llama_cpp/README.MD",
            "skippy_api/docs/TYPE_DESIGN.md",
            "scripts/recipes/quantization/README.md",
            "tools/process-supervision/README.md",
            "tools/xtask/src/release/mod.rs",
            "scripts/update-llama-pin.sh",
            "ci/targets.json",
        ] {
            assert!(!relevant(path), "{path}");
            assert!(
                !requires_qualification(&json!([[{"filename":path}]])).unwrap(),
                "{path}"
            );
        }
    }

    #[test]
    fn serving_native_build_and_harness_inputs_require_qualification() {
        for path in [
            "Cargo.toml",
            "Cargo.lock",
            "Justfile",
            "justfile",
            "rust-toolchain.toml",
            ".cargo/config.toml",
            "crates/skippy-serving/src/lib.rs",
            "crates/skippy-runtime/Cargo.toml",
            "crates/skippy-ffi/build.rs",
            "llama_cpp/upstream.txt",
            "llama_cpp/patches/model_support/series",
            "llama_cpp/patches/generated/0001-family-qwen.patch",
            "skippy_api/src/llama/staged.cpp",
            "skippy_api/include/skippy.h",
            "skippy_api/CMakeLists.txt",
            "scripts/build-llama.sh",
            "scripts/prepare-whisper.sh",
            "scripts/lib/macos-deployment-target.txt",
            "scripts/cargo-linker-linux-aarch64",
            "tools/xtask/Cargo.toml",
            "tools/xtask/src/artifact.rs",
            "tools/xtask/src/inference/execute.rs",
            "tools/xtask/src/inference.rs",
            "tools/xtask/src/native/mod.rs",
            "tools/process-supervision/src/lib.rs",
            ".gi/templates.yaml",
            ".github/workflows/inference.yml",
            ".github/actions/setup/action.yml",
            "ci/inference-ladder.json",
            "ci/inference-artifacts.json",
            "ci/inference-quality.json",
            "ci/inference-corpus.json",
            "ci/aiperf-requirements.lock",
        ] {
            assert!(
                requires_qualification(&json!([[{"filename":path}]])).unwrap(),
                "{path}"
            );
        }
    }

    #[test]
    fn renames_and_deletions_retain_both_input_paths() {
        for file in [
            json!({"filename":"docs/archived.md","previous_filename":"ci/inference-ladder.json"}),
            json!({"filename":"crates/skippy-serving/src/lib.rs","previous_filename":"docs/example.rs"}),
            json!({"filename":"llama_cpp/patches/a.patch","status":"removed"}),
        ] {
            assert!(requires_qualification(&json!([[file]])).unwrap());
        }
        assert!(
            !requires_qualification(&json!([[{
                "filename":"docs/template.md","previous_filename":".github/PULL_REQUEST_TEMPLATE.md"
            }]]))
            .unwrap()
        );
    }

    #[test]
    fn all_pages_are_checked_without_qualifying_large_docs_changes() {
        let page = vec![json!({"filename":"docs/typo.md"}); 100];
        assert!(!requires_qualification(&json!([page])).unwrap());
        assert!(!requires_qualification(&json!([page, [{"filename":"ci/README.md"}]] )).unwrap());
        assert!(
            requires_qualification(
                &json!([page, [{"filename":"crates/skippy-serving/src/lib.rs"}]] )
            )
            .unwrap()
        );
        assert!(!requires_qualification(&json!([[]])).unwrap());
    }

    #[test]
    fn malformed_or_truncated_file_lists_fail_closed() {
        for pages in [
            Value::Null,
            json!([]),
            json!([null]),
            json!([[{}]]),
            json!([[{
                "filename":"docs/typo.md", "previous_filename":null
            }]]),
        ] {
            assert!(requires_qualification(&pages).is_err());
        }
        let page = vec![json!({"filename":"docs/typo.md"}); 100];
        assert!(requires_qualification(&json!(vec![page; 30])).is_err());
    }
}
