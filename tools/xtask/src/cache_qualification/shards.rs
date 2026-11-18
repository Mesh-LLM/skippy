use super::{
    Backend, Cell, MANIFEST, SUMMARY_VERSION, Status, Summary, artifact, assess_report, bundle,
    failure, read_manifest, run_cell, verify, write_summary,
};
use crate::command::DynResult;
use skippy_model_artifact::kv_fixtures::KvFixture;
use std::{env, fs, path::Path};

pub(super) fn run(backend: Backend, family: &str, archive: &Path, output: &Path) -> DynResult<()> {
    fs::create_dir(output)?;
    let output = output.canonicalize()?;
    let manifest = fs::read_to_string(MANIFEST)?;
    let fixtures = read_manifest(&manifest)?;
    let model = fixtures
        .models
        .iter()
        .find(|fixture| fixture.family.as_str() == family)
        .ok_or_else(|| format!("unknown KV architecture case {family:?}"))?;
    let start = verify::Source::capture(&output)?;
    let binaries = tempfile::tempdir()?;
    let cell = match bundle::inputs(backend, archive, binaries.path()) {
        Ok(inputs) => run_cell(backend, &output, model, &inputs),
        Err(error) => failure(model, None, None, error.to_string()),
    };
    let end = verify::Source::capture(&output)?;
    write_summary(backend, &output, start, end, &manifest, vec![cell])
}

pub(super) fn assemble(backend: Backend, shards: &Path, output: &Path) -> DynResult<()> {
    let scope = env::var("SKIPPY_ARTIFACT_SCOPE").unwrap_or_else(|_| "ci".into());
    if scope.is_empty()
        || !scope
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err("invalid cache shard artifact scope".into());
    }
    fs::create_dir(output)?;
    let output = output.canonicalize()?;
    let manifest = fs::read_to_string(MANIFEST)?;
    let fixtures = read_manifest(&manifest)?;
    let digest = artifact::digest(manifest.as_bytes())?;
    let start = verify::Source::capture(&output)?;
    let cells = fixtures
        .models
        .iter()
        .map(|model| {
            let case = model.family.as_str();
            let shard = shards.join(format!("{scope}-kv-shard-{}-{case}", backend.name()));
            match admit(backend, model, &start.head, &digest, &shard, &output) {
                Ok(cell) => cell,
                Err(error) => failure(
                    model,
                    None,
                    None,
                    format!("invalid shard evidence: {error}"),
                ),
            }
        })
        .collect::<Vec<_>>();
    for cell in &cells {
        if let Some(error) = &cell.error {
            eprintln!("{} {}: {error}", backend.name(), cell.family);
        }
    }
    let end = verify::Source::capture(&output)?;
    write_summary(backend, &output, start, end, &manifest, cells)
}

fn admit(
    backend: Backend,
    model: &KvFixture,
    source: &str,
    manifest: &str,
    shard: &Path,
    output: &Path,
) -> DynResult<Cell> {
    let summary: Summary = serde_json::from_slice(&fs::read(shard.join("batch-result.json"))?)?;
    let [cell] = summary
        .cells
        .try_into()
        .map_err(|_| "expected exactly one case per shard")?;
    let case = model.family.as_str();
    if summary.schema_version != SUMMARY_VERSION
        || summary.profile != backend.profile()
        || summary.source_head != source
        || !summary.source_tree_clean
        || summary.fixture_manifest_sha256 != manifest
        || summary.status != cell.status
        || cell.family != case
    {
        return Err(
            "shard identity does not match the requested source, fixtures, backend, and case"
                .into(),
        );
    }
    match (&cell.report, &cell.report_sha256) {
        (Some(path), Some(digest)) if path == &Path::new(case).join("result.json") => {
            let report = fs::read(shard.join(path))?;
            if artifact::digest(report.as_slice())? != *digest {
                return Err("shard report checksum mismatch".into());
            }
            if cell.status == Status::Pass {
                assess_report(
                    backend,
                    model,
                    Ok(()),
                    Some(serde_json::from_slice(&report)),
                )?;
            }
            fs::create_dir(output.join(case))?;
            fs::write(output.join(path), report)?;
        }
        (None, None) if cell.status == Status::Fail => {}
        _ => return Err("shard is missing its expected report or checksum".into()),
    }
    if (cell.status == Status::Pass) == cell.error.is_some() {
        return Err("shard status and error are inconsistent".into());
    }
    Ok(cell)
}

#[cfg(test)]
mod tests {
    use super::*;
    use skippy_model_artifact::kv_fixtures::KvFixtureManifest;

    #[test]
    fn shard_admission_preserves_failure_and_rejects_stale_or_altered_evidence() {
        let workspace = tempfile::tempdir().unwrap();
        let output = tempfile::tempdir().unwrap();
        let model = KvFixtureManifest::bundled().unwrap().models.remove(0);
        let path = Path::new(model.family.as_str()).join("result.json");
        fs::create_dir(workspace.path().join(path.parent().unwrap())).unwrap();
        let report = b"{\"status\":\"fail\"}";
        fs::write(workspace.path().join(&path), report).unwrap();
        let summary = Summary {
            schema_version: SUMMARY_VERSION,
            source_head: "source".into(),
            source_tree_clean: true,
            fixture_manifest_sha256: "manifest".into(),
            profile: Backend::Metal.profile().into(),
            status: Status::Fail,
            cells: vec![failure(
                &model,
                Some(path.clone()),
                Some(artifact::digest(&report[..]).unwrap()),
                "recording failed".into(),
            )],
        };
        fs::write(
            workspace.path().join("batch-result.json"),
            serde_json::to_vec(&summary).unwrap(),
        )
        .unwrap();
        let admit_at = |source| {
            admit(
                Backend::Metal,
                &model,
                source,
                "manifest",
                workspace.path(),
                output.path(),
            )
        };
        assert!(admit_at("stale").is_err());
        let cell = admit_at("source").unwrap();
        assert_eq!(cell.error.as_deref(), Some("recording failed"));
        assert_eq!(fs::read(output.path().join(&path)).unwrap(), report);
        fs::write(workspace.path().join(&path), b"altered").unwrap();
        assert_eq!(
            admit_at("source").err().unwrap().to_string(),
            "shard report checksum mismatch"
        );
    }
}
