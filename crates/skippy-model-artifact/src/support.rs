//! Source-owned model identities and the qualification required to publish them.

use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use skippy_model_ref::gguf_matches_quant_selector;
use std::collections::BTreeSet;

pub const PHASE_ONE_MANIFEST: &str = include_str!("support/phase-one.json");
const ARCHITECTURE_PROFILES: &str = include_str!("support/architecture-profiles.json");

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum PhaseOneFamily {
    DenseAttention,
    HybridDense,
    AttentionMoe,
    HybridMoe,
}

impl PhaseOneFamily {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::DenseAttention => "dense-attention",
            Self::HybridDense => "hybrid-dense",
            Self::AttentionMoe => "attention-moe",
            Self::HybridMoe => "hybrid-moe",
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub enum PhaseOneQuantization {
    #[serde(rename = "Q4_K_M")]
    Q4Km,
    #[serde(rename = "UD-Q4_K_M")]
    UdQ4Km,
}

impl PhaseOneQuantization {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Q4Km => "Q4_K_M",
            Self::UdQ4Km => "UD-Q4_K_M",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Sidecar {
    pub file: String,
    pub size_bytes: u64,
    pub sha256: String,
}

#[derive(Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PhaseOneFixture {
    pub family: PhaseOneFamily,
    pub repo: String,
    pub revision: String,
    pub file: String,
    pub quantization: PhaseOneQuantization,
    pub size_bytes: u64,
    pub sha256: String,
    pub chat_template_sha256: String,
    pub license: String,
    pub architecture: String,
    pub layers: u32,
    pub required_sidecars: Vec<Sidecar>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PhaseOneManifest {
    schema_version: u32,
    pub models: Vec<PhaseOneFixture>,
}

impl PhaseOneManifest {
    pub fn bundled() -> Result<Self> {
        Self::parse(PHASE_ONE_MANIFEST)
    }

    pub fn parse(json: &str) -> Result<Self> {
        let manifest: Self =
            serde_json::from_str(json).context("parse Phase 1 model identities")?;
        let mut families = BTreeSet::new();
        let mut coordinates = BTreeSet::new();
        let mut digests = BTreeSet::new();
        for model in &manifest.models {
            model
                .validate()
                .with_context(|| format!("validate {} fixture", model.family.as_str()))?;
            ensure!(families.insert(model.family), "duplicate Phase 1 family");
            ensure!(
                coordinates.insert((&model.repo, &model.revision, &model.file))
                    && digests.insert(&model.sha256),
                "duplicate Phase 1 artifact identity"
            );
        }
        ensure!(
            manifest.schema_version == 2 && families.len() == 4,
            "Phase 1 requires one fixture for each of the four families"
        );
        Ok(manifest)
    }
}

impl PhaseOneFixture {
    fn validate(&self) -> Result<()> {
        let (owner, repo) = self
            .repo
            .split_once('/')
            .context("repository requires an owner")?;
        ensure!(
            identifier(owner) && identifier(repo) && valid_digest(&self.revision, 40),
            "expected an immutable repository revision"
        );
        ensure!(
            safe_filename(&self.file)
                && self
                    .file
                    .ends_with(&format!("-{}.gguf", self.quantization.as_str()))
                && (!matches!(self.quantization, PhaseOneQuantization::Q4Km)
                    || !self.file.ends_with("-UD-Q4_K_M.gguf"))
                && gguf_matches_quant_selector(&self.file, self.quantization.as_str()),
            "GGUF filename must match the declared quantization"
        );
        ensure!(
            valid_digest(&self.sha256, 64) && valid_digest(&self.chat_template_sha256, 64),
            "artifact and chat template require SHA-256 digests"
        );
        ensure!(
            self.size_bytes > 0
                && self.layers > 0
                && identifier(&self.architecture)
                && identifier(&self.license),
            "layout and model license must be declared"
        );
        let mut files = BTreeSet::from([self.file.as_str()]);
        for sidecar in &self.required_sidecars {
            ensure!(
                safe_filename(&sidecar.file)
                    && files.insert(&sidecar.file)
                    && sidecar.size_bytes > 0
                    && valid_digest(&sidecar.sha256, 64),
                "sidecar filename, length, or SHA-256 is invalid or duplicated"
            );
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ArchitectureTrack {
    TierB,
    Experimental,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ArchitectureProfile {
    pub architecture: String,
    pub canary_fixture: String,
    pub track: ArchitectureTrack,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

pub fn bundled_architecture_profiles() -> Result<Vec<ArchitectureProfile>> {
    let profiles: Vec<ArchitectureProfile> = serde_json::from_str(ARCHITECTURE_PROFILES)?;
    let mut architectures = BTreeSet::new();
    for profile in &profiles {
        ensure!(
            identifier(&profile.architecture)
                && identifier(&profile.canary_fixture)
                && architectures.insert(&profile.architecture),
            "invalid or duplicate model support architecture"
        );
        if profile.track == ArchitectureTrack::Experimental {
            ensure!(
                profile
                    .note
                    .as_ref()
                    .is_some_and(|note| !note.trim().is_empty()),
                "experimental architecture needs an explanation"
            );
        }
    }
    Ok(profiles)
}

pub fn valid_sha256(value: &str) -> bool {
    valid_digest(value, 64)
}

fn valid_digest(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
        && value.bytes().any(|byte| byte != b'0')
}

fn identifier(value: &str) -> bool {
    value
        .as_bytes()
        .first()
        .is_some_and(u8::is_ascii_alphanumeric)
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

fn safe_filename(value: &str) -> bool {
    identifier(value) && !value.ends_with('.')
}

#[cfg(test)]
mod tests;
