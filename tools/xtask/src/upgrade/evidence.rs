use crate::{
    artifact,
    command::{DynResult, write_json_file},
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

#[derive(Deserialize)]
pub(super) struct Manifest {
    pub schema_version: u32,
    pub models: Vec<Model>,
}
#[derive(Deserialize)]
pub(super) struct Model {
    pub family: String,
    #[serde(rename = "class")]
    pub class: String,
    pub profile: String,
    pub artifact: ModelArtifact,
    pub mmproj_artifact: Option<ModelArtifact>,
    pub draft_artifact: Option<ModelArtifact>,
    pub execution: Execution,
}
#[derive(Deserialize)]
pub(super) struct Execution {
    pub trunk_layers: u32,
    pub mtp_layers: u32,
    pub activation_width: u32,
    pub speculative_policy: String,
}
#[derive(Deserialize)]
pub(super) struct ModelArtifact {
    pub repo: String,
    pub revision: String,
    pub files: Vec<String>,
    pub file_integrity: BTreeMap<String, Integrity>,
    pub selector: String,
}
#[derive(Deserialize)]
pub(super) struct Integrity {
    pub size_bytes: u64,
    pub blob_id: String,
}
#[derive(Serialize)]
pub(super) struct Family {
    pub family: String,
    pub profile: String,
    pub revision: String,
    pub files: BTreeMap<String, String>,
    pub lanes: Vec<String>,
}
#[derive(Serialize)]
pub(super) struct Receipt {
    pub schema_version: u32,
    pub candidate_sha: String,
    pub upstream_sha: String,
    pub manifest_sha256: String,
    pub native_recipe_sha256: String,
    pub backend: String,
    pub pass: String,
    pub result: &'static str,
    pub families: Vec<Family>,
    pub artifacts: BTreeMap<String, String>,
}

pub(crate) fn recipe_digest_at(root: &Path) -> DynResult<String> {
    let mut paths = Vec::new();
    for relative in [
        "llama_cpp",
        "scripts/build-llama.sh",
        "scripts/prepare-llama.sh",
        "skippy_api",
        "crates/skippy-ffi",
    ] {
        inventory(root, &root.join(relative), &mut paths)?;
    }
    paths.sort();
    let mut digest = Sha256::new();
    for path in paths {
        digest.update(path.as_bytes());
        digest.update([0]);
        digest.update(std::fs::read(root.join(path))?);
        digest.update([0]);
    }
    Ok(hex::encode(digest.finalize()))
}
fn inventory(root: &Path, path: &Path, paths: &mut Vec<String>) -> DynResult<()> {
    let kind = path.symlink_metadata()?.file_type();
    if kind.is_symlink() {
        return Err("native recipe contains a symlink".into());
    }
    if kind.is_file() {
        paths.push(
            path.strip_prefix(root)?
                .to_str()
                .ok_or("non-UTF8 recipe path")?
                .replace('\\', "/"),
        );
    } else if kind.is_dir() {
        for entry in std::fs::read_dir(path)? {
            inventory(root, &entry?.path(), paths)?;
        }
    } else {
        return Err("native recipe contains a special file".into());
    }
    Ok(())
}
pub(super) fn write(root: &Path, receipt: &mut Receipt) -> DynResult<()> {
    let mut paths = Vec::new();
    inventory(root, root, &mut paths)?;
    paths.sort();
    for path in paths {
        if path != "receipt.json" {
            receipt
                .artifacts
                .insert(path.clone(), artifact::sha256(&root.join(path))?);
        }
    }
    write_json_file(&root.join("receipt.json"), receipt)
}
pub(super) fn load() -> DynResult<Manifest> {
    let result: Manifest =
        serde_json::from_slice(&std::fs::read("ci/llama-canary/family-certified.json")?)?;
    if result.schema_version != 1 || result.models.is_empty() {
        return Err("invalid supported family manifest".into());
    }
    let mut names = std::collections::BTreeSet::new();
    for model in &result.models {
        if !names.insert(&model.family)
            || model.family.is_empty()
            || !model
                .family
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'))
        {
            return Err("invalid or duplicate family label".into());
        }
        if model.execution.trunk_layers == 0
            || model.execution.activation_width == 0
            || !matches!(
                model.execution.speculative_policy.as_str(),
                "disabled" | "mtp-if-present"
            )
        {
            return Err("invalid family execution policy".into());
        }
        lanes(model)?;
        for artifact in std::iter::once(&model.artifact)
            .chain(model.mmproj_artifact.iter())
            .chain(model.draft_artifact.iter())
        {
            validate_artifact(artifact)?;
        }
    }
    Ok(result)
}
fn validate_artifact(artifact: &ModelArtifact) -> DynResult<()> {
    super::source::validate_sha(&artifact.revision)?;
    if artifact.repo.split('/').count() != 2
        || artifact.repo.split('/').any(|p| p.is_empty() || p == "..")
        || artifact.selector.is_empty()
        || artifact.files.is_empty()
        || artifact.files.len() != artifact.file_integrity.len()
    {
        return Err("invalid immutable model coordinate".into());
    }
    for file in &artifact.files {
        let path = Path::new(file);
        if path
            .components()
            .any(|p| !matches!(p, std::path::Component::Normal(_)))
        {
            return Err("model filename escapes snapshot".into());
        }
        let integrity = artifact
            .file_integrity
            .get(file)
            .ok_or("missing model file integrity")?;
        if integrity.size_bytes == 0
            || integrity.blob_id.len() != 64
            || !integrity.blob_id.bytes().all(|b| b.is_ascii_hexdigit())
        {
            return Err("invalid model file integrity".into());
        }
    }
    Ok(())
}
pub(super) fn lanes(model: &Model) -> DynResult<Vec<String>> {
    if model.class == "causal_generation" && model.profile == "full" {
        let mut lanes = vec!["single-step".into(), "chain".into(), "state-handoff".into()];
        if model.execution.mtp_layers > 0 {
            lanes.push("native-mtp-heads".into());
        }
        if model.mmproj_artifact.is_some() {
            lanes.push("multimodal-smoke".into());
        }
        Ok(lanes)
    } else if model.profile == "workload-oracle"
        && [
            "embedding",
            "rerank",
            "encoder_decoder",
            "ocr",
            "speech_synthesis",
            "speech_recognition",
        ]
        .contains(&model.class.as_str())
    {
        Ok(vec![
            format!("{}-smoke", model.class),
            format!("{}-oracle", model.class),
        ])
    } else {
        Err(format!(
            "unsupported qualification contract: {} {}",
            model.class, model.profile
        )
        .into())
    }
}
pub(super) fn cached(
    artifact: &ModelArtifact,
    cache: &Path,
    files: &mut BTreeMap<String, String>,
) -> DynResult<PathBuf> {
    let snapshot = cache
        .join(format!("models--{}", artifact.repo.replace('/', "--")))
        .join("snapshots")
        .join(&artifact.revision);
    for relative in &artifact.files {
        let file = snapshot.join(relative);
        let integrity = &artifact.file_integrity[relative];
        if std::fs::metadata(&file)?.len() != integrity.size_bytes
            || artifact::sha256(&file)? != integrity.blob_id
        {
            return Err(format!(
                "model cache integrity mismatch: {}@{}/{}",
                artifact.repo, artifact.revision, relative
            )
            .into());
        }
        files.insert(
            format!("{}@{}/{}", artifact.repo, artifact.revision, relative),
            integrity.blob_id.clone(),
        );
    }
    Ok(snapshot.join(&artifact.files[0]))
}

#[cfg(test)]
mod tests {
    #[test]
    fn cache_requires_actual_pinned_bytes() {
        let temp = tempfile::tempdir().unwrap();
        let value = super::ModelArtifact {
            repo: "org/model".into(),
            revision: "a".repeat(40),
            files: vec!["model.gguf".into()],
            file_integrity: std::collections::BTreeMap::from([(
                "model.gguf".into(),
                super::Integrity {
                    size_bytes: 4,
                    blob_id: "b".repeat(64),
                },
            )]),
            selector: "Q4".into(),
        };
        assert!(super::cached(&value, temp.path(), &mut Default::default()).is_err());
    }
}
