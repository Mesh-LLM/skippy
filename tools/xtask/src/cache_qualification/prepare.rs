use super::{Backend, execution, existing_input, strings};
use crate::command::DynResult;
use std::{
    env, fs,
    path::{Path, PathBuf},
};

pub(super) struct Builds {
    pub(super) reference: PathBuf,
    pub(super) native_build: PathBuf,
}

pub(super) fn build(backend: Backend, output: &Path) -> DynResult<Builds> {
    let mut environment = vec![
        ("LLAMA_STAGE_BACKEND", backend.runtime_backend().to_owned()),
        ("LLAMA_STAGE_LINK_MODE", "static".into()),
    ];
    if matches!(backend, Backend::Cuda | Backend::CudaArm64) {
        let matrix = crate::ci::matrix::Matrix::load()?;
        let runtime = matrix.runtime(if matches!(backend, Backend::CudaArm64) {
            "linux-aarch64-cuda13"
        } else {
            "linux-x86_64-cuda13"
        })?;
        environment.push(("SKIPPY_CUDA_VERSION", runtime.toolkit.clone()));
        environment.push(("LLAMA_STAGE_CUDA_ARCHITECTURES", "native".into()));
    }
    let native_build = if env::var_os("LLAMA_STAGE_BUILD_DIR").is_some() {
        existing_input("LLAMA_STAGE_BUILD_DIR", Path::is_dir)?
    } else {
        execution::run("just", &strings(&["llama-build"]), &environment, 7200)?;
        let directory = execution::capture(
            "just",
            &strings(&["cache-correctness-native-dir"]),
            &environment,
        )?;
        Path::new(directory.trim()).canonicalize()?
    };
    let reference = if env::var_os("SKIPPY_CACHE_QUAL_LLAMA_SERVER").is_some() {
        existing_input("SKIPPY_CACHE_QUAL_LLAMA_SERVER", Path::is_file)?
    } else {
        if env::var_os("LLAMA_STAGE_BUILD_DIR").is_some() {
            execution::run("just", &strings(&["llama-prepare"]), &environment, 600)?;
        }
        build_reference(backend, output, &environment)?
    };
    Ok(Builds {
        reference,
        native_build,
    })
}

fn build_reference(
    backend: Backend,
    output: &Path,
    environment: &[(&str, String)],
) -> DynResult<PathBuf> {
    let pin = fs::read_to_string("llama_cpp/upstream.txt")?;
    let pin = pin.trim();
    if pin.len() != 40 || !pin.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("invalid llama.cpp upstream pin".into());
    }
    let workdir = env::var_os("LLAMA_WORKDIR")
        .map_or_else(|| PathBuf::from(".deps/llama.cpp"), PathBuf::from);
    let archive = output.join("reference-source.tar");
    execution::run(
        "git",
        &strings(&[
            "-C",
            &workdir.to_string_lossy(),
            "archive",
            "--format=tar",
            "--output",
            &archive.to_string_lossy(),
            pin,
        ]),
        &[],
        600,
    )?;
    let source = output.join("reference-source");
    fs::create_dir(&source)?;
    tar::Archive::new(fs::File::open(&archive)?).unpack(&source)?;
    fs::remove_file(archive)?;
    let build = output.join("reference-build");
    let mut command = strings(&[
        "cache-correctness-reference",
        &source.to_string_lossy(),
        &build.to_string_lossy(),
        pin,
    ]);
    match backend {
        Backend::Metal => command.extend(strings(&["-DGGML_METAL=ON"])),
        Backend::Cuda | Backend::CudaArm64 => command.extend(strings(&[
            "-DGGML_CUDA=ON",
            "-DCMAKE_CUDA_ARCHITECTURES=native",
        ])),
    }
    execution::run("just", &command, environment, 7200)?;
    Ok(build.join("bin/llama-server").canonicalize()?)
}
