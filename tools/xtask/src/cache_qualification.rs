//! Run each GPU cache fixture independently and retain every outcome.

use crate::{
    artifact,
    command::DynResult,
    execution::{self, strings},
    huggingface,
};
use serde::{Deserialize, Serialize};
use skippy_model_artifact::kv_fixtures::{KvFixture, KvFixtureManifest, valid_sha256};
use std::{
    collections::BTreeMap,
    env, fs,
    path::{Path, PathBuf},
};

pub(crate) const MANIFEST: &str = "crates/skippy-model-artifact/src/kv_fixtures/models.json";
const CONVERSATIONS: [&str; 3] = ["first_follow_up", "second_follow_up", "divergent_tail"];
const STRUCTURED: [&str; 3] = ["json_schema", "named_tool", "long_retrieval"];
const CELL_TIMEOUT_SECONDS: u64 = 1800;

mod prepare;
pub(crate) mod verify;

const SUMMARY_VERSION: u32 = 2;

#[derive(Clone, Copy)]
pub(crate) enum Backend {
    Metal,
    Cuda,
    CudaArm64,
}

impl Backend {
    fn parse(name: &str) -> DynResult<Self> {
        match name {
            "metal" => Ok(Self::Metal),
            "cuda" => Ok(Self::Cuda),
            "cuda-arm64" => Ok(Self::CudaArm64),
            _ => Err(format!("unknown cache qualification backend {name:?}").into()),
        }
    }

    fn profile(self) -> &'static str {
        match self {
            Self::Metal => "macos-arm64-metal",
            Self::Cuda => "linux-x86_64-cuda",
            Self::CudaArm64 => "linux-aarch64-cuda",
        }
    }

    fn recipe(self) -> &'static str {
        match self {
            Self::Metal => "cache-correctness-metal",
            Self::Cuda | Self::CudaArm64 => "cache-correctness-cuda",
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::Metal => "metal",
            Self::Cuda => "cuda",
            Self::CudaArm64 => "cuda-arm64",
        }
    }

    fn runtime_backend(self) -> &'static str {
        match self {
            Self::Metal => "metal",
            Self::Cuda | Self::CudaArm64 => "cuda",
        }
    }
}

pub(crate) fn read_manifest(body: &str) -> DynResult<KvFixtureManifest> {
    let manifest = KvFixtureManifest::parse(body).map_err(|error| format!("{error:#}"))?;
    if manifest
        .models
        .iter()
        .any(|row| !row.required_sidecars.is_empty())
    {
        return Err("cache qualification does not support attaching model sidecars".into());
    }
    Ok(manifest)
}

struct Inputs {
    fixtures: Fixtures,
    reference: PathBuf,
    native_build: PathBuf,
}

enum Fixtures {
    Directory(PathBuf),
    HuggingFace,
}

impl Fixtures {
    fn resolve(&self, model: &KvFixture) -> DynResult<PathBuf> {
        let path = match self {
            Self::Directory(directory) => directory.join(&model.file),
            Self::HuggingFace => {
                huggingface::download(&model.repo, &model.revision, &[&model.file], 1800)?
            }
        };
        if !path.is_file() {
            return Err(format!("missing pinned fixture {}", path.display()).into());
        }
        Ok(path)
    }
}

impl Inputs {
    fn load(
        backend: Backend,
        output: &Path,
        prepare_builds: bool,
        download: bool,
    ) -> DynResult<Self> {
        let fixtures = if download {
            Fixtures::HuggingFace
        } else {
            Fixtures::Directory(existing_input(
                "SKIPPY_CACHE_QUAL_FIXTURE_DIR",
                Path::is_dir,
            )?)
        };
        if prepare_builds {
            return prepare::build(backend, output, fixtures);
        }
        Ok(Self {
            fixtures,
            reference: existing_input("SKIPPY_CACHE_QUAL_LLAMA_SERVER", Path::is_file)?,
            native_build: existing_input("LLAMA_STAGE_BUILD_DIR", Path::is_dir)?,
        })
    }
}

fn existing_input(name: &str, valid: impl FnOnce(&Path) -> bool) -> DynResult<PathBuf> {
    let path = PathBuf::from(env::var_os(name).ok_or_else(|| format!("set {name}"))?);
    if !path.is_absolute() || !valid(&path) {
        return Err(format!("{name} must name an existing absolute path").into());
    }
    Ok(path.canonicalize()?)
}

#[derive(Clone, Copy, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
enum Status {
    Pass,
    Fail,
}

#[derive(Deserialize)]
struct Report {
    schema_version: u32,
    status: Status,
    backend: String,
    fixture: KvFixture,
    model_sha256: Option<String>,
    skippy_binary_sha256: Option<String>,
    reference: Option<ReferenceIdentity>,
    error: Option<String>,
    conversation_results: BTreeMap<String, ScenarioReport>,
    structured_results: BTreeMap<String, ScenarioReport>,
    template_result: Option<Status>,
}

#[derive(Deserialize)]
struct ReferenceIdentity {
    binary_sha256: String,
}

#[derive(Deserialize)]
struct ScenarioReport {
    status: Status,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Cell {
    family: String,
    status: Status,
    report: Option<PathBuf>,
    report_sha256: Option<String>,
    error: Option<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Summary {
    schema_version: u32,
    source_head: String,
    source_tree_clean: bool,
    fixture_manifest_sha256: String,
    profile: String,
    status: Status,
    cells: Vec<Cell>,
}

pub fn run(args: &[String]) -> DynResult<()> {
    let (backend, output, prepare_builds, download) = match args {
        [command, backend, source, directory] if command == "verify" => {
            return verify::run(Backend::parse(backend)?, source, Path::new(directory));
        }
        [backend, output] => (backend, output, false, false),
        [backend, output, flag] if flag == "--build" => (backend, output, true, false),
        [backend, output, build, hf] if build == "--build" && hf == "--hf" => {
            (backend, output, true, true)
        }
        _ => {
            return Err(
                "usage: cache-qualification {metal|cuda|cuda-arm64} FRESH_REPORT_DIR [--build [--hf]] | verify {metal|cuda|cuda-arm64} SOURCE_SHA REPORT_DIR".into(),
            );
        }
    };
    let backend = Backend::parse(backend)?;
    let output = Path::new(output);
    fs::create_dir(output)?;
    let output = output.canonicalize()?;
    let manifest_bytes = fs::read_to_string(MANIFEST)?;
    let manifest = read_manifest(&manifest_bytes)?;
    let start = verify::Source::capture(&output)?;
    let inputs = Inputs::load(backend, &output, prepare_builds, download);
    let cells = manifest
        .models
        .iter()
        .map(|model| match &inputs {
            Ok(inputs) => run_cell(backend, &output, model, inputs),
            Err(error) => failure(model, None, None, error.to_string()),
        })
        .collect::<Vec<_>>();
    let status = if cells.iter().all(|row| row.status == Status::Pass) {
        Status::Pass
    } else {
        Status::Fail
    };
    let end = verify::Source::capture(&output)?;
    let source_tree_clean = start.clean && end.clean && start.head == end.head;
    let summary = Summary {
        schema_version: SUMMARY_VERSION,
        source_head: start.head,
        source_tree_clean,
        fixture_manifest_sha256: artifact::digest(manifest_bytes.as_bytes())?,
        profile: backend.profile().into(),
        status,
        cells,
    };
    let report_path = output.join("batch-result.json");
    serde_json::to_writer_pretty(fs::File::create(&report_path)?, &summary)?;
    println!(
        "Cache qualification matrix report {}",
        report_path.display()
    );
    if status == Status::Fail {
        return Err("one or more cache qualification cells failed".into());
    }
    Ok(())
}

fn run_cell(backend: Backend, output: &Path, model: &KvFixture, inputs: &Inputs) -> Cell {
    let fixture = match inputs.fixtures.resolve(model) {
        Ok(path) => path,
        Err(error) => return failure(model, None, None, error.to_string()),
    };
    let family = model.family.as_str();
    let directory = output.join(family);
    let relative_report = PathBuf::from(family).join("result.json");
    let result = execution::run(
        "just",
        &strings(&[backend.recipe()]),
        &[
            ("SKIPPY_CACHE_QUAL_BACKEND", backend.name().into()),
            ("SKIPPY_CACHE_QUAL_FAMILY", family.into()),
            (
                "SKIPPY_CACHE_QUAL_MODEL",
                fixture.to_string_lossy().into_owned(),
            ),
            (
                "SKIPPY_CACHE_QUAL_LLAMA_SERVER",
                inputs.reference.to_string_lossy().into_owned(),
            ),
            (
                "SKIPPY_CACHE_QUAL_REPORT_DIR",
                directory.to_string_lossy().into_owned(),
            ),
            (
                "LLAMA_STAGE_BUILD_DIR",
                inputs.native_build.to_string_lossy().into_owned(),
            ),
        ],
        CELL_TIMEOUT_SECONDS,
    );
    let path = output.join(&relative_report);
    let bytes = fs::read(&path).ok();
    let report_sha256 = match bytes.as_deref().map(artifact::digest).transpose() {
        Ok(digest) => digest,
        Err(error) => return failure(model, Some(relative_report), None, error.to_string()),
    };
    let evidence = bytes.as_deref().map(serde_json::from_slice::<Report>);
    let path = path.is_file().then_some(relative_report);
    match assess_report(backend, model, result, evidence) {
        Ok(()) => Cell {
            family: family.into(),
            status: Status::Pass,
            report: path,
            report_sha256,
            error: None,
        },
        Err(error) => failure(model, path, report_sha256, error.to_string()),
    }
}

fn assess_report(
    backend: Backend,
    model: &KvFixture,
    child: DynResult<()>,
    evidence: Option<serde_json::Result<Report>>,
) -> DynResult<()> {
    let report = match evidence {
        Some(evidence) => evidence?,
        None => {
            child?;
            return Err("qualification exited without a result report".into());
        }
    };
    if report.schema_version != 9 || report.backend != backend.profile() || report.fixture != *model
    {
        return Err("qualification result identity does not match the requested cell".into());
    }
    if report.status == Status::Fail {
        return Err(report
            .error
            .unwrap_or_else(|| "qualification reported a failure".into())
            .into());
    }
    if report.error.is_some()
        || report.model_sha256.as_deref() != Some(model.sha256.as_str())
        || !report
            .skippy_binary_sha256
            .as_deref()
            .is_some_and(valid_sha256)
        || !all_scenarios_pass(&report.conversation_results, &CONVERSATIONS)
        || !all_scenarios_pass(&report.structured_results, &STRUCTURED)
        || report.template_result != Some(Status::Pass)
    {
        return Err("passing report lacks complete passing scenario evidence".into());
    }
    child
}

fn all_scenarios_pass(results: &BTreeMap<String, ScenarioReport>, names: &[&str]) -> bool {
    results.len() == names.len()
        && names.iter().all(|name| {
            results
                .get(*name)
                .is_some_and(|row| row.status == Status::Pass)
        })
}

fn failure(
    model: &KvFixture,
    report: Option<PathBuf>,
    report_sha256: Option<String>,
    error: String,
) -> Cell {
    Cell {
        family: model.family.as_str().into(),
        status: Status::Fail,
        report,
        report_sha256,
        error: Some(error),
    }
}

#[cfg(test)]
mod tests;
