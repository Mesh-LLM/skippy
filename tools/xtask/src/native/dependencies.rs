//! Native runtime dependency closure, reviewed redistribution and load ordering.
use super::imports::{self, Binary};
use crate::{artifact, command::DynResult};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

struct Package {
    lib: PathBuf,
    scan: Vec<PathBuf>,
    search: Vec<PathBuf>,
    arch: Option<String>,
    cuda_major: Option<String>,
    primary: Option<String>,
    format: &'static str,
}

pub(crate) fn run(args: &[String], format: &'static str) -> DynResult<()> {
    let (verb, args) = args.split_first().ok_or("missing dependency command")?;
    let package = parse(args, format)?;
    match verb.as_str() {
        "collect" => collect(&package),
        "verify" => verify(&package).map(|_| ()),
        "order" => order(&package),
        _ => Err(format!("unknown native dependency command: {verb}").into()),
    }
}
fn parse(args: &[String], format: &'static str) -> DynResult<Package> {
    let (pairs, tail) = args.as_chunks::<2>();
    if !tail.is_empty() {
        return Err("native dependency options need values".into());
    }
    let mut lib = None;
    let (mut scan, mut search, mut arch, mut cuda_major, mut primary) =
        (Vec::new(), Vec::new(), None, None, None);
    for [key, value] in pairs {
        match key.as_str() {
            "--lib-dir" if lib.is_none() => lib = Some(PathBuf::from(value)),
            "--scan-dir" => scan.push(PathBuf::from(value)),
            "--search-dir" => search.push(PathBuf::from(value)),
            "--arch" if arch.is_none() => arch = Some(value.clone()),
            "--cuda-major" if cuda_major.is_none() => cuda_major = Some(value.clone()),
            "--primary" if primary.is_none() => primary = Some(value.clone()),
            _ => return Err(format!("unknown or duplicate option: {key}").into()),
        }
    }
    if format == "pe" {
        search.extend(std::env::split_paths(
            &std::env::var_os("PATH").unwrap_or_default(),
        ));
    }
    Ok(Package {
        lib: lib.ok_or("missing --lib-dir")?,
        scan,
        search,
        arch,
        cuda_major,
        primary,
        format,
    })
}

fn files(root: &Path, recursive: bool) -> DynResult<Vec<PathBuf>> {
    let mut paths = Vec::new();
    if !root.exists() {
        return Ok(paths);
    }
    for entry in std::fs::read_dir(root)? {
        let entry = entry?;
        let kind = entry.file_type()?;
        if kind.is_dir() && recursive {
            paths.extend(files(&entry.path(), true)?);
        } else if kind.is_file() || (kind.is_symlink() && entry.path().is_file()) {
            paths.push(entry.path());
        }
    }
    paths.sort();
    Ok(paths)
}
fn is_binary(path: &Path, format: &str) -> DynResult<bool> {
    use std::io::Read;
    let mut bytes = [0; 4];
    let count = std::fs::File::open(path)?.read(&mut bytes)?;
    Ok(count == 4
        && if format == "elf" {
            bytes == *b"\x7fELF"
        } else {
            bytes[..2] == *b"MZ"
        })
}
fn inspect_files(package: &Package) -> DynResult<Vec<(PathBuf, Binary)>> {
    let mut paths = files(&package.lib, true)?;
    for directory in &package.scan {
        paths.extend(files(directory, true)?);
    }
    let mut images = Vec::new();
    for path in paths {
        if !is_binary(&path, package.format)? {
            continue;
        }
        let image = imports::inspect(&path)?;
        if package.arch.as_ref().is_some_and(|arch| arch != image.arch) {
            return Err(format!("wrong architecture: {}", path.display()).into());
        }
        images.push((path, image));
    }
    if images.is_empty() {
        return Err("runtime contains no native binaries".into());
    }
    Ok(images)
}
fn key(name: &str, format: &str) -> String {
    if format == "pe" {
        name.to_ascii_lowercase()
    } else {
        name.into()
    }
}
fn name(path: &Path) -> DynResult<&str> {
    path.file_name()
        .and_then(|p| p.to_str())
        .ok_or_else(|| "non-UTF8 native filename".into())
}

fn package_index(
    package: &Package,
    images: &[(PathBuf, Binary)],
) -> DynResult<BTreeMap<String, PathBuf>> {
    let mut index = BTreeMap::new();
    for (path, image) in images {
        if !path.starts_with(&package.lib) {
            continue;
        }
        for alias in std::iter::once(name(path)?).chain(image.soname.as_deref()) {
            if let Some(previous) = index.insert(key(alias, package.format), path.clone())
                && artifact::sha256(&previous)? != artifact::sha256(path)?
            {
                return Err(format!("conflicting native providers for {alias}").into());
            }
        }
    }
    Ok(index)
}

fn gaps(package: &Package) -> DynResult<BTreeSet<String>> {
    let images = inspect_files(package)?;
    let index = package_index(package, &images)?;
    let mut missing = BTreeSet::new();
    for (_, image) in images {
        for dependency in image.imports {
            if !host_owned(&dependency, package.format)
                && !index.contains_key(&key(&dependency, package.format))
            {
                missing.insert(dependency);
            }
        }
    }
    Ok(missing)
}
fn verify(package: &Package) -> DynResult<Vec<(PathBuf, Binary)>> {
    let missing = gaps(package)?;
    if !missing.is_empty() {
        return Err(format!("unpackaged native dependencies: {missing:?}").into());
    }
    inspect_files(package)
}
fn collect(package: &Package) -> DynResult<()> {
    let candidates = package
        .search
        .iter()
        .map(|dir| files(dir, package.format == "elf"))
        .collect::<DynResult<Vec<_>>>()?
        .into_iter()
        .flatten()
        .collect::<Vec<_>>();
    for _ in 0..256 {
        let missing = gaps(package)?;
        if missing.is_empty() {
            return Ok(());
        }
        for dependency in missing {
            if package.format == "elf" {
                admit_cuda(
                    &dependency,
                    package.cuda_major.as_deref().ok_or("missing CUDA major")?,
                )?;
            }
            let mut selected: Option<PathBuf> = None;
            for path in &candidates {
                if path.components().any(|p| {
                    p.as_os_str()
                        .to_string_lossy()
                        .eq_ignore_ascii_case("stubs")
                }) || key(name(path)?, package.format) != key(&dependency, package.format)
                {
                    continue;
                }
                let image = imports::inspect(path)?;
                if package.arch.as_ref().is_some_and(|arch| arch != image.arch) {
                    continue;
                }
                if let Some(previous) = &selected {
                    if artifact::sha256(previous)? != artifact::sha256(path)? {
                        return Err(
                            format!("conflicting dependency providers: {dependency}").into()
                        );
                    }
                } else {
                    selected = Some(path.clone());
                }
            }
            let source =
                selected.ok_or_else(|| format!("unresolved native dependency: {dependency}"))?;
            let destination = package.lib.join(&dependency);
            if destination.is_symlink() {
                return Err("dependency destination is a symlink".into());
            }
            std::fs::copy(source, destination)?;
        }
    }
    Err("native dependency closure exceeded 256 passes".into())
}
fn admit_cuda(name: &str, major: &str) -> DynResult<()> {
    if !matches!(major, "12" | "13") {
        return Err("unsupported CUDA toolkit major".into());
    }
    if ["libcudart", "libcublas", "libcublasLt", "libnvJitLink"]
        .iter()
        .any(|stem| {
            name.strip_prefix(&format!("{stem}.so.{major}"))
                .is_some_and(|tail| tail.is_empty() || tail.starts_with('.'))
        })
    {
        return Ok(());
    }
    Err(format!("unreviewed CUDA redistributable: {name}").into())
}
fn order(package: &Package) -> DynResult<()> {
    let images = verify(package)?;
    let index = package_index(package, &images)?;
    let mut ordered = Vec::new();
    let mut visiting = BTreeSet::new();
    let mut visited = BTreeSet::new();
    let primary = package.primary.as_deref().ok_or("missing --primary")?;
    if !index.contains_key(primary) {
        return Err("missing primary native library".into());
    }
    for (path, _) in &images {
        if path.parent() == Some(&package.lib) && name(path)? != primary {
            visit(
                path,
                &images,
                &index,
                &mut visiting,
                &mut visited,
                &mut ordered,
            )?;
        }
    }
    visit(
        index.get(primary).ok_or("missing primary")?,
        &images,
        &index,
        &mut visiting,
        &mut visited,
        &mut ordered,
    )?;
    for path in ordered {
        println!("{}", name(&path)?);
    }
    Ok(())
}
fn visit(
    path: &Path,
    images: &[(PathBuf, Binary)],
    index: &BTreeMap<String, PathBuf>,
    visiting: &mut BTreeSet<PathBuf>,
    visited: &mut BTreeSet<PathBuf>,
    ordered: &mut Vec<PathBuf>,
) -> DynResult<()> {
    if visited.contains(path) {
        return Ok(());
    }
    if !visiting.insert(path.into()) {
        return Err("cyclic native dependency graph".into());
    }
    let image = images
        .iter()
        .find(|(p, _)| p == path)
        .ok_or("missing dependency image")?;
    for dependency in &image.1.imports {
        if let Some(next) = index.get(dependency) {
            visit(next, images, index, visiting, visited, ordered)?;
        }
    }
    visiting.remove(path);
    visited.insert(path.into());
    ordered.push(path.into());
    Ok(())
}

pub(super) fn host_owned(name: &str, format: &str) -> bool {
    if format == "macho" {
        return name.starts_with("/usr/lib/") || name.starts_with("/System/Library/");
    }
    let name = name.to_ascii_lowercase();
    if format == "pe" {
        return name.starts_with("api-ms-win-")
            || name.starts_with("ext-ms-win-")
            || [
                "advapi32.dll",
                "bcrypt.dll",
                "cfgmgr32.dll",
                "comdlg32.dll",
                "crypt32.dll",
                "d3d12.dll",
                "dbghelp.dll",
                "dxgi.dll",
                "gdi32.dll",
                "kernel32.dll",
                "msvcp140.dll",
                "msvcrt.dll",
                "nvcuda.dll",
                "ntdll.dll",
                "ole32.dll",
                "oleaut32.dll",
                "psapi.dll",
                "rpcrt4.dll",
                "secur32.dll",
                "setupapi.dll",
                "shell32.dll",
                "shlwapi.dll",
                "ucrtbase.dll",
                "user32.dll",
                "version.dll",
                "vcruntime140.dll",
                "vcruntime140_1.dll",
                "vulkan-1.dll",
                "winmm.dll",
                "ws2_32.dll",
            ]
            .contains(&name.as_str());
    }
    name.starts_with("libnvidia-")
        || name.starts_with("linux-vdso")
        || [
            "ld-linux.so.2",
            "ld-linux-x86-64.so.2",
            "ld-linux-aarch64.so.1",
            "libc.so.6",
            "libcrypt.so.1",
            "libdl.so.2",
            "libgcc_s.so.1",
            "libatomic.so.1",
            "libgomp.so.1",
            "libm.so.6",
            "libnsl.so.1",
            "libnuma.so.1",
            "libpthread.so.0",
            "libresolv.so.2",
            "librt.so.1",
            "libstdc++.so.6",
            "libutil.so.1",
            "libz.so.1",
            "libcuda.so",
            "libcuda.so.1",
        ]
        .contains(&name.as_str())
}

#[cfg(test)]
mod tests {
    #[test]
    fn driver_libraries_stay_host_owned_but_toolkits_must_be_packaged() {
        assert!(super::host_owned("libcuda.so.1", "elf"));
        assert!(!super::host_owned("libcudart.so.12", "elf"));
        assert!(super::host_owned("KERNEL32.dll", "pe"));
        assert!(!super::host_owned("cudart64_13.dll", "pe"));
    }
    #[test]
    fn redistribution_requires_matching_major_and_reviewed_library() {
        assert!(super::admit_cuda("libcublas.so.13", "13").is_ok());
        assert!(super::admit_cuda("libcublas.so.12", "13").is_err());
        assert!(super::admit_cuda("libcuda.so.1", "12").is_err());
    }
}
