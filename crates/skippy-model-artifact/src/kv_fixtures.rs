//! Pinned model fixtures for KV cache correctness and parity tests.

use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use skippy_model_ref::gguf_matches_quant_selector;
use std::collections::BTreeSet;

pub const KV_FIXTURE_MANIFEST: &str = include_str!("kv_fixtures/models.json");

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum KvArchitectureCase {
    DenseAttention,
    HybridDense,
    AttentionMoe,
    HybridMoe,
}

impl KvArchitectureCase {
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
pub enum KvFixtureQuantization {
    #[serde(rename = "Q4_K_M")]
    Q4Km,
    #[serde(rename = "UD-Q4_K_M")]
    UdQ4Km,
}

impl KvFixtureQuantization {
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
pub struct KvFixture {
    pub family: KvArchitectureCase,
    pub repo: String,
    pub revision: String,
    pub file: String,
    pub quantization: KvFixtureQuantization,
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
pub struct KvFixtureManifest {
    schema_version: u32,
    pub models: Vec<KvFixture>,
}

impl KvFixtureManifest {
    pub fn bundled() -> Result<Self> {
        Self::parse(KV_FIXTURE_MANIFEST)
    }

    pub fn parse(json: &str) -> Result<Self> {
        let manifest: Self = serde_json::from_str(json).context("parse KV fixture identities")?;
        let mut families = BTreeSet::new();
        let mut coordinates = BTreeSet::new();
        let mut digests = BTreeSet::new();
        for model in &manifest.models {
            model
                .validate()
                .with_context(|| format!("validate {} fixture", model.family.as_str()))?;
            ensure!(
                families.insert(model.family),
                "duplicate KV architecture case"
            );
            ensure!(
                coordinates.insert((&model.repo, &model.revision, &model.file))
                    && digests.insert(&model.sha256),
                "duplicate KV fixture artifact identity"
            );
        }
        ensure!(
            manifest.schema_version == 2 && families.len() == 4,
            "KV qualification requires one fixture per architecture case"
        );
        Ok(manifest)
    }
}

impl KvFixture {
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
                && (!matches!(self.quantization, KvFixtureQuantization::Q4Km)
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
