use super::{Backend, execution, existing_input, strings};
use crate::command::DynResult;
use std::{
    env,
    path::{Path, PathBuf},
};

pub(super) fn build_native(backend: Backend) -> DynResult<PathBuf> {
    let mut environment = vec![
        ("LLAMA_STAGE_BACKEND", backend.runtime_backend().to_owned()),
        ("LLAMA_STAGE_LINK_MODE", "static".into()),
    ];
    environment.extend(native_environment(backend)?);
    if env::var_os("LLAMA_STAGE_BUILD_DIR").is_some() {
        return existing_input("LLAMA_STAGE_BUILD_DIR", Path::is_dir);
    }
    execution::run("just", &strings(&["llama-build"]), &environment, 7200)?;
    let directory = execution::capture(
        "just",
        &strings(&["cache-correctness-native-dir"]),
        &environment,
    )?;
    Ok(Path::new(directory.trim()).canonicalize()?)
}

pub(super) fn diagnostic_reference() -> DynResult<Option<PathBuf>> {
    env::var_os("SKIPPY_CACHE_QUAL_LLAMA_SERVER")
        .map(|_| existing_input("SKIPPY_CACHE_QUAL_LLAMA_SERVER", Path::is_file))
        .transpose()
}

fn native_environment(backend: Backend) -> DynResult<Vec<(&'static str, String)>> {
    let variant = match backend {
        Backend::Metal => return Ok(Vec::new()),
        Backend::Cuda => "linux-x86_64-cuda13",
        Backend::CudaArm64 => "linux-aarch64-cuda13",
    };
    let plan = execution::output("just", &strings(&["ci-plan"]))?;
    let plan: serde_json::Value = serde_json::from_str(&plan)?;
    let runtimes = plan["runtimes"]["include"]
        .as_array()
        .ok_or("CI plan omitted runtimes")?;
    let toolkit = runtimes
        .iter()
        .find(|row| row["id"] == variant)
        .and_then(|row| row["toolkit"].as_str())
        .filter(|value| !value.is_empty())
        .ok_or("CI plan omitted the CUDA toolkit")?;
    Ok(vec![
        ("SKIPPY_CUDA_VERSION", toolkit.to_owned()),
        ("LLAMA_STAGE_CUDA_ARCHITECTURES", "native".into()),
    ])
}
