use super::{DynResult, contract::is_digest, raw};
use crate::{artifact, cache_qualification::MANIFEST, execution};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{env, fs, path::Path};

#[derive(Serialize)]
pub(super) struct Checkout {
    pub(super) source_sha: String,
    pub(super) source_tree_clean: bool,
    pub(super) llama_pin: String,
    pub(super) patch_queue_sha256: String,
    pub(super) model_manifest_sha256: String,
    pub(super) abi_version: String,
    pub(super) cache_format_version: u32,
    pub(super) harness_sha256: String,
    pub(super) agentx_scorer_sha256: String,
}

impl Checkout {
    pub(super) fn load(manifest: &str) -> DynResult<Self> {
        let root = env::current_dir()?.canonicalize()?;
        let pin_file = root.join("llama_cpp/upstream.txt");
        let pin = fs::read_to_string(&pin_file)?.trim().to_owned();
        let patch_queue_digest = patch_queue_sha256(&root, &pin_file)?;
        let source_sha = execution::output("git", &execution::strings(&["rev-parse", "HEAD"]))?;
        let source_sha = source_sha.trim().to_owned();
        if !is_digest(&source_sha, 40) || !is_digest(&pin, 40) {
            return Err("invalid checkout source or llama.cpp revision".into());
        }
        let status = execution::output(
            "git",
            &execution::strings(&["status", "--porcelain=v1", "--untracked-files=all"]),
        )?;
        Ok(Self {
            source_sha,
            source_tree_clean: status.trim().is_empty(),
            llama_pin: pin,
            patch_queue_sha256: patch_queue_digest,
            model_manifest_sha256: artifact::digest(manifest.as_bytes())?,
            abi_version: execution::output(
                "python3",
                &execution::strings(&[
                    "scripts/native-source.py",
                    "package-source-version",
                    "abi",
                    "crates/skippy-ffi/src/lib.rs",
                ]),
            )?
            .trim()
            .to_owned(),
            cache_format_version: cache_format_version(&fs::read_to_string(
                "crates/skippy-cache/src/l3.rs",
            )?)?,
            harness_sha256: harness_sha256(&root)?,
            agentx_scorer_sha256: raw::scorer_sha256()?,
        })
    }

    pub(super) fn print() -> DynResult<()> {
        let manifest = fs::read_to_string(MANIFEST)?;
        println!("{}", serde_json::to_string_pretty(&Self::load(&manifest)?)?);
        Ok(())
    }
}

fn patch_queue_sha256(root: &Path, pin: &Path) -> DynResult<String> {
    let output = execution::capture(
        "bash",
        &execution::strings(&["scripts/prepare-llama.sh", "--patch-digest"]),
        &[
            (
                "LLAMA_PATCH_DIR",
                root.join("llama_cpp/patches").display().to_string(),
            ),
            ("LLAMA_PIN_FILE", pin.display().to_string()),
        ],
    )?;
    let digest = output.trim();
    if !is_digest(digest, 64) {
        return Err("native preparation returned an invalid patch queue digest".into());
    }
    Ok(digest.to_owned())
}

fn harness_sha256(root: &Path) -> DynResult<String> {
    let inputs = [
        "Justfile",
        "tools/xtask/Cargo.toml",
        "tools/xtask/src/cache_parity.rs",
        "tools/xtask/src/cache_parity",
        "tools/xtask/src/execution.rs",
        "tools/xtask/native/metal_memory_probe.m",
        "tools/process-supervision",
        "ci/cache-qualification/parity.schema.json",
        "ci/cache-qualification/raw-trial.schema.json",
        "ci/cache-qualification/workload.schema.json",
    ];
    let mut paths = Vec::new();
    for relative in inputs {
        inventory_harness(root, &root.join(relative), &mut paths)?;
    }
    paths.sort();
    let mut digest = Sha256::new();
    for path in paths {
        digest.update(path.as_bytes());
        digest.update([0]);
        digest.update(fs::read(root.join(path))?);
        digest.update([0]);
    }
    Ok(hex::encode(digest.finalize()))
}

fn inventory_harness(root: &Path, path: &Path, paths: &mut Vec<String>) -> DynResult<()> {
    let kind = path.symlink_metadata()?.file_type();
    if kind.is_file() {
        paths.push(
            path.strip_prefix(root)?
                .to_str()
                .ok_or("non-UTF8 harness path")?
                .replace('\\', "/"),
        );
    } else if kind.is_dir() {
        for entry in fs::read_dir(path)? {
            inventory_harness(root, &entry?.path(), paths)?;
        }
    } else {
        return Err("benchmark harness contains a symlink or special file".into());
    }
    Ok(())
}

fn cache_format_version(source: &str) -> DynResult<u32> {
    let mut declarations = source.lines().filter_map(|line| {
        line.trim()
            .strip_prefix("pub const MANIFEST_VERSION: u32 = ")
    });
    let value = declarations
        .next()
        .ok_or("missing KV cache manifest version")?;
    let digits = value
        .strip_suffix(';')
        .ok_or("invalid KV cache manifest version")?;
    let version = digits.parse()?;
    if declarations.next().is_some() || version == 0 {
        return Err("invalid or duplicate KV cache manifest version".into());
    }
    Ok(version)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cache_manifest_version_requires_one_nonzero_declaration() {
        let version = "pub const MANIFEST_VERSION: u32 = 4;";
        assert_eq!(cache_format_version(version).unwrap(), 4);
        assert!(cache_format_version("pub const LEGACY_MANIFEST_VERSION: u32 = 4;").is_err());
        assert!(cache_format_version("pub const MANIFEST_VERSION: u32 = 0;").is_err());
        assert!(cache_format_version(&format!("{version}\n{version}")).is_err());
    }
}
