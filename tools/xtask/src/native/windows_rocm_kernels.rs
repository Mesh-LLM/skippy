//! Advertised Windows ROCm targets require checksum-bound rocBLAS code objects.
use crate::command::DynResult;
use std::{collections::BTreeMap, ffi::OsStr, fs, io::Write, path::Path};

const LIBRARY: &str = "lib/rocblas/library";

pub(super) fn admit(
    os: &str,
    root: &Path,
    files: &mut BTreeMap<String, String>,
    targets: Vec<String>,
) -> DynResult<Vec<String>> {
    if os != "windows"
        || targets.is_empty()
        || !files
            .keys()
            .any(|path| path.eq_ignore_ascii_case("lib/rocblas.dll"))
    {
        return Ok(targets);
    }
    let paths = code_objects(root)?;
    // Use the same contained regular-file validation and hashing as the DLLs.
    // The manifest consumers then verify the objects that justify admission.
    let objects = super::files::hashes(root, paths.iter())?;
    let (kept, dropped): (Vec<_>, Vec<_>) = targets.into_iter().partition(|target| {
        objects.keys().any(|path| {
            path.rsplit('/').next().is_some_and(|name| {
                name.match_indices(target.as_str()).any(|(start, _)| {
                    let end = start + target.len();
                    let bytes = name.as_bytes();
                    (start == 0 || !bytes[start - 1].is_ascii_alphanumeric())
                        && (end == bytes.len() || !bytes[end].is_ascii_alphanumeric())
                })
            })
        })
    });
    if kept.is_empty() {
        return Err(format!(
            "no rocBLAS kernels for any ROCm target ({}) in {LIBRARY}; the runtime would match no GPU",
            dropped.join(" ")
        )
        .into());
    }
    if !dropped.is_empty() {
        writeln!(
            std::io::stderr(),
            "not advertising {}: no packaged rocBLAS code objects in {LIBRARY}",
            dropped.join(" ")
        )?;
    }
    files.extend(objects);
    Ok(kept)
}

fn code_objects(root: &Path) -> DynResult<Vec<String>> {
    let entries = fs::read_dir(root.join(LIBRARY)).map_err(|error| {
        format!("cannot inspect packaged rocBLAS kernels in {LIBRARY}: {error}")
    })?;
    let mut paths = Vec::new();
    for entry in entries {
        if let Some(path) = code_object_path(&entry?.file_name())? {
            paths.push(path);
        }
    }
    paths.sort();
    Ok(paths)
}

fn code_object_path(name: &OsStr) -> DynResult<Option<String>> {
    let name = name
        .to_str()
        .ok_or("packaged rocBLAS kernel filename is not UTF-8")?;
    Ok(Path::new(name)
        .extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| {
            extension.eq_ignore_ascii_case("co") || extension.eq_ignore_ascii_case("hsaco")
        })
        .then(|| format!("{LIBRARY}/{name}")))
}
