//! Read candidate objects without checking out or executing candidate source.
use crate::{command::DynResult, execution};
use std::{collections::BTreeMap, path::Path};

pub(in crate::inference) trait Source {
    fn read(&mut self, revision: &str, path: &str) -> DynResult<Option<String>>;
}

pub(super) fn relative(directory: &str, path: &str) -> DynResult<String> {
    let joined = Path::new(directory).join(path);
    let mut parts = Vec::new();
    for part in joined.components() {
        match part {
            std::path::Component::Normal(value) => {
                parts.push(value.to_str().ok_or("non-UTF-8 input path")?);
            }
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir if !parts.is_empty() => {
                parts.pop();
            }
            _ => return Err("inference input escapes repository".into()),
        }
    }
    Ok(parts.join("/"))
}

pub(in crate::inference) struct GitSource {
    directory: tempfile::TempDir,
    trees: BTreeMap<String, BTreeMap<String, (String, String)>>,
    blobs: BTreeMap<String, String>,
}

impl GitSource {
    pub(in crate::inference) fn new(base: &str, head: &str) -> DynResult<Self> {
        Self::load(
            base,
            head,
            "https://github.com/general-inference/skippy.git",
            &[("GH_TOKEN", std::env::var("GH_TOKEN")?)],
        )
    }

    pub(super) fn local(base: &str, head: &str) -> DynResult<Self> {
        let directory = execution::capture(
            "git",
            &["rev-parse".into(), "--absolute-git-dir".into()],
            &[],
        )?;
        Self::load(base, head, directory.trim(), &[])
    }

    fn load(base: &str, head: &str, remote: &str, env: &[(&str, String)]) -> DynResult<Self> {
        for sha in [base, head] {
            if sha.len() != 40 || !sha.bytes().all(|b| b.is_ascii_hexdigit()) {
                return Err("input revision must be an immutable SHA".into());
            }
        }
        let directory = tempfile::tempdir()?;
        let mut source = Self {
            directory,
            trees: BTreeMap::new(),
            blobs: BTreeMap::new(),
        };
        source.git(&["init", "--bare", "--quiet"], &[])?;
        source.git(
            &[
                "-c",
                "credential.helper=",
                "-c",
                "credential.helper=!gh auth git-credential",
                "fetch",
                "--quiet",
                "--no-tags",
                "--no-recurse-submodules",
                "--depth=1",
                remote,
                base,
                head,
            ],
            env,
        )?;
        for revision in [base, head] {
            let listing = source.git(&["ls-tree", "-r", "-z", revision], &[])?;
            let mut tree = BTreeMap::new();
            for entry in listing.split('\0').filter(|entry| !entry.is_empty()) {
                let (metadata, path) = entry.split_once('\t').ok_or("invalid git tree entry")?;
                let parts: Vec<_> = metadata.split_whitespace().collect();
                if parts.len() != 3 {
                    return Err("invalid git tree metadata".into());
                }
                tree.insert(path.to_owned(), (parts[0].to_owned(), parts[2].to_owned()));
            }
            source.trees.insert(revision.to_owned(), tree);
        }
        Ok(source)
    }

    pub(super) fn changes(&self, base: &str, head: &str) -> DynResult<serde_json::Value> {
        let names = self.git(
            &["diff", "--name-only", "-z", "--no-renames", base, head],
            &[],
        )?;
        Ok(serde_json::json!([names
            .split('\0')
            .filter(|name| !name.is_empty())
            .map(|name| serde_json::json!({"filename":name}))
            .collect::<Vec<_>>()]))
    }

    fn git(&self, arguments: &[&str], env: &[(&str, String)]) -> DynResult<String> {
        let mut args = vec![
            "--git-dir".to_owned(),
            self.directory.path().display().to_string(),
        ];
        args.extend(arguments.iter().map(|arg| (*arg).to_owned()));
        execution::capture("git", &args, env)
    }
}

impl Source for GitSource {
    fn read(&mut self, revision: &str, path: &str) -> DynResult<Option<String>> {
        if relative("", path)? != path {
            return Err("non-canonical inference input path".into());
        }
        let tree = self.trees.get(revision).ok_or("unknown input revision")?;
        let Some((mode, blob)) = tree.get(path) else {
            return Ok(None);
        };
        if !matches!(mode.as_str(), "100644" | "100755") {
            return Err(format!("inference input is not a regular file: {path}").into());
        }
        let blob = blob.clone();
        if !self.blobs.contains_key(&blob) {
            let bytes = self.git(&["cat-file", "blob", &blob], &[])?;
            self.blobs.insert(blob.clone(), bytes);
        }
        Ok(self.blobs.get(&blob).cloned())
    }
}
