use super::{
    artifacts,
    execution::{run, strings},
    matrix::{Host, Matrix, Runtime, require_host},
};
use crate::command::DynResult;
use std::{fs, path::Path};

pub fn host(id: &str) -> DynResult<()> {
    let matrix = Matrix::load()?;
    let host = matrix.host(id)?;
    require_host(host)?;
    run("just", &strings(&["skippy-cli-release-build"]), &[], 7200)?;
    let directory = tempfile::tempdir()?;
    let binary = artifacts::binary(directory.path(), &host.os);
    fs::copy(
        artifacts::binary(Path::new("target/release"), &host.os),
        &binary,
    )?;
    crate::native::verify_host(&strings(&[
        &binary.to_string_lossy(),
        "--bind-sha256",
        "--report",
        &directory.path().join("host-imports.json").to_string_lossy(),
    ]))?;
    artifacts::receipt(directory.path(), "host", &host.target, "")?;
    let output = Path::new("dist/hosts").join(id);
    fs::create_dir_all(&output)?;
    artifacts::archive(directory.path(), &output.join("host.tar.gz"))
}

pub fn runtime(id: &str) -> DynResult<()> {
    let matrix = Matrix::load()?;
    let row = matrix.runtime(id)?;
    let host = matrix.host(&row.host)?;
    require_host(host)?;
    let environment = runtime_environment(row);
    if row.backend == "cuda" {
        let detected = super::execution::capture("nvcc", &strings(&["--version"]), &[])?;
        let expected = format!(
            "release {}",
            row.toolkit
                .rsplit_once('.')
                .ok_or("invalid CUDA toolkit")?
                .0
        );
        if !detected.contains(&expected) {
            return Err(format!("expected CUDA {}, found {detected}", row.toolkit).into());
        }
    }
    run(
        "just",
        &strings(&["release-runtime-build", &row.backend, &host.target]),
        &environment,
        10800,
    )?;
    let runtime = fs::read_dir("dist/native-runtimes")?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.join("manifest.json").is_file())
        .collect::<Vec<_>>();
    if runtime.len() != 1 {
        return Err("native producer must emit exactly one runtime".into());
    }
    validate_native_identity(&runtime[0], host, row)?;
    run(
        "just",
        &strings(&[
            "automation-run",
            "native",
            "verify-runtime-package",
            &runtime[0].to_string_lossy(),
        ]),
        &[],
        180,
    )?;
    artifacts::receipt(&runtime[0], "runtime", &host.target, id)?;
    let output = Path::new("dist/runtimes").join(id);
    fs::create_dir_all(&output)?;
    artifacts::archive(&runtime[0], &output.join("runtime.tar.gz"))
}

fn runtime_environment(row: &Runtime) -> Vec<(&'static str, String)> {
    let mut environment = vec![
        ("LLAMA_STAGE_BACKEND", row.backend.clone()),
        ("LLAMA_STAGE_BUILD_TESTS", "OFF".into()),
        ("LLAMA_STAGE_FULL_REPLAY", "OFF".into()),
        ("LLAMA_STAGE_UPSTREAM_TESTS", "OFF".into()),
    ];
    if !row.toolkit.is_empty() {
        environment.push(("SKIPPY_CUDA_VERSION", row.toolkit.clone()));
        environment.push((
            "SKIPPY_CUDA_TOOLKIT_MAJOR",
            row.toolkit.split('.').next().unwrap_or_default().into(),
        ));
        environment.push((
            "LLAMA_STAGE_CUDA_ARCHITECTURES",
            row.gpu_architectures.clone(),
        ));
    }
    environment
}

pub fn validate_native_identity(directory: &Path, host: &Host, row: &Runtime) -> DynResult<()> {
    let document: serde_json::Value =
        serde_json::from_slice(&fs::read(directory.join("manifest.json"))?)?;
    let manifest = &document["runtime"];
    if manifest["platform"]["os"] != host.os
        || manifest["platform"]["arch"] != host.arch
        || manifest["backend"]["kind"] != row.backend
    {
        return Err("native manifest platform/backend does not match matrix row".into());
    }
    if row.backend == "cuda" {
        let major = row
            .toolkit
            .split('.')
            .next()
            .ok_or("missing toolkit major")?
            .parse::<u64>()?;
        if manifest["backend"]["cuda"]["toolkit_major"].as_u64() != Some(major) {
            return Err("CUDA manifest toolkit mismatch".into());
        }
    }
    Ok(())
}

pub fn package(id: &str, host_input: &Path, runtime_input: &Path) -> DynResult<()> {
    let matrix = Matrix::load()?;
    let row = matrix.runtime(id)?;
    let host = matrix.host(&row.host)?;
    require_host(host)?;
    let workspace = tempfile::tempdir()?;
    let host_dir = workspace.path().join("host");
    let runtime_dir = workspace.path().join("runtime");
    artifacts::unpack(host_input, &host_dir)?;
    artifacts::unpack(runtime_input, &runtime_dir)?;
    artifacts::verify(&host_dir, "host", &host.target, "")?;
    artifacts::verify(&runtime_dir, "runtime", &host.target, id)?;
    validate_native_identity(&runtime_dir, host, row)?;
    let product = workspace.path().join("product");
    fs::create_dir_all(&product)?;
    artifacts::copy_tree(&host_dir, &product)?;
    fs::remove_file(product.join("provenance.json"))?;
    artifacts::copy_tree(&runtime_dir, &product.join("native-runtimes").join(id))?;
    for license in ["LICENSE", "NOTICE"] {
        if Path::new(license).is_file() {
            fs::copy(license, product.join(license))?;
        }
    }
    artifacts::receipt(&product, "product", &host.target, id)?;
    run(
        &artifacts::binary(&product, &host.os).to_string_lossy(),
        &strings(&["--version"]),
        &[],
        60,
    )?;
    let output = Path::new("dist/products").join(id);
    fs::create_dir_all(&output)?;
    artifacts::archive(&product, &output.join(format!("skippy-{id}.tar.gz")))
}
