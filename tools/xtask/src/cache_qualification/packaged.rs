use super::{
    Backend, Fixtures, Inputs, MANIFEST, Report, Status, TestRunner, execution, prepare,
    read_manifest, run_cell, strings, verify, write_summary,
};
use crate::{
    ci::{
        artifacts,
        matrix::{Matrix, require_host},
        producers::validate_native_identity,
    },
    command::DynResult,
};
use serde::Deserialize;
use std::{
    fs,
    path::{Path, PathBuf},
};

const SMOKE_CASE: &str = "frontend::tests::cache_qualification::smoke::packaged_cache_smoke";

pub(super) fn run(variant: &str, profile: &str, input: &Path, output: &Path) -> DynResult<()> {
    let (backend, full) = match (variant, profile) {
        ("darwin-aarch64-metal", "short") => (Backend::Metal, false),
        ("linux-x86_64-cuda13", "short") => (Backend::Cuda, false),
        ("darwin-aarch64-metal", "full") => (Backend::Metal, true),
        ("linux-aarch64-cuda13", "full") => (Backend::CudaArm64, true),
        _ => return Err(format!("unsupported KV profile {profile:?} for {variant:?}").into()),
    };
    let matrix = Matrix::load()?;
    let row = matrix.runtime(variant)?;
    let host = matrix.host(&row.host)?;
    require_host(host)?;
    let temp = tempfile::tempdir()?;
    let product = temp.path().join("product");
    artifacts::unpack(input, &product)?;
    artifacts::verify(&product, "product", &host.target, variant)?;
    let runtime = product.join("native-runtimes");
    let variant_dir = runtime.join(variant);
    validate_native_identity(&variant_dir, host, row, None)?;

    fs::create_dir(output)?;
    let output = output.canonicalize()?;
    artifacts::write_json(
        &output.join("runtime.json"),
        &serde_json::json!({
            "source": artifacts::source()?,
            "variant": variant,
            "profile": profile,
            "manifest_sha256": artifacts::digest(&variant_dir.join("manifest.json"))?
        }),
    )?;
    let binary = compile_test()?;
    let manifest = fs::read_to_string(MANIFEST)?;
    let fixtures = read_manifest(&manifest)?;
    if !full {
        let model = fixtures
            .models
            .iter()
            .find(|model| model.family.as_str() == "dense-attention")
            .ok_or("missing the pinned dense-attention KV fixture")?;
        let fixture = Fixtures::HuggingFace.resolve(model)?;
        let report_dir = output.join(model.family.as_str());
        run_test(
            &binary,
            &runtime,
            SMOKE_CASE,
            vec![
                ("SKIPPY_CACHE_QUAL_BACKEND", backend.name().into()),
                (
                    "SKIPPY_CACHE_QUAL_MODEL",
                    fixture.to_string_lossy().into_owned(),
                ),
                (
                    "SKIPPY_CACHE_QUAL_REPORT_DIR",
                    report_dir.to_string_lossy().into_owned(),
                ),
            ],
        )?;
        let report: Report =
            serde_json::from_slice(&fs::read(report_dir.join("smoke-result.json"))?)?;
        if report.status != Status::Pass
            || report.backend != backend.profile()
            || report.fixture != *model
        {
            return Err(
                "missing passing KV smoke evidence for the selected fixture and backend".into(),
            );
        }
        return Ok(());
    }

    let start = verify::Source::capture(&output)?;
    let inputs = Inputs {
        fixtures: Fixtures::HuggingFace,
        reference: prepare::packaged_reference(backend)?,
        runner: TestRunner::Packaged { binary, runtime },
    };
    let cells = fixtures
        .models
        .iter()
        .map(|model| run_cell(backend, &output, model, &inputs))
        .collect();
    let end = verify::Source::capture(&output)?;
    write_summary(backend, &output, start, end, &manifest, cells)
}

pub(super) fn run_test(
    binary: &Path,
    runtime: &Path,
    test: &str,
    mut environment: Vec<(&str, String)>,
) -> DynResult<()> {
    environment.push((
        "SKIPPY_NATIVE_RUNTIME_BUNDLE_DIR",
        runtime.to_string_lossy().into_owned(),
    ));
    execution::run(
        &binary.to_string_lossy(),
        &strings(&[test, "--ignored", "--exact", "--nocapture"]),
        &environment,
        if test == SMOKE_CASE {
            600
        } else {
            super::CELL_TIMEOUT_SECONDS
        },
    )
}

fn compile_test() -> DynResult<PathBuf> {
    let messages = execution::capture_for(
        "just",
        &strings(&[
            "with-lld",
            "cargo",
            "test",
            "--locked",
            "-p",
            "skippy-serving",
            "--features",
            "dynamic-native-runtime",
            "--lib",
            "--no-run",
            "--message-format=json",
        ]),
        &[],
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
