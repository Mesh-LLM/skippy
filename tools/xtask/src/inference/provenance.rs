use super::*;
use crate::{artifact, execution};
use serde_json::json;
use std::fs;
pub fn record(output: &Path, sha: &str) -> DynResult<()> {
    let source = fs::read_to_string("crates/skippy-ffi/src/lib.rs")?;
    let mut version = Vec::new();
    for name in ["MAJOR", "MINOR", "PATCH"] {
        let prefix = format!("pub const ABI_VERSION_{name}:");
        let value = source
            .lines()
            .find(|line| line.starts_with(&prefix))
            .and_then(|line| line.split('=').nth(1))
            .ok_or("native ABI source constant missing")?
            .trim()
            .trim_end_matches(';')
            .parse::<u32>()?;
        version.push(value);
    }
    let native = execution::capture(
        "git",
        &[
            "-C".into(),
            ".deps/llama.cpp".into(),
            "rev-parse".into(),
            "HEAD^{tree}".into(),
        ],
        &[],
    )?;
    let cache = fs::read_dir(".deps/llama-build")?
        .filter_map(Result::ok)
        .map(|entry| entry.path().join("CMakeCache.txt"))
        .find(|path| path.is_file())
        .ok_or("native CUDA build cache missing")?;
    let cache_text = fs::read_to_string(&cache)?;
    let compiler = cache_text
        .lines()
        .find(|line| line.starts_with("CMAKE_CUDA_COMPILER:"))
        .and_then(|line| line.split_once('=').map(|(_, value)| value))
        .ok_or("native CUDA compiler missing from build cache")?;
    write_json_file(
        &output.join("provenance.json"),
        &json!({
            "stack_commit":sha,"native_upstream":fs::read_to_string("llama_cpp/upstream.txt")?.trim(),
            "native_tree":native.trim(),"native_abi":version,
            "cli_sha256":artifact::sha256(Path::new("target/debug/skippy"))?,
            "correctness_sha256":artifact::sha256(Path::new("target/debug/skippy-correctness"))?,
            "model_manifest_sha256":artifact::sha256(Path::new("ci/inference-artifacts.json"))?,
            "client_lock_sha256":artifact::sha256(Path::new("ci/aiperf-requirements.lock"))?,
            "harness":"aiperf-0.13.0-python-3.12",
            "cuda_compiler":execution::capture(compiler,&["--version".into()],&[])?
        }),
    )
}
