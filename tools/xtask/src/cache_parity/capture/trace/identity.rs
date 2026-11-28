use super::DynResult;
use crate::{artifact, cache_parity::capture::server::Server, command::write_json_file};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Path, PathBuf},
};

#[derive(Deserialize)]
struct HostProfile {
    os: String,
    arch: String,
    metal_memory_probe_sha256: Option<String>,
}

pub(in crate::cache_parity::capture) struct Inputs {
    host_profile: Vec<u8>,
    models: [PathBuf; 2],
    binaries: [PathBuf; 2],
    model_sha256: String,
    binary_sha256: [String; 2],
    probes: [Option<PathBuf>; 2],
    metal_memory_probe_sha256: Option<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(in crate::cache_parity) struct Identity {
    pub(in crate::cache_parity) schema_version: u32,
    pub(in crate::cache_parity) pairs_sha256: String,
    pub(in crate::cache_parity) host_profile_sha256: String,
    pub(in crate::cache_parity) model_sha256: String,
}

impl Inputs {
    pub(in crate::cache_parity::capture) fn observe(
        host_profile: &Path,
        candidate: &Server,
        baseline: &Server,
    ) -> DynResult<Self> {
        let host_profile = fs::read(host_profile)?;
        let host: HostProfile = serde_json::from_slice(&host_profile)?;
        let arch = match std::env::consts::ARCH {
            "aarch64" if std::env::consts::OS == "macos" => "arm64",
            other => other,
        };
        if host.os != std::env::consts::OS || host.arch != arch {
            return Err(
                "capture host profile does not match the running OS and architecture".into(),
            );
        }
        if host.metal_memory_probe_sha256 != candidate.probe_sha256()?
            || host.metal_memory_probe_sha256 != baseline.probe_sha256()?
        {
            return Err("capture Metal memory probe does not match the host profile".into());
        }
        let models = [
            explicit_model(candidate, "--model-path")?,
            explicit_model(baseline, "--model")?,
        ];
        let binaries = [candidate.binary.clone(), baseline.binary.clone()];
        let binary_sha256 = [
            artifact::sha256(&binaries[0])?,
            artifact::sha256(&binaries[1])?,
        ];
        let model_sha256 = matched_model_sha(&models)?;
        Ok(Self {
            host_profile,
            models,
            binaries,
            model_sha256,
            binary_sha256,
            probes: [
                candidate.metal_memory_probe.clone(),
                baseline.metal_memory_probe.clone(),
            ],
            metal_memory_probe_sha256: host.metal_memory_probe_sha256,
        })
    }

    pub(in crate::cache_parity::capture) fn retain(&self, output: &Path) -> DynResult<()> {
        Ok(fs::write(
            output.join("host-profile.json"),
            &self.host_profile,
        )?)
    }

    pub(in crate::cache_parity::capture) fn complete(&self, output: &Path) -> DynResult<()> {
        if matched_model_sha(&self.models)? != self.model_sha256
            || artifact::sha256(&self.binaries[0])? != self.binary_sha256[0]
            || artifact::sha256(&self.binaries[1])? != self.binary_sha256[1]
        {
            return Err("model or executable changed during capture".into());
        }
        for probe in &self.probes {
            if probe.as_deref().map(artifact::sha256).transpose()? != self.metal_memory_probe_sha256
            {
                return Err("Metal memory probe changed during capture".into());
            }
        }
        write_json_file(
            &output.join("capture-identity.json"),
            &Identity {
                schema_version: 1,
                pairs_sha256: artifact::sha256(&output.join("pairs.json"))?,
                host_profile_sha256: artifact::digest(self.host_profile.as_slice())?,
                model_sha256: self.model_sha256.clone(),
            },
        )
    }
}

fn explicit_model(server: &Server, option: &str) -> DynResult<PathBuf> {
    let mut args = server.args.iter();
    let mut path = None;
    while let Some(arg) = args.next() {
        let (name, inline) = arg
            .split_once('=')
            .map_or((arg.as_str(), None), |(key, value)| (key, Some(value)));
        if matches!(
            name,
            "--chat-template"
                | "--chat-template-file"
                | "--settings"
                | "--model-url"
                | "--hf-repo"
                | "-hf"
                | "-m"
        ) {
            return Err(format!(
                "pinned capture requires direct model and embedded template settings, found {name}"
            )
            .into());
        }
        if name == option {
            let value = inline
                .or_else(|| args.next().map(String::as_str))
                .ok_or("missing explicit model path")?;
            if path.replace(PathBuf::from(value)).is_some() {
                return Err(format!("duplicate {option}").into());
            }
        }
    }
    let path = path.ok_or_else(|| format!("pinned capture requires {option}"))?;
    if !path.is_absolute() || !path.is_file() {
        return Err("pinned capture requires an absolute local model file for each engine".into());
    }
    Ok(path)
}

fn matched_model_sha(paths: &[PathBuf; 2]) -> DynResult<String> {
    let first = artifact::sha256(&paths[0])?;
    if paths[0].canonicalize()? != paths[1].canonicalize()? && artifact::sha256(&paths[1])? != first
    {
        return Err("measured engines do not use identical model bytes".into());
    }
    Ok(first)
}
