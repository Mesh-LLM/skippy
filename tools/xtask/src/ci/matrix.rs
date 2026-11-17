use crate::command::DynResult;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Host {
    pub id: String,
    pub target: String,
    pub os: String,
    pub arch: String,
    pub runner: Vec<String>,
    #[serde(default = "enabled")]
    pub enabled: bool,
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Runtime {
    pub id: String,
    pub host: String,
    pub backend: String,
    pub toolkit: String,
    pub gpu_architectures: String,
    pub runner: Vec<String>,
    #[serde(default = "enabled")]
    pub enabled: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Matrix {
    pub schema_version: u32,
    pub hosts: Vec<Host>,
    pub runtimes: Vec<Runtime>,
}

impl Matrix {
    pub fn load() -> DynResult<Self> {
        let matrix: Self = serde_json::from_slice(&std::fs::read("ci/targets.json")?)?;
        matrix.validate()?;
        Ok(matrix)
    }
    pub fn host(&self, id: &str) -> DynResult<&Host> {
        self.hosts
            .iter()
            .find(|row| row.id == id)
            .ok_or_else(|| format!("unknown host: {id}").into())
    }
    pub fn runtime(&self, id: &str) -> DynResult<&Runtime> {
        self.runtimes
            .iter()
            .find(|row| row.id == id)
            .ok_or_else(|| format!("unknown runtime: {id}").into())
    }
    pub fn active_runtimes(&self) -> impl Iterator<Item = &Runtime> {
        self.runtimes.iter().filter(|row| row.enabled)
    }
    fn validate(&self) -> DynResult<()> {
        if self.schema_version != 1 || self.hosts.len() != 4 || self.runtimes.len() != 11 {
            return Err("expected schema 1, four hosts, and eleven runtimes".into());
        }
        let mut ids = BTreeSet::new();
        for host in &self.hosts {
            if !safe_id(&host.id)
                || !ids.insert(&host.id)
                || host.runner != [hosted_runner(&host.id)?]
                || !valid_host_identity(host)
            {
                return Err("invalid host identity or runner boundary".into());
            }
        }
        let mut variants = BTreeSet::new();
        for runtime in &self.runtimes {
            let host = self.host(&runtime.host)?;
            if runtime.enabled && !host.enabled {
                return Err("enabled runtime cannot use a disabled host".into());
            }
            if !safe_id(&runtime.id) || !ids.insert(&runtime.id) {
                return Err("invalid runtime identity or runner boundary".into());
            }
            let variant = match runtime.backend.as_str() {
                "cpu" if runtime.toolkit.is_empty() && runtime.gpu_architectures.is_empty() => {
                    "cpu"
                }
                "metal" if host.os == "macos" && runtime.toolkit.is_empty() => "metal",
                "cuda"
                    if host.os != "macos"
                        && runtime.toolkit.starts_with("12.")
                        && !runtime.gpu_architectures.is_empty() =>
                {
                    "cuda12"
                }
                "cuda"
                    if host.os != "macos"
                        && runtime.toolkit.starts_with("13.")
                        && !runtime.gpu_architectures.is_empty() =>
                {
                    "cuda13"
                }
                _ => return Err("unsupported backend/toolkit/OS combination".into()),
            };
            if runtime.runner != expected_runner(host, variant) {
                return Err(
                    "runtime runner labels do not match its exact OS/architecture/backend".into(),
                );
            }
            if !variants.insert((runtime.host.as_str(), variant)) {
                return Err("duplicate runtime variant".into());
            }
        }
        for host in &self.hosts {
            let required: &[&str] = if host.os == "macos" {
                &["cpu", "metal"]
            } else {
                &["cpu", "cuda12", "cuda13"]
            };
            for variant in required {
                if !variants.contains(&(host.id.as_str(), *variant)) {
                    return Err(format!("missing {}/{}", host.id, variant).into());
                }
            }
        }
        Ok(())
    }
}
fn enabled() -> bool {
    true
}
fn valid_host_identity(host: &Host) -> bool {
    matches!(
        (
            host.id.as_str(),
            host.target.as_str(),
            host.os.as_str(),
            host.arch.as_str()
        ),
        ("darwin-aarch64", "aarch64-apple-darwin", "macos", "aarch64")
            | (
                "linux-x86_64",
                "x86_64-unknown-linux-gnu",
                "linux",
                "x86_64"
            )
            | (
                "linux-aarch64",
                "aarch64-unknown-linux-gnu",
                "linux",
                "aarch64"
            )
            | (
                "windows-x86_64",
                "x86_64-pc-windows-msvc",
                "windows",
                "x86_64"
            )
    )
}
fn expected_runner(host: &Host, variant: &str) -> Vec<String> {
    if variant == "cpu" {
        return host.runner.clone();
    }
    let os = match host.os.as_str() {
        "macos" => "macOS",
        "linux" => "Linux",
        _ => "Windows",
    };
    let arch = if host.arch == "aarch64" {
        "ARM64"
    } else {
        "X64"
    };
    ["self-hosted", "skippy-ephemeral", os, arch, variant]
        .into_iter()
        .map(str::to_owned)
        .collect()
}
fn safe_id(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-' || b == b'_')
}

fn hosted_runner(id: &str) -> DynResult<&'static str> {
    match id {
        "darwin-aarch64" => Ok("macos-15"),
        "linux-x86_64" => Ok("ubuntu-24.04"),
        "linux-aarch64" => Ok("ubuntu-24.04-arm"),
        "windows-x86_64" => Ok("windows-2025"),
        _ => Err("unknown host identity".into()),
    }
}

pub fn require_host(host: &Host) -> DynResult<()> {
    if host.os != std::env::consts::OS || host.arch != std::env::consts::ARCH {
        return Err(format!(
            "{} requires {}/{}, running {}/{}",
            host.id,
            host.os,
            host.arch,
            std::env::consts::OS,
            std::env::consts::ARCH
        )
        .into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_missing_or_duplicate_runtime() {
        let mut matrix: Matrix =
            serde_json::from_str(include_str!("../../../../ci/targets.json")).unwrap();
        matrix.validate().unwrap();
        matrix.runtimes[1] = matrix.runtimes[0].clone();
        assert!(matrix.validate().is_err());
        matrix.runtimes.pop();
        assert!(matrix.validate().is_err());
    }
    #[test]
    fn rejects_cuda_on_macos() {
        let mut matrix: Matrix =
            serde_json::from_str(include_str!("../../../../ci/targets.json")).unwrap();
        matrix.runtimes[1].backend = "cuda".into();
        matrix.runtimes[1].toolkit = "13.0.2".into();
        assert!(matrix.validate().is_err());
    }
    #[test]
    fn hardware_labels_must_match_exact_variant() {
        let matrix_text = include_str!("../../../../ci/targets.json");
        for wrong in [
            vec!["self-hosted", "skippy-ephemeral"],
            vec!["self-hosted", "skippy-ephemeral", "Linux", "X64", "metal"],
        ] {
            let mut matrix: Matrix = serde_json::from_str(matrix_text).unwrap();
            matrix.runtimes[1].runner = wrong.into_iter().map(str::to_owned).collect();
            assert!(matrix.validate().is_err());
        }
    }
    #[test]
    fn disabled_targets_stay_declared_but_are_not_active() {
        let matrix: Matrix =
            serde_json::from_str(include_str!("../../../../ci/targets.json")).unwrap();
        assert_eq!(matrix.runtimes.len(), 11);
        assert_eq!(matrix.hosts.len(), 4);
        assert_eq!(matrix.hosts.iter().filter(|row| row.enabled).count(), 3);
        assert_eq!(matrix.active_runtimes().count(), 8);
        assert!(
            matrix
                .hosts
                .iter()
                .filter(|row| row.enabled)
                .all(|row| row.os != "windows")
        );
        assert!(
            matrix
                .active_runtimes()
                .all(|row| !row.id.starts_with("windows-x86_64"))
        );
    }
}
