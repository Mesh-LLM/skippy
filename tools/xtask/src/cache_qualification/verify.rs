use super::{
    Backend, MANIFEST, SUMMARY_VERSION, Status, Summary, artifact, assess_report, execution,
    read_manifest, strings,
};
use crate::command::DynResult;
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
    let manifest = fs::read_to_string(MANIFEST)?;
    let digest = artifact::digest(manifest.as_bytes())?;
    let fixtures = read_manifest(&manifest)?;
    let directory = directory.canonicalize()?;
    let summary: Summary =
        serde_json::from_slice(&read_file(&directory.join("batch-result.json"))?)?;
    validate_summary(backend, source, &digest, &fixtures, &summary)?;
    let staging = tempfile::tempdir()?;
    for model in &fixtures.models {
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
        validate_report_binaries(&measured)?;
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
    }
    println!(
        "Verified {} correctness evidence for {source}",
        backend.name()
    );
    Ok(())
}

fn validate_report_binaries(report: &super::Report) -> DynResult<()> {
    report
        .skippy_binary_sha256
        .as_deref()
        .filter(|hash| valid_sha256(hash))
        .ok_or("missing correctness test executable digest")?;
    report
        .reference
        .as_ref()
        .map(|reference| reference.binary_sha256.as_str())
        .filter(|hash| valid_sha256(hash))
        .ok_or("missing correctness reference executable digest")?;
    Ok(())
}

fn validate_summary(
    backend: Backend,
    source: &str,
    manifest_sha256: &str,
    fixtures: &KvFixtureManifest,
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
    if summary.status != Status::Pass || summary.cells.len() != fixtures.models.len() {
        return Err("correctness matrix lacks a complete passing roster".into());
    }
    let expected: BTreeSet<_> = fixtures
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
    fn offline_verification_requires_both_executable_digests() {
        let model = KvFixtureManifest::bundled().unwrap().models.remove(0);
        let mut report = super::super::Report {
            schema_version: 9,
            status: Status::Pass,
            backend: Backend::Metal.profile().into(),
            model_sha256: Some(model.sha256.clone()),
            fixture: model,
            error: None,
            skippy_binary_sha256: Some("a".repeat(64)),
            reference: Some(super::super::ReferenceIdentity {
                binary_sha256: "b".repeat(64),
            }),
            conversation_results: Default::default(),
            structured_results: Default::default(),
            template_result: None,
        };
        assert!(validate_report_binaries(&report).is_ok());
        report.skippy_binary_sha256 = Some("0".repeat(64));
        assert!(validate_report_binaries(&report).is_err());
        report.skippy_binary_sha256 = Some("a".repeat(64));
        report.reference = None;
        assert!(validate_report_binaries(&report).is_err());
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
