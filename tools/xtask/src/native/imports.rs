//! Inspect real binary headers instead of parsing tool-dependent import listings.
use crate::{artifact, command::DynResult};
use goblin::{Object, mach::Mach};
use serde::Serialize;
use std::{collections::BTreeSet, path::Path};

#[derive(Debug)]
pub(super) struct Binary {
    pub format: &'static str,
    pub arch: &'static str,
    pub imports: Vec<String>,
    pub soname: Option<String>,
}

pub(super) fn inspect(path: &Path) -> DynResult<Binary> {
    let bytes = std::fs::read(path)?;
    let (format, arch, imports, soname) = match Object::parse(&bytes)? {
        Object::Elf(elf) => (
            "elf",
            match elf.header.e_machine {
                goblin::elf::header::EM_X86_64 => "x86_64",
                goblin::elf::header::EM_AARCH64 => "aarch64",
                _ => return Err("unsupported ELF architecture".into()),
            },
            elf.libraries.iter().map(|s| s.to_string()).collect(),
            elf.soname.map(str::to_owned),
        ),
        Object::PE(pe) => (
            "pe",
            match pe.header.coff_header.machine {
                0x8664 => "x86_64",
                0xaa64 => "aarch64",
                _ => return Err("unsupported PE architecture".into()),
            },
            pe.libraries.iter().map(|s| s.to_string()).collect(),
            None,
        ),
        Object::Mach(Mach::Binary(mach)) => (
            "macho",
            match mach.header.cputype {
                goblin::mach::constants::cputype::CPU_TYPE_ARM64 => "aarch64",
                goblin::mach::constants::cputype::CPU_TYPE_X86_64 => "x86_64",
                _ => return Err("unsupported Mach-O architecture".into()),
            },
            mach.libs.iter().skip(1).map(|s| s.to_string()).collect(),
            mach.name.map(str::to_owned),
        ),
        _ => return Err(format!("unsupported native binary: {}", path.display()).into()),
    };
    let imports: BTreeSet<String> = imports;
    Ok(Binary {
        format,
        arch,
        imports: imports.into_iter().collect(),
        soname,
    })
}

pub(super) fn forbidden(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    let base = lower.rsplit(['/', '\\']).next().unwrap_or(&lower);
    lower.contains("metal.framework")
        || [
            "libcuda",
            "libcublas",
            "libnccl",
            "libamdhip",
            "libhip",
            "libhsa-",
            "nvcuda",
            "cudart",
            "cublas",
            "amdhip",
            "hipblas",
            "rocblas",
            "libvulkan",
            "vulkan-",
            "ggml",
            "libggml",
            "llama",
            "libllama",
        ]
        .iter()
        .any(|prefix| base.starts_with(prefix))
}

#[derive(Serialize)]
struct HostReport {
    binary: String,
    binary_sha256: String,
    format: &'static str,
    arch: &'static str,
    policy: &'static str,
    imports: Vec<String>,
    rejected_imports: Vec<String>,
}

pub(crate) fn verify_host(args: &[String]) -> DynResult<()> {
    let mut binary = None;
    let mut format = None;
    let mut floor = None;
    let mut no_policy = false;
    let mut report_path = None;
    let mut rest = args;
    while let Some((arg, tail)) = rest.split_first() {
        rest = tail;
        match arg.as_str() {
            "--json" | "--bind-sha256" => {}
            "--no-import-policy" => no_policy = true,
            "--format" | "--max-glibc" | "--report" => {
                let (value, tail) = rest.split_first().ok_or("missing option value")?;
                rest = tail;
                if arg == "--format" {
                    format = Some(value);
                } else if arg == "--report" {
                    report_path = Some(Path::new(value));
                } else {
                    floor = Some(value);
                }
            }
            _ if !arg.starts_with('-') && binary.is_none() => binary = Some(Path::new(arg)),
            _ => return Err(format!("unexpected native inspection argument: {arg}").into()),
        }
    }
    let path = binary.ok_or("missing binary path")?;
    let identity = artifact::sha256(path)?;
    let inspected = inspect(path)?;
    if format.is_some_and(|format| format != inspected.format) {
        return Err("binary format mismatch".into());
    }
    if let Some(floor) = floor {
        check_glibc(path, inspected.format, floor)?;
    }
    let rejected_imports: Vec<String> = inspected
        .imports
        .iter()
        .filter(|s| !no_policy && forbidden(s))
        .cloned()
        .collect();
    let rejected = !rejected_imports.is_empty();
    if artifact::sha256(path)? != identity {
        return Err("binary changed during inspection".into());
    }
    let report = HostReport {
        binary: path
            .file_name()
            .ok_or("missing binary name")?
            .to_string_lossy()
            .into(),
        binary_sha256: identity,
        format: inspected.format,
        arch: inspected.arch,
        policy: if no_policy {
            "none"
        } else {
            "skippy-dynamic-host-v1"
        },
        imports: inspected.imports,
        rejected_imports,
    };
    if let Some(path) = report_path {
        crate::command::write_json_file(path, &report)?;
    } else {
        println!("{}", serde_json::to_string_pretty(&report)?);
    }
    if rejected {
        return Err("backend-neutral host imports native backend libraries".into());
    }
    Ok(())
}

fn check_glibc(path: &Path, format: &str, floor: &str) -> DynResult<()> {
    if format != "elf" {
        return Ok(());
    }
    let floor = if floor == "declared" {
        std::fs::read_to_string("scripts/linux-glibc-floor.txt")?
    } else {
        floor.into()
    };
    let declared = floor
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .collect::<Vec<_>>();
    let [value] = declared.as_slice() else {
        return Err("GLIBC floor must contain exactly one version".into());
    };
    let max = parse_version(value)?;
    let output = crate::execution::output("readelf", &["-V".into(), path.display().to_string()])?;
    if let Some((_, needs)) = output.split_once("Version needs section") {
        for part in needs.split("GLIBC_").skip(1) {
            let version: String = part
                .chars()
                .take_while(|c| c.is_ascii_digit() || *c == '.')
                .collect();
            if !version.is_empty() && parse_version(&version)? > max {
                return Err(format!("GLIBC_{version} exceeds {floor}").into());
            }
            if part.starts_with("ABI_DT_RELR") && (2, 36) > max {
                return Err("GLIBC_ABI_DT_RELR requires GLIBC 2.36".into());
            }
        }
    }
    Ok(())
}
fn parse_version(value: &str) -> DynResult<(u32, u32)> {
    let mut parts = value.split('.');
    Ok((
        parts.next().ok_or("missing major")?.parse()?,
        parts.next().ok_or("missing minor")?.parse()?,
    ))
}

pub(crate) fn verify_cli_input(args: &[String]) -> DynResult<()> {
    let [report, binary, target] = args else {
        return Err("skippy-cli-input REPORT BINARY TARGET".into());
    };
    let report: serde_json::Value = serde_json::from_slice(&std::fs::read(report)?)?;
    let binary = Path::new(binary);
    let actual = inspect(binary)?;
    let expected = match target.as_str() {
        "darwin-aarch64" => ("macho", "aarch64"),
        "linux-x86_64" => ("elf", "x86_64"),
        "linux-aarch64" => ("elf", "aarch64"),
        "windows-x86_64" => ("pe", "x86_64"),
        _ => return Err("unknown host target".into()),
    };
    if (actual.format, actual.arch) != expected
        || actual.imports.iter().any(|s| forbidden(s))
        || report["policy"] != "skippy-dynamic-host-v1"
        || report["binary_sha256"] != artifact::sha256(binary)?
        || report["imports"] != serde_json::to_value(actual.imports)?
    {
        return Err("invalid host import report or native host identity".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    #[test]
    fn neutral_host_rejects_backend_imports() {
        for import in [
            "@rpath/libllama.dylib",
            "nvcuda.dll",
            "libcublas.so.12",
            "/System/Library/Frameworks/Metal.framework/Metal",
        ] {
            assert!(super::forbidden(import));
        }
        assert!(!super::forbidden("/usr/lib/libSystem.B.dylib"));
    }
}
