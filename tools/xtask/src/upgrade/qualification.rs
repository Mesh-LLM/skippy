mod build;
mod causal;
mod workload;
use super::evidence::{self, Family, Receipt};
use crate::{
    artifact,
    command::{DynResult, write_json_file},
    execution,
};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

pub(super) fn run(args: &[String]) -> DynResult<()> {
    let [
        backend_option,
        backend,
        suite_option,
        suite,
        pass_option,
        pass,
    ] = args
    else {
        return Err(
            "certify --backend cpu|metal|cuda --suite supported --pass candidate|independent"
                .into(),
        );
    };
    if backend_option != "--backend"
        || suite_option != "--suite"
        || suite != "supported"
        || pass_option != "--pass"
        || !matches!(backend.as_str(), "cpu" | "metal" | "cuda")
        || !matches!(pass.as_str(), "candidate" | "independent")
    {
        return Err("invalid supported-family qualification request".into());
    }
    let root = std::env::current_dir()?.canonicalize()?;
    if !execution::output(
        "git",
        &execution::strings(&["status", "--porcelain", "--untracked-files=no"]),
    )?
    .trim()
    .is_empty()
    {
        return Err("qualification requires an unchanged candidate checkout".into());
    }
    let models = evidence::load()?;
    let cache = cache_root()?;
    let output = root.join("target/llama-upgrade/evidence");
    if output.exists() {
        return Err("qualification evidence directory must be absent for a fresh pass".into());
    }
    std::fs::create_dir_all(&output)?;
    let candidate = execution::output("git", &execution::strings(&["rev-parse", "HEAD"]))?
        .trim()
        .to_owned();
    let recipe = evidence::recipe_digest_at(&root)?;
    let mut admitted = Vec::new();
    for model in &models.models {
        let mut files = BTreeMap::new();
        let target = evidence::cached(&model.artifact, &cache, &mut files)?;
        let projector = model
            .mmproj_artifact
            .as_ref()
            .map(|artifact| evidence::cached(artifact, &cache, &mut files))
            .transpose()?;
        if let Some(draft) = &model.draft_artifact {
            evidence::cached(draft, &cache, &mut files)?;
        }
        admitted.push((model, target, projector, files));
    }
    let binaries = build::prepare(&root, backend, &output)?;
    let cpu = if backend == "cpu" {
        None
    } else {
        Some(build::prepare(&root, "cpu", &output)?)
    };
    let cpu = cpu.as_ref().unwrap_or(&binaries);
    let mut families = Vec::new();
    for (model, target, projector, files) in admitted {
        let directory = output.join(&model.family);
        std::fs::create_dir_all(&directory)?;
        if model.class == "causal_generation" {
            causal::run(model, &target, projector.as_deref(), &binaries, &directory)?;
        } else {
            workload::run(model, &target, projector.as_deref(), cpu, &directory)?;
        }
        families.push(Family {
            family: model.family.clone(),
            profile: model.profile.clone(),
            revision: model.artifact.revision.clone(),
            files,
            lanes: evidence::lanes(model)?,
        });
    }
    if recipe != evidence::recipe_digest_at(&root)?
        || candidate
            != execution::output("git", &execution::strings(&["rev-parse", "HEAD"]))?.trim()
    {
        return Err("qualification source changed during execution".into());
    }
    let mut receipt = Receipt {
        schema_version: 1,
        candidate_sha: candidate,
        upstream_sha: std::fs::read_to_string("llama_cpp/upstream.txt")?
            .trim()
            .into(),
        manifest_sha256: artifact::sha256(Path::new("ci/llama-canary/family-certified.json"))?,
        native_recipe_sha256: recipe,
        backend: backend.clone(),
        pass: pass.clone(),
        result: "pass",
        families,
        artifacts: BTreeMap::new(),
    };
    evidence::write(&output, &mut receipt)
}
fn cache_root() -> DynResult<PathBuf> {
    if let Some(path) = std::env::var_os("HF_HUB_CACHE").filter(|s| !s.is_empty()) {
        return Ok(PathBuf::from(path).canonicalize()?);
    }
    if let Some(path) = std::env::var_os("HF_CACHE").filter(|s| !s.is_empty()) {
        return Ok(PathBuf::from(path).join("hub").canonicalize()?);
    }
    Err("qualification requires a prewarmed immutable HF_HUB_CACHE or HF_CACHE; missing models never download implicitly".into())
}
fn logged(
    program: &str,
    args: &[String],
    env: &[(&str, String)],
    path: &Path,
    seconds: u64,
) -> DynResult<()> {
    let report = process_supervision::supervise(
        &execution::specification(program, args, env)?,
        &execution::limits(seconds),
        &process_supervision::Cancellation::default(),
        process_supervision::OutputFiles {
            stdout: Some(path.with_extension("stdout.log")),
            stderr: Some(path.with_extension("stderr.log")),
        },
    )?;
    write_json_file(
        &path.with_extension("result.json"),
        &serde_json::json!({"success":report.success(),"outcome":format!("{:?}",report.outcome),"cleanup_complete":report.cleanup.complete,"exit_code":report.status.as_ref().and_then(std::process::ExitStatus::code)}),
    )?;
    if !report.success() {
        return Err(format!("qualification command failed; see {}", path.display()).into());
    }
    Ok(())
}
