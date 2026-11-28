use super::{
    Backend, MANIFEST, SUMMARY_VERSION, Status, Summary, artifact, assess_report, execution,
    read_manifest, strings,
};
use crate::command::DynResult;
use serde::Serialize;
use skippy_model_artifact::kv_fixtures::{KvFixtureManifest, valid_sha256};
use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
};

pub(super) struct Source {
    pub(super) head: String,
    pub(super) clean: bool,
}

impl Source {
    pub(super) fn capture(output: &Path) -> DynResult<Self> {
        let head = execution::output("git", &strings(&["rev-parse", "HEAD"]))?;
        let root = execution::output("git", &strings(&["rev-parse", "--show-toplevel"]))?;
        let mut args = strings(&[
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            ".",
        ]);
        if let Ok(relative) = output.strip_prefix(Path::new(root.trim())) {
            let relative = relative
                .to_str()
                .ok_or("report directory must be Unicode")?;
            args.push(format!(":(top,exclude,literal){relative}"));
        }
        let clean = execution::output("git", &args)?.trim().is_empty();
        Ok(Self {
            head: head.trim().into(),
            clean,
        })
    }
}

pub(super) fn run(backend: Backend, source: &str, directory: &Path) -> DynResult<()> {
    assess(backend, source, directory, None)?;
    println!(
        "Verified {} correctness evidence for {source}",
        backend.name()
    );
    Ok(())
}

#[derive(Serialize)]
pub(crate) struct VerifiedMatrix {
    summary_sha256: String,
    reports: Vec<VerifiedReport>,
}

#[derive(Serialize)]
struct VerifiedReport {
    family: String,
    report_sha256: String,
    test_executable_sha256: String,
    reference_binary_sha256: String,
}

pub(crate) fn assess(
    backend: Backend,
    source: &str,
    directory: &Path,
    reference: Option<&str>,
) -> DynResult<VerifiedMatrix> {
    let manifest = fs::read_to_string(MANIFEST)?;
    let digest = artifact::digest(manifest.as_bytes())?;
    let phase_one = read_manifest(&manifest)?;
    let directory = directory.canonicalize()?;
    let summary_bytes = read_file(&directory.join("batch-result.json"))?;
    let summary: Summary = serde_json::from_slice(&summary_bytes)?;
    validate_summary(backend, source, &digest, &phase_one, &summary)?;
    let staging = tempfile::tempdir()?;
    let mut reports = Vec::new();
    for model in &phase_one.models {
        let family = model.family.as_str();
        let cell = summary
            .cells
            .iter()
            .find(|cell| cell.family == family)
            .ok_or_else(|| format!("missing correctness cell {family}"))?;
        let evidence_directory = directory.join(family);
        if !evidence_directory.symlink_metadata()?.file_type().is_dir() {
            return Err(format!("expected a regular correctness directory for {family}").into());
        }
        let report = read_file(&evidence_directory.join("result.json"))?;
        let observed_sha = artifact::digest(report.as_slice())?;
        if cell.report_sha256.as_deref() != Some(&observed_sha) {
            return Err(format!("changed correctness report for {family}").into());
        }
        let measured = serde_json::from_slice::<super::Report>(&report)?;
        let (test_digest, reference_digest) = report_binaries(&measured, reference)?;
        assess_report(backend, model, Ok(()), Some(Ok(measured)))?;
        let staged = staging.path().join(format!("{family}.json"));
        fs::write(&staged, report)?;
        let staged = staged
            .to_str()
            .ok_or("temporary report path must be Unicode")?;
        execution::run(
            "just",
            &strings(&["cache-correctness-verify", backend.name(), staged]),
            &[("CARGO_NET_OFFLINE", "true".into())],
            1800,
        )
        .map_err(|error| {
            format!("{family} correctness evidence failed offline validation: {error}")
        })?;
        reports.push(VerifiedReport {
            family: family.into(),
            report_sha256: observed_sha,
            test_executable_sha256: test_digest,
            reference_binary_sha256: reference_digest,
        });
    }
    Ok(VerifiedMatrix {
        summary_sha256: artifact::digest(&summary_bytes[..])?,
        reports,
    })
}

fn report_binaries(
    report: &super::Report,
    expected_reference: Option<&str>,
) -> DynResult<(String, String)> {
    let test = report
        .skippy_binary_sha256
        .as_deref()
        .filter(|hash| valid_sha256(hash))
        .ok_or("missing correctness test executable digest")?;
    let reference = report
        .reference
        .as_ref()
        .map(|reference| reference.binary_sha256.as_str())
        .filter(|hash| valid_sha256(hash))
        .ok_or("missing correctness reference executable digest")?;
    if expected_reference.is_some_and(|expected| expected != reference) {
        return Err("correctness reference binary differs from the frozen parity plan".into());
    }
    Ok((test.into(), reference.into()))
}

fn validate_summary(
    backend: Backend,
    source: &str,
    manifest_sha256: &str,
    phase_one: &KvFixtureManifest,
    summary: &Summary,
) -> DynResult<()> {
    if source.len() != 40
        || !source
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
        || !source.bytes().any(|byte| byte != b'0')
        || summary.schema_version != SUMMARY_VERSION
        || summary.source_head != source
        || !summary.source_tree_clean
        || summary.profile != backend.profile()
        || summary.fixture_manifest_sha256 != manifest_sha256
    {
        return Err(
            "correctness matrix identity does not match the requested source and profile".into(),
        );
    }
    if summary.status != Status::Pass || summary.cells.len() != phase_one.models.len() {
        return Err("correctness matrix lacks a complete passing roster".into());
    }
    let expected: BTreeSet<_> = phase_one
        .models
        .iter()
        .map(|model| model.family.as_str())
        .collect();
    let mut families = BTreeSet::new();
    for cell in &summary.cells {
        let expected_path = PathBuf::from(&cell.family).join("result.json");
        if !expected.contains(cell.family.as_str())
            || !families.insert(cell.family.as_str())
            || cell.status != Status::Pass
            || cell.error.is_some()
            || cell.report.as_ref() != Some(&expected_path)
            || !cell.report_sha256.as_deref().is_some_and(valid_sha256)
        {
            return Err(format!("invalid correctness matrix cell {}", cell.family).into());
        }
    }
    Ok(())
}

fn read_file(path: &Path) -> DynResult<Vec<u8>> {
    if !path.symlink_metadata()?.file_type().is_file() {
        return Err(format!("expected a regular evidence file {}", path.display()).into());
    }
    Ok(fs::read(path)?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cache_qualification::Cell;

    #[test]
    fn parity_join_checks_the_reference_digest_and_preserves_test_binary_identity() {
        let model = KvFixtureManifest::bundled().unwrap().models.remove(0);
        let candidate = "a".repeat(64);
        let reference = "b".repeat(64);
        let mut report = super::super::Report {
            schema_version: 9,
            status: Status::Pass,
            backend: Backend::Metal.profile().into(),
            model_sha256: Some(model.sha256.clone()),
            fixture: model,
            error: None,
            skippy_binary_sha256: Some(candidate.clone()),
            reference: Some(super::super::ReferenceIdentity {
                binary_sha256: reference.clone(),
            }),
            conversation_results: Default::default(),
            structured_results: Default::default(),
            template_result: None,
        };
        assert_eq!(
            report_binaries(&report, Some(&reference)).unwrap(),
            (candidate.clone(), reference)
        );
        assert!(report_binaries(&report, Some(&candidate)).is_err());
        report.reference = None;
        assert!(report_binaries(&report, None).is_err());
    }

    #[test]
    fn matrix_admission_requires_a_clean_pinned_source_and_unique_matching_cells() {
        let source = "1".repeat(40);
        let digest = "a".repeat(64);
        let manifest = KvFixtureManifest::bundled().unwrap();
        let mut summary = Summary {
            schema_version: SUMMARY_VERSION,
            source_head: source.clone(),
            source_tree_clean: true,
            fixture_manifest_sha256: digest.clone(),
            profile: Backend::Metal.profile().into(),
            status: Status::Pass,
            cells: manifest
                .models
                .iter()
                .map(|model| {
                    let family = model.family.as_str();
                    Cell {
                        family: family.into(),
                        status: Status::Pass,
                        report: Some(PathBuf::from(family).join("result.json")),
                        report_sha256: Some("b".repeat(64)),
                        error: None,
                    }
                })
                .collect(),
        };
        let validate = |summary: &Summary| {
            validate_summary(Backend::Metal, &source, &digest, &manifest, summary)
        };
        assert!(validate(&summary).is_ok());
        assert!(validate_summary(Backend::Cuda, &source, &digest, &manifest, &summary).is_err());
        summary.source_tree_clean = false;
        assert!(validate(&summary).is_err());
        summary.source_tree_clean = true;
        summary.source_head = "2".repeat(40);
        assert!(validate(&summary).is_err());
        summary.source_head = source.clone();
        summary.cells[1].report = Some(PathBuf::from("../external.json"));
        assert!(validate(&summary).is_err());
        summary.cells[1].family = summary.cells[0].family.clone();
        summary.cells[1].report = summary.cells[0].report.clone();
        assert!(validate(&summary).is_err());
    }
}
