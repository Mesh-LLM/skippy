use super::file_sha256;
use crate::kv_integration::StagePrefixCachePayload;
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use skippy_model_artifact::gguf::scan_gguf_compact_meta;
use skippy_model_artifact::kv_fixtures::{KV_FIXTURE_MANIFEST, Sidecar};
use std::{collections::BTreeSet, fs, path::Path};

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "kebab-case")]
pub(super) enum Family {
    DenseAttention,
    HybridDense,
    AttentionMoe,
    HybridMoe,
}

impl Family {
    pub(super) fn parse(value: &str) -> Result<Self> {
        serde_json::from_value(serde_json::Value::String(value.into()))
            .with_context(|| format!("unknown qualification model family {value:?}"))
    }

    pub(super) fn payload(self) -> StagePrefixCachePayload {
        match self {
            Self::DenseAttention | Self::AttentionMoe => StagePrefixCachePayload::ResidentKv,
            Self::HybridDense | Self::HybridMoe => StagePrefixCachePayload::KvRecurrent,
        }
    }

    pub(super) fn has_experts(self) -> bool {
        matches!(self, Self::AttentionMoe | Self::HybridMoe)
    }

    pub(super) fn prefill_tokens(self, prompt_tokens: u32) -> u32 {
        match self {
            // The deferred recurrent path records the entire final suffix.
            Self::HybridDense | Self::HybridMoe => prompt_tokens,
            Self::DenseAttention | Self::AttentionMoe => prompt_tokens.saturating_sub(1),
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema_version: u32,
    models: Vec<Fixture>,
}

#[derive(Clone, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Fixture {
    pub(super) family: Family,
    repo: String,
    revision: String,
    file: String,
    quantization: String,
    size_bytes: u64,
    sha256: String,
    pub(super) chat_template_sha256: String,
    license: String,
    architecture: String,
    pub(super) layers: u32,
    required_sidecars: Vec<Sidecar>,
}

impl Fixture {
    pub(super) fn load(family: Family) -> Result<Self> {
        let manifest: Manifest =
            serde_json::from_str(KV_FIXTURE_MANIFEST).context("read cache fixture manifest")?;
        manifest.validate()?;
        manifest
            .models
            .into_iter()
            .find(|row| row.family == family)
            .context("qualification fixture missing from manifest")
    }

    pub(super) fn model_id(&self) -> String {
        format!("{}:{}", self.repo, self.quantization)
    }

    pub(super) fn digest(&self) -> &str {
        &self.sha256
    }

    pub(super) fn verify_model(&self, path: &Path) -> Result<String> {
        let observed_bytes = fs::metadata(path)?.len();
        ensure!(
            observed_bytes == self.size_bytes,
            "fixture size mismatch: expected {}, observed {observed_bytes}",
            self.size_bytes
        );
        let digest = file_sha256(path)?;
        ensure!(
            digest == self.sha256,
            "fixture SHA-256 mismatch: expected {}, observed {digest}",
            self.sha256
        );
        let meta = scan_gguf_compact_meta(path).context("read qualified GGUF metadata")?;
        ensure!(
            meta.architecture == self.architecture
                && meta.layer_count == self.layers
                && (meta.expert_count > 0) == self.family.has_experts(),
            "fixture layout disagrees with the manifest for {}",
            self.model_id()
        );
        Ok(digest)
    }
}

impl Manifest {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.schema_version == 2 && self.models.len() == 4,
            "expected four version-2 cache fixtures"
        );
        let mut families = BTreeSet::new();
        let mut identities = BTreeSet::new();
        for model in &self.models {
            ensure!(
                model.required_sidecars.is_empty(),
                "this cache runner does not support attaching model sidecars"
            );
            ensure!(
                families.insert(model.family) && identities.insert(model.model_id()),
                "duplicate cache fixture identity"
            );
            ensure!(
                hex(&model.revision, 40),
                "fixture revision must be a pinned commit"
            );
            ensure!(
                hex(&model.sha256, 64) && hex(&model.chat_template_sha256, 64),
                "fixture and chat template require SHA-256"
            );
            ensure!(
                model.size_bytes > 0 && model.layers > 0 && !model.architecture.is_empty(),
                "fixture layout is incomplete"
            );
            let (owner, name) = model
                .repo
                .split_once('/')
                .context("fixture requires an owner and repository")?;
            ensure!(
                !owner.is_empty() && !name.is_empty() && !name.contains('/'),
                "invalid fixture repository"
            );
            ensure!(
                matches!(model.quantization.as_str(), "Q4_K_M" | "UD-Q4_K_M")
                    && model
                        .file
                        .ends_with(&format!("-{}.gguf", model.quantization))
                    && !model.file.contains(['/', '\\']),
                "invalid fixture GGUF file or quantization"
            );
            ensure!(
                model.license == "Apache-2.0",
                "fixture requires a supported SPDX license"
            );
        }
        Ok(())
    }
}

fn hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[test]
fn cache_fixture_manifest_has_one_pinned_model_per_family() {
    Fixture::load(Family::DenseAttention).unwrap();
    let mut manifest: Manifest = serde_json::from_str(KV_FIXTURE_MANIFEST).unwrap();
    manifest.models[0].revision = "main".into();
    assert!(manifest.validate().is_err());
    let mut manifest: Manifest = serde_json::from_str(KV_FIXTURE_MANIFEST).unwrap();
    manifest.models[0].family = manifest.models[1].family;
    assert!(manifest.validate().is_err());
}
