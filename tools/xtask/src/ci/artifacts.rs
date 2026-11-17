use super::execution::{capture, strings};
use crate::command::DynResult;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::Read,
    path::{Path, PathBuf},
};

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub schema_version: u32,
    pub source: String,
    pub kind: String,
    pub target: String,
    pub variant: String,
    pub files: BTreeMap<String, String>,
}
pub fn digest(path: &Path) -> DynResult<String> {
    let mut file = fs::File::open(path)?;
    let mut state = Sha256::new();
    let mut buffer = [0_u8; 65536];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        state.update(&buffer[..count]);
    }
    Ok(hex::encode(state.finalize()))
}
pub fn source() -> DynResult<String> {
    if !capture(
        "git",
        &strings(&["status", "--porcelain", "--untracked-files=no"]),
        &[],
    )?
    .trim()
    .is_empty()
    {
        return Err("artifact provenance requires an unchanged tracked source tree".into());
    }
    Ok(capture("git", &strings(&["rev-parse", "HEAD"]), &[])?
        .trim()
        .to_owned())
}
pub fn write_json(path: &Path, value: &impl Serialize) -> DynResult<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, serde_json::to_vec_pretty(value)?)?;
    Ok(())
}
pub fn files(root: &Path) -> DynResult<BTreeMap<String, String>> {
    let mut result = BTreeMap::new();
    collect(root, root, &mut result)?;
    Ok(result)
}
fn collect(root: &Path, directory: &Path, result: &mut BTreeMap<String, String>) -> DynResult<()> {
    for entry in fs::read_dir(directory)? {
        let path = entry?.path();
        if !path.canonicalize()?.starts_with(root.canonicalize()?) {
            return Err("artifact link escapes its root".into());
        }
        if path.is_dir() {
            if fs::symlink_metadata(&path)?.file_type().is_symlink() {
                return Err("artifact directory symlinks are not admitted".into());
            }
            collect(root, &path, result)?;
        } else if path.is_file() {
            let relative = path
                .strip_prefix(root)?
                .to_str()
                .ok_or("non-Unicode artifact path")?
                .replace('\\', "/");
            result.insert(relative, digest(&path)?);
        } else {
            return Err("artifact contains unsupported filesystem entry".into());
        }
    }
    Ok(())
}
pub fn receipt(root: &Path, kind: &str, target: &str, variant: &str) -> DynResult<()> {
    let receipt = Receipt {
        schema_version: 1,
        source: source()?,
        kind: kind.into(),
        target: target.into(),
        variant: variant.into(),
        files: files(root)?,
    };
    write_json(&root.join("provenance.json"), &receipt)
}
pub fn verify(root: &Path, kind: &str, target: &str, variant: &str) -> DynResult<()> {
    let receipt: Receipt = serde_json::from_slice(&fs::read(root.join("provenance.json"))?)?;
    if receipt.schema_version != 1
        || receipt.source != source()?
        || receipt.kind != kind
        || receipt.target != target
        || receipt.variant != variant
    {
        return Err("artifact source/target/variant identity mismatch".into());
    }
    let mut actual = files(root)?;
    actual.remove("provenance.json");
    if actual != receipt.files {
        return Err("artifact file roster or checksum mismatch".into());
    }
    Ok(())
}
pub fn copy_tree(source: &Path, destination: &Path) -> DynResult<()> {
    fs::create_dir_all(destination)?;
    for relative in files(source)?.keys() {
        let output = destination.join(relative);
        if let Some(parent) = output.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::copy(source.join(relative), output)?;
    }
    Ok(())
}
pub fn archive(source: &Path, destination: &Path) -> DynResult<()> {
    let file = fs::File::create(destination)?;
    let gzip = flate2::write::GzEncoder::new(file, flate2::Compression::default());
    let mut archive = tar::Builder::new(gzip);
    archive.follow_symlinks(false);
    archive.append_dir_all(".", source)?;
    archive.into_inner()?.finish()?;
    fs::write(
        PathBuf::from(format!("{}.sha256", destination.display())),
        format!(
            "{}  {}\n",
            digest(destination)?,
            destination
                .file_name()
                .ok_or("missing filename")?
                .to_string_lossy()
        ),
    )?;
    Ok(())
}
pub fn unpack(directory: &Path, destination: &Path) -> DynResult<()> {
    let archives = fs::read_dir(directory)?
        .map(|entry| entry.map(|entry| entry.path()))
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .filter(|path| path.to_string_lossy().ends_with(".tar.gz"))
        .collect::<Vec<_>>();
    if archives.len() != 1 {
        return Err("expected exactly one producer archive".into());
    }
    let archive = &archives[0];
    let checksum = fs::read_to_string(PathBuf::from(format!("{}.sha256", archive.display())))?;
    let expected = format!(
        "{}  {}\n",
        digest(archive)?,
        archive
            .file_name()
            .ok_or("missing filename")?
            .to_string_lossy()
    );
    if checksum != expected {
        return Err("producer archive checksum mismatch".into());
    }
    fs::create_dir_all(destination)?;
    let mut tar = tar::Archive::new(flate2::read::GzDecoder::new(fs::File::open(archive)?));
    tar.unpack(destination)?;
    files(destination)?;
    Ok(())
}
pub fn binary(root: &Path, os: &str) -> PathBuf {
    root.join(if os == "windows" {
        "skippy.exe"
    } else {
        "skippy"
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn archive_detects_corruption() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("source");
        let input = temp.path().join("input");
        fs::create_dir_all(&source).unwrap();
        fs::create_dir_all(&input).unwrap();
        fs::write(source.join("test"), b"original").unwrap();
        let path = input.join("product.tar.gz");
        archive(&source, &path).unwrap();
        unpack(&input, &temp.path().join("good")).unwrap();
        fs::write(path, b"changed").unwrap();
        assert!(unpack(&input, &temp.path().join("bad")).is_err());
    }
}
