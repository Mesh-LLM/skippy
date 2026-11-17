//! Verify the manifest, binary identity and dependency closure of a runtime bundle.
use super::{dependencies, imports};
use crate::{artifact, command::DynResult};
use serde::Deserialize;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Component, Path, PathBuf},
};

#[derive(Deserialize)]
struct Manifest {
    schema_version: u32,
    runtime: Runtime,
    build: Build,
}
#[derive(Deserialize)]
struct Runtime {
    id: String,
    release_version: String,
    skippy_abi: String,
    platform: Platform,
    libraries: Vec<String>,
    files: BTreeMap<String, String>,
    tools: BTreeMap<String, String>,
}
#[derive(Deserialize)]
struct Platform {
    os: String,
    arch: String,
    target: String,
}
#[derive(Deserialize)]
struct Build {
    primary_library: String,
    library_sha256: String,
    llama_upstream_sha: String,
    llama_patched_sha: String,
    llama_patch_digest: String,
}

pub(crate) fn run(args: &[String]) -> DynResult<()> {
    let args = args.strip_prefix(&["--portable".into()]).unwrap_or(args);
    let [input] = args else {
        return Err("verify-runtime-package [--portable] DIRECTORY_OR_ARCHIVE".into());
    };
    let input = Path::new(input);
    if input.is_dir() {
        return verify(input);
    }
    let directory = tempfile::tempdir()?;
    let decoder = flate2::read::GzDecoder::new(std::fs::File::open(input)?);
    let mut archive = tar::Archive::new(decoder);
    let mut seen = BTreeSet::new();
    for entry in archive.entries()? {
        let mut entry = entry?;
        let path = entry.path()?.into_owned();
        safe_path(&path)?;
        if !seen.insert(path.clone()) {
            return Err("duplicate runtime archive path".into());
        }
        let kind = entry.header().entry_type();
        if kind.is_hard_link() {
            let target = entry
                .link_name()?
                .ok_or("runtime hardlink has no target")?
                .into_owned();
            safe_path(&target)?;
            let source = contained_file(
                directory.path(),
                target.to_str().ok_or("non-UTF8 archive path")?,
            )?;
            let destination = directory.path().join(&path);
            std::fs::create_dir_all(destination.parent().ok_or("missing archive parent")?)?;
            std::fs::copy(source, destination)?;
            continue;
        }
        if !(kind.is_file() || kind.is_dir()) {
            return Err("runtime archive contains a link or special file".into());
        }
        if !entry.unpack_in(directory.path())? {
            return Err("runtime archive escapes extraction directory".into());
        }
    }
    let roots = std::fs::read_dir(directory.path())?.collect::<Result<Vec<_>, _>>()?;
    if roots.len() != 1 || !roots[0].file_type()?.is_dir() {
        return Err("runtime archive must contain one root directory".into());
    }
    verify(&roots[0].path())
}

pub(crate) fn verify(root: &Path) -> DynResult<()> {
    let root = root.canonicalize()?;
    let manifest: Manifest = serde_json::from_slice(&std::fs::read(root.join("manifest.json"))?)?;
    let runtime = &manifest.runtime;
    if manifest.schema_version != 2
        || !runtime.id.starts_with("skippy-")
        || runtime.release_version.is_empty()
        || runtime.skippy_abi.is_empty()
    {
        return Err("invalid standalone runtime identity".into());
    }
    super::manifest_identity::verify(
        &runtime.platform.target,
        &runtime.platform.os,
        &runtime.platform.arch,
    )?;
    for sha in [
        &manifest.build.llama_upstream_sha,
        &manifest.build.llama_patched_sha,
    ] {
        if sha.len() != 40 || !sha.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("invalid native source SHA".into());
        }
    }
    if manifest.build.llama_patch_digest.len() != 64 {
        return Err("invalid patch digest".into());
    }
    let mut names = BTreeSet::new();
    for (relative, digest) in runtime.files.iter().chain(&runtime.tools) {
        let path = contained_file(&root, relative)?;
        if artifact::sha256(&path)? != *digest {
            return Err(format!("runtime checksum mismatch: {relative}").into());
        }
        names.insert(
            path.file_name()
                .ok_or("missing native file name")?
                .to_string_lossy()
                .to_ascii_lowercase(),
        );
    }
    if runtime.libraries.is_empty()
        || !runtime.libraries.contains(&manifest.build.primary_library)
        || runtime.files.get(&manifest.build.primary_library)
            != Some(&manifest.build.library_sha256)
    {
        return Err("invalid native primary library".into());
    }
    for relative in runtime.libraries.iter().chain(runtime.tools.keys()) {
        if !runtime.files.contains_key(relative) && !runtime.tools.contains_key(relative) {
            return Err(format!("unhashed runtime library: {relative}").into());
        }
        let binary = imports::inspect(&contained_file(&root, relative)?)?;
        if binary.arch != runtime.platform.arch {
            return Err(format!("runtime architecture mismatch: {relative}").into());
        }
        for import in binary.imports {
            let name = import
                .rsplit(['/', '\\'])
                .next()
                .unwrap_or(&import)
                .to_ascii_lowercase();
            if !dependencies::host_owned(&import, binary.format) && !names.contains(&name) {
                return Err(format!("unpackaged native dependency: {relative} -> {import}").into());
            }
            if binary.format == "macho"
                && !dependencies::host_owned(&import, binary.format)
                && !(import.starts_with("@rpath/") || import.starts_with("@loader_path/"))
            {
                return Err(format!("nonrelocatable native import: {import}").into());
            }
        }
    }
    println!("verified runtime {}", runtime.id);
    Ok(())
}
fn safe_path(path: &Path) -> DynResult<()> {
    if path.as_os_str().is_empty()
        || path
            .components()
            .any(|part| !matches!(part, Component::Normal(_) | Component::CurDir))
    {
        return Err(format!("unsafe runtime path: {}", path.display()).into());
    }
    Ok(())
}
fn contained_file(root: &Path, relative: &str) -> DynResult<PathBuf> {
    let root = root.canonicalize()?;
    let path = Path::new(relative);
    safe_path(path)?;
    let source = root.join(path);
    if source.symlink_metadata()?.file_type().is_symlink() {
        return Err("runtime manifest file is a symlink".into());
    }
    let source = source.canonicalize()?;
    if !source.starts_with(&root) || !source.is_file() {
        return Err(format!("runtime file escapes bundle: {relative}").into());
    }
    Ok(source)
}
#[cfg(test)]
mod tests {
    #[test]
    fn archive_paths_cannot_escape_root() {
        for value in ["../outside", "/outside", "a/../../outside"] {
            assert!(super::safe_path(std::path::Path::new(value)).is_err());
        }
        assert!(super::safe_path(std::path::Path::new("lib/llama.dll")).is_ok());
    }
}
