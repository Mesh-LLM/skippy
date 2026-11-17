use super::*;
use crate::{artifact, execution};
use std::{collections::BTreeMap, fs, path::PathBuf};
#[derive(Deserialize)]
pub struct Fixture {
    pub repo: String,
    pub revision: String,
    pub files: Vec<File>,
    pub tokenizer_repo: String,
    pub tokenizer_revision: String,
    pub layers: u32,
}
#[derive(Deserialize)]
pub struct File {
    pub path: String,
    pub sha256: String,
    pub size: u64,
}
pub fn load(key: &str) -> DynResult<Fixture> {
    let mut fixtures: BTreeMap<String, Fixture> =
        serde_json::from_str(include_str!("../../../../ci/inference-artifacts.json"))?;
    fixtures
        .remove(key)
        .ok_or_else(|| "unknown model fixture".into())
}
pub fn admit(key: &str) -> DynResult<PathBuf> {
    let fixture = load(key)?;
    if fixture.revision.len() != 40 || fixture.files.is_empty() {
        return Err("unpinned fixture".into());
    }
    let mut paths = Vec::new();
    for file in fixture.files {
        let text = execution::capture_for(
            "hf",
            &[
                "download".into(),
                fixture.repo.clone(),
                "--revision".into(),
                fixture.revision.clone(),
                file.path,
            ],
            &[],
            7200,
        )?;
        let path = PathBuf::from(text.trim());
        let metadata = fs::metadata(&path)
            .map_err(|error| format!("downloaded model path {}: {error}", path.display()))?;
        if metadata.len() != file.size || artifact::sha256(&path)? != file.sha256 {
            return Err("model artifact digest mismatch".into());
        }
        paths.push(path);
    }
    paths
        .into_iter()
        .next()
        .ok_or_else(|| "empty fixture".into())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn all_ladder_models_have_pinned_base_weights() {
        let ladder: Ladder =
            serde_json::from_str(include_str!("../../../../ci/inference-ladder.json")).unwrap();
        for model in ladder.models {
            let fixture = load(&model.id).unwrap();
            assert_eq!(fixture.revision.len(), 40);
            assert!(!fixture.files.is_empty());
            for f in fixture.files {
                assert_eq!(f.sha256.len(), 64);
                assert!(f.size > 0);
                assert!(!f.path.starts_with("MTP/"));
            }
        }
    }
}
