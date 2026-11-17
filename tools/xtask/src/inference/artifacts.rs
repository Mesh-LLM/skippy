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
    let directory = PathBuf::from("target/inference-models").join(key);
    fs::create_dir_all(&directory)?;
    let mut paths = Vec::new();
    for file in fixture.files {
        let args = [
            "download".into(),
            fixture.repo.clone(),
            "--revision".into(),
            fixture.revision.clone(),
            file.path.clone(),
        ];
        let cached = std::env::var_os("HF_HUB_CACHE").map(|root| {
            PathBuf::from(root)
                .join(format!("models--{}", fixture.repo.replace('/', "--")))
                .join("snapshots")
                .join(&fixture.revision)
                .join(&file.path)
        });
        println!(
            "Admitting {}@{}: {}",
            fixture.repo, fixture.revision, file.path
        );
        let path = match cached.filter(|path| path.is_file()) {
            Some(path) => path,
            None => {
                // Stream cold-download progress into durable JY logs. The second
                // invocation resolves the now-cached path without redownloading.
                // Stream directly into the shared NFS cache rather than using
                // Xet's chunk cache there. Keep the outer five-hour JY lease as
                // the resource bound, including waits behind another writer.
                execution::run("hf", &args, &[("HF_HUB_DISABLE_XET", "1".into())], 18000)?;
                let mut resolve = args.to_vec();
                resolve.push("--quiet".into());
                downloaded_path(&execution::capture_for("hf", &resolve, &[], 180)?)?
            }
        };
        let metadata = fs::metadata(&path)
            .map_err(|error| format!("downloaded model path {}: {error}", path.display()))?;
        if metadata.len() != file.size || artifact::sha256(&path)? != file.sha256 {
            return Err("model artifact digest mismatch".into());
        }
        let name = Path::new(&file.path)
            .file_name()
            .ok_or("model shard filename missing")?;
        let admitted = directory.join(name);
        stage(&path, &admitted)?;
        paths.push(admitted.canonicalize()?);
    }
    paths
        .into_iter()
        .next()
        .ok_or_else(|| "empty fixture".into())
}
fn stage(source: &Path, destination: &Path) -> DynResult<()> {
    if destination.exists() {
        fs::remove_file(destination)?;
    }
    // Preserve shard filenames without exposing nonstandard cache symlinks to
    // the serving loader. Hard links avoid copying weights on the same volume.
    if fs::hard_link(source.canonicalize()?, destination).is_err() {
        fs::copy(source, destination)?;
    }
    Ok(())
}
pub fn downloaded_path(text: &str) -> DynResult<PathBuf> {
    for line in text.lines().rev() {
        let line = line.trim();
        let candidate = line
            .strip_prefix("path:")
            .or_else(|| line.strip_prefix("path="))
            .map(str::trim)
            .unwrap_or(line);
        if candidate.starts_with('/') && !candidate.contains('\u{1b}') {
            return Ok(PathBuf::from(candidate));
        }
    }
    Err("hf download returned no absolute artifact path".into())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[cfg(unix)]
    fn admitted_shards_are_regular_files_despite_external_cache_links() {
        let root = tempfile::tempdir().unwrap();
        let blob = root.path().join("shared-blob");
        fs::write(&blob, b"admitted bytes").unwrap();
        let snapshot = root.path().join("snapshot.gguf");
        std::os::unix::fs::symlink(&blob, &snapshot).unwrap();
        let admitted = root.path().join("model-00001-of-00002.gguf");
        stage(&snapshot, &admitted).unwrap();
        assert!(fs::symlink_metadata(&admitted).unwrap().is_file());
        assert_eq!(fs::read(admitted).unwrap(), b"admitted bytes");
        assert!(blob.exists());
    }
    #[test]
    fn download_paths_support_legacy_and_formatted_cli_output() {
        assert_eq!(
            downloaded_path("/models/a.gguf\n").unwrap(),
            PathBuf::from("/models/a.gguf")
        );
        assert_eq!(
            downloaded_path("\u{1b}[32m✓ Downloaded\u{1b}[0m\n  path: /models/a.gguf\n").unwrap(),
            PathBuf::from("/models/a.gguf")
        );
        assert!(downloaded_path("Downloaded successfully").is_err());
        assert!(downloaded_path("path: relative.gguf").is_err());
        assert_eq!(
            downloaded_path("path=/models/a.gguf\n").unwrap(),
            PathBuf::from("/models/a.gguf")
        );
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
