use super::{Backend, Fixtures, Inputs, TestRunner, execution, prepare, strings};
use crate::{ci::artifacts, command::DynResult};
use serde::Deserialize;
use std::{
    fs,
    path::{Path, PathBuf},
};

const BUNDLE_KIND: &str = "kv-correctness-binaries";
const TEST_BINARY: &str = "skippy-cache-test";
const REFERENCE_BINARY: &str = "llama-server";

pub(super) fn build(backend: Backend, output: &Path) -> DynResult<()> {
    fs::create_dir(output)?;
    let builds = prepare::build(backend, output)?;
    let output = output.canonicalize()?;
    let binary = compile_test(backend, &builds.native_build)?;
    let bundle = tempfile::tempdir()?;
    fs::copy(binary, bundle.path().join(TEST_BINARY))?;
    fs::copy(&builds.reference, bundle.path().join(REFERENCE_BINARY))?;
    artifacts::receipt(bundle.path(), BUNDLE_KIND, backend.profile(), "")?;
    artifacts::archive(bundle.path(), &output.join("kv-binaries.tar.gz"))
}

pub(super) fn inputs(backend: Backend, archive: &Path, directory: &Path) -> DynResult<Inputs> {
    artifacts::unpack(archive, directory)?;
    artifacts::verify(directory, BUNDLE_KIND, backend.profile(), "")?;
    Ok(Inputs {
        fixtures: Fixtures::HuggingFace,
        reference: directory.join(REFERENCE_BINARY),
        runner: TestRunner::Binary(directory.join(TEST_BINARY)),
    })
}

fn compile_test(backend: Backend, native_build: &Path) -> DynResult<PathBuf> {
    let messages = execution::capture_for(
        "just",
        &strings(&[
            "with-lld",
            "cargo",
            "test",
            "--locked",
            "-p",
            "skippy-serving",
            "--lib",
            "--no-run",
            "--message-format=json",
        ]),
        &[
            ("LLAMA_STAGE_BACKEND", backend.runtime_backend().into()),
            (
                "LLAMA_STAGE_BUILD_DIR",
                native_build.to_string_lossy().into_owned(),
            ),
        ],
        1800,
    )?;
    test_executable(&messages)
}

#[derive(Deserialize)]
struct CargoMessage {
    reason: String,
    target: Option<CargoTarget>,
    executable: Option<PathBuf>,
}

#[derive(Deserialize)]
struct CargoTarget {
    name: String,
    kind: Vec<String>,
}

fn test_executable(messages: &str) -> DynResult<PathBuf> {
    let paths = messages
        .lines()
        .filter_map(|line| serde_json::from_str::<CargoMessage>(line).ok())
        .filter(|message| {
            message.reason == "compiler-artifact"
                && message
                    .target
                    .as_ref()
                    .is_some_and(|target| target.name == "skippy_serving" && target.kind == ["lib"])
        })
        .filter_map(|message| message.executable)
        .collect::<Vec<_>>();
    match paths.as_slice() {
        [path] => Ok(path.canonicalize()?),
        _ => Err("expected exactly one skippy-serving test executable".into()),
    }
}
