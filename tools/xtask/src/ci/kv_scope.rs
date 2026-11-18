use super::{DynResult, execution, profile::Profile};
use serde_json::Value;
use std::{env, fs};

pub(super) fn required(profile: Profile) -> bool {
    profile == Profile::Full || changed().unwrap_or(true)
}

fn changed() -> DynResult<bool> {
    let event: Value = serde_json::from_slice(&fs::read(
        env::var_os("GITHUB_EVENT_PATH").ok_or("missing GitHub event")?,
    )?)?;
    let name = env::var("GITHUB_EVENT_NAME")?;
    let base = match name.as_str() {
        "pull_request" => event["pull_request"]["base"]["sha"].as_str(),
        "merge_group" => event["merge_group"]["base_sha"].as_str(),
        "push" => event["before"].as_str(),
        _ => None,
    }
    .filter(|sha| sha.len() == 40 && sha.bytes().all(|b| b.is_ascii_hexdigit()))
    .ok_or("missing usable comparison base")?;
    let changes = execution::capture(
        "git",
        &execution::strings(&[
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            base,
            "HEAD",
            "--",
        ]),
        &[],
    )?;
    Ok(changes.split_terminator('\0').any(affects_cache))
}

fn affects_cache(path: &str) -> bool {
    !matches!(
        path,
        "README.md"
            | "DESIGN.md"
            | "PRODUCT.md"
            | "RELEASE.md"
            | "ci/README.md"
            | "assets/skippy-logo.svg"
    ) && !path.starts_with("docs-site/")
        && !path.starts_with("docs/")
}

#[test]
fn only_known_documentation_paths_can_skip_the_gpu_gate() {
    assert!(!affects_cache("docs/PROMPT_CACHE.md"));
    for path in [
        "Cargo.lock",
        "Justfile",
        "ci/cache-qualification/workload.schema.json",
        "crates/skippy-serving/src/cache.rs",
        "llama_cpp/patches/0001.patch",
        "unexpected.new",
        ".github/workflows/ci.yml",
    ] {
        assert!(affects_cache(path), "{path}");
    }
}
