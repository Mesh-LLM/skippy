use crate::{command::DynResult, execution};
use std::path::{Path, PathBuf};

pub(crate) fn download(
    repo: &str,
    revision: &str,
    files: &[&str],
    timeout_seconds: u64,
) -> DynResult<PathBuf> {
    println!(
        "Resolving Hugging Face fixtures {repo}@{revision}: {}",
        files.join(", ")
    );
    let mut args = execution::strings(&["download", repo, "--revision", revision, "--quiet"]);
    args.extend(files.iter().map(|file| (*file).to_owned()));
    let output = execution::capture_for(
        "hf",
        &args,
        &[("HF_HUB_DISABLE_XET", "1".into())],
        timeout_seconds,
    )?;
    downloaded_path(&output)
}

fn downloaded_path(text: &str) -> DynResult<PathBuf> {
    for line in text.lines().rev() {
        let line = line.trim();
        let candidate = line
            .strip_prefix("path:")
            .or_else(|| line.strip_prefix("path="))
            .map(str::trim)
            .unwrap_or(line);
        if Path::new(candidate).is_absolute() && !candidate.contains('\u{1b}') {
            return Ok(PathBuf::from(candidate));
        }
    }
    Err("hf download returned no absolute artifact path".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn download_paths_support_legacy_and_formatted_cli_output() {
        let file = if cfg!(windows) {
            "C:/models/a.gguf"
        } else {
            "/models/a.gguf"
        };
        assert_eq!(downloaded_path(file).unwrap(), PathBuf::from(file));
        assert_eq!(
            downloaded_path(&format!(
                "\u{1b}[32m✓ Downloaded\u{1b}[0m\n  path: {file}\n"
            ))
            .unwrap(),
            PathBuf::from(file)
        );
        assert!(downloaded_path("Downloaded successfully").is_err());
        assert!(downloaded_path("path: relative.gguf").is_err());
        assert_eq!(
            downloaded_path(&format!("path={file}\n")).unwrap(),
            PathBuf::from(file)
        );
    }
}
