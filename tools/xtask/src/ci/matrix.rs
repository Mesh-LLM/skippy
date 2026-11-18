use crate::command::DynResult;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Host {
    pub id: String,
    pub display_name: String,
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
    pub display_name: String,
    pub host: String,
    pub backend: String,
    pub toolkit: String,
    pub gpu_architectures: String,
    pub runner: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub runner_group: Option<String>,
    #[serde(default = "enabled")]
    pub enabled: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Matrix {
    pub schema_version: u32,
    #[serde(default)]
    enabled_hosts: Option<Vec<String>>,
    pub hosts: Vec<Host>,
    pub runtimes: Vec<Runtime>,
}

impl Matrix {
    pub fn load() -> DynResult<Self> {
        Self::from_json(&std::fs::read_to_string("ci/targets.json")?)
    }
    pub(crate) fn from_json(text: &str) -> DynResult<Self> {
        let mut matrix: Self = serde_json::from_str(text)?;
        matrix.validate()?;
        if let Some(enabled) = &matrix.enabled_hosts {
            matrix.hosts.retain(|host| enabled.contains(&host.id));
            matrix
                .runtimes
                .retain(|runtime| enabled.contains(&runtime.host));
        }
        matrix.hosts.retain(|host| host.enabled);
        matrix.runtimes.retain(|runtime| runtime.enabled);
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
                || host.display_name.trim().is_empty()
                || !ids.insert(&host.id)
                || host.runner != expected_host_runner(&host.id)?
                || !valid_host_identity(host)
            {
                return Err("invalid host identity or runner boundary".into());
            }
        }
        if let Some(enabled) = &self.enabled_hosts {
            let selected = enabled.iter().collect::<BTreeSet<_>>();
            if selected.is_empty()
                || selected.len() != enabled.len()
                || selected
                    .iter()
                    .any(|id| !self.hosts.iter().any(|host| &host.id == *id))
            {
                return Err("enabled_hosts must select unique, defined hosts".into());
            }
        }
        let mut variants = BTreeSet::new();
        for runtime in &self.runtimes {
            let host = self.host(&runtime.host)?;
            if runtime.enabled && !host.enabled {
                return Err("enabled runtime cannot use a disabled host".into());
            }
            if !safe_id(&runtime.id)
                || runtime.display_name.trim().is_empty()
                || !ids.insert(&runtime.id)
            {
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
            if let Some(group) = &runtime.runner_group
                && (runtime.runner.first().map(String::as_str) != Some("self-hosted")
                    || !group.strip_prefix("jy-rnr_").is_some_and(|id| {
                        !id.is_empty()
                            && id
                                .chars()
                                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
                    }))
            {
                return Err("runner groups require self-hosted JY runner labels".into());
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
    let mut runner = ["self-hosted", "skippy-ephemeral", os, arch]
        .into_iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if variant.starts_with("cuda") {
        runner.push("runtime-image".into());
    }
    runner.push(variant.into());
    runner
}
fn safe_id(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-' || b == b'_')
}

fn expected_host_runner(id: &str) -> DynResult<Vec<String>> {
    let runner = match id {
        "darwin-aarch64" => "macos-15",
        "linux-x86_64" => "ubuntu-24.04",
        "linux-aarch64" => "ubuntu-24.04-arm",
        "windows-x86_64" => "windows-2025",
        _ => return Err("unknown host identity".into()),
    };
    Ok(vec![runner.to_owned()])
}

#[derive(Serialize)]
pub(crate) struct RunnerSelection {
    group: &'static str,
    labels: &'static [&'static str],
}

pub(crate) fn release_runner(host: &Host) -> DynResult<RunnerSelection> {
    let selection = match host.id.as_str() {
        "darwin-aarch64" => RunnerSelection {
            group: "jy-rnr_h7x5s2yx9gtkz3c",
            labels: &["self-hosted", "skippy-ephemeral", "macOS", "ARM64"],
        },
        "linux-x86_64" => RunnerSelection {
            group: "jy-rnr_vfnz8k5p4yrh334",
            labels: &["self-hosted", "jy-linux-docker", "Linux", "X64"],
        },
        "linux-aarch64" => RunnerSelection {
            group: "jy-rnr_8gvzbe9b9d79y0k",
            labels: &[
                "self-hosted",
                "skippy-ephemeral",
                "Linux",
                "ARM64",
                "runtime-image",
            ],
        },
        _ => return Err(format!("no JY release runner is configured for {}", host.id).into()),
    };
    Ok(selection)
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
    fn windows_definitions_are_retained_but_not_activated() {
        let text = include_str!("../../../../ci/targets.json");
        let raw: Matrix = serde_json::from_str(text).unwrap();
        assert_eq!(raw.hosts.len(), 4);
        assert_eq!(raw.runtimes.len(), 11);
        let active = Matrix::from_json(text).unwrap();
        assert_eq!(active.hosts.len(), 3);
        assert_eq!(active.runtimes.len(), 8);
        assert!(active.hosts.iter().all(|host| host.os != "windows"));
        assert!(
            active
                .runtimes
                .iter()
                .all(|runtime| !runtime.host.starts_with("windows"))
        );
        let mut restored = raw;
        restored.enabled_hosts = None;
        restored.validate().unwrap();
    }
    #[test]
    fn enabled_selection_rejects_empty_unknown_and_duplicate_hosts() {
        for enabled in [
            vec![],
            vec!["unknown"],
            vec!["linux-x86_64", "linux-x86_64"],
        ] {
            let mut matrix: Matrix =
                serde_json::from_str(include_str!("../../../../ci/targets.json")).unwrap();
            matrix.enabled_hosts = Some(enabled.into_iter().map(str::to_owned).collect());
            assert!(matrix.validate().is_err());
        }
    }
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
