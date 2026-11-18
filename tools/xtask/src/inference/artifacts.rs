use super::*;
use crate::{artifact, huggingface};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};
#[derive(Deserialize)]
pub struct Fixture {
    pub repo: String,
    pub revision: String,
    pub files: Vec<File>,
    pub tokenizer_repo: String,
    pub tokenizer_revision: String,
    pub tokenizer_files: Vec<String>,
    pub layers: u32,
    pub activation_width: u32,
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
    let mut first = None;
    for file in fixture.files {
        let path = huggingface::download(&fixture.repo, &fixture.revision, &[&file.path], 18000)?;
        if !valid_file(&path, &file)? {
            return Err(format!("model artifact digest mismatch: {}", file.path).into());
        }
        // Keep snapshot filenames so the loader can locate sibling GGUF shards.
        first.get_or_insert(path);
    }
    first.ok_or_else(|| "empty fixture".into())
}
fn valid_file(path: &Path, file: &File) -> DynResult<bool> {
    Ok(fs::metadata(path)?.len() == file.size && artifact::sha256(path)? == file.sha256)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[cfg(unix)]
    fn pinned_shard_verification_reads_the_snapshot_symlink() {
        let root = tempfile::tempdir().unwrap();
        let blob = root.path().join("shared-blob");
        fs::write(&blob, b"admitted bytes").unwrap();
        let snapshot = root.path().join("model-00001-of-00002.gguf");
        std::os::unix::fs::symlink(&blob, &snapshot).unwrap();
        let file = File {
            path: snapshot.file_name().unwrap().to_string_lossy().into_owned(),
            size: fs::metadata(&blob).unwrap().len(),
            sha256: artifact::sha256(&blob).unwrap(),
        };
        assert!(valid_file(&snapshot, &file).unwrap());
        fs::write(blob, b"corrupted data").unwrap();
        assert!(!valid_file(&snapshot, &file).unwrap());
    }
    #[test]
    fn all_ladder_models_have_pinned_base_weights() {
        let ladder: Ladder =
            serde_json::from_str(include_str!("../../../../ci/inference-ladder.json")).unwrap();
        for model in ladder.models {
            let fixture = load(&model.id).unwrap();
            assert_eq!(fixture.revision.len(), 40);
            assert!(!fixture.files.is_empty());
            assert!(
                fixture
                    .tokenizer_files
                    .iter()
                    .any(|file| file == "tokenizer.json")
            );
            for f in fixture.files {
                assert_eq!(f.sha256.len(), 64);
                assert!(f.size > 0);
                assert!(!f.path.starts_with("MTP/"));
            }
        }
    }
}
