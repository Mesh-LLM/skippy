use super::{DynResult, identity::Checkout};
use serde::{Deserialize, Serialize};
use skippy_model_artifact::kv_fixtures::KvFixtureManifest;
use std::collections::BTreeSet;

pub(super) const VERSION: u32 = 2;
pub(super) const PAIRS: usize = 20;
pub(super) const REQUESTS: u32 = 200;
pub(super) const EMPTY_SIDECARS_SHA256: &str =
    "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945";

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Backend {
    Metal,
    Cuda,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum CacheState {
    ColdPrefill,
    WarmL1,
    L2Restore,
    PostRestartL3,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Workload {
    ExactReplay,
    DivergentTail,
    GrowingConversation,
}

#[derive(Clone, Copy, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct WorkloadDesign {
    pub(super) cache: CacheState,
    pub(super) workload: Workload,
}

#[derive(Clone, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Cell {
    pub(super) family: String,
    pub(super) backend: Backend,
    pub(super) cache: CacheState,
    pub(super) workload: Workload,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Plan {
    pub(super) schema_version: u32,
    pub(super) source_sha: String,
    pub(super) llama_pin: String,
    pub(super) patch_queue_sha256: String,
    pub(super) model_manifest_sha256: String,
    pub(super) abi_version: String,
    pub(super) cache_format_version: u32,
    pub(super) harness_sha256: String,
    pub(super) agentx_corpus_sha256: String,
    pub(super) agentx_scorer_sha256: String,
    pub(super) backends: Hosts,
    pub(super) cells: Vec<PlannedCell>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Hosts {
    pub(super) metal: Host,
    pub(super) cuda: Host,
}

impl Hosts {
    pub(super) fn get(&self, backend: Backend) -> &Host {
        match backend {
            Backend::Metal => &self.metal,
            Backend::Cuda => &self.cuda,
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Host {
    pub(super) os: String,
    pub(super) arch: String,
    pub(super) host_profile_sha256: String,
    pub(super) candidate_binary_sha256: String,
    pub(super) baseline_binary_sha256: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct PlannedCell {
    pub(super) cell: Cell,
    pub(super) model_sha256: String,
    pub(super) template_sha256: String,
    pub(super) sidecars_sha256: String,
    pub(super) workload_sha256: String,
    pub(super) candidate_config_sha256: String,
    pub(super) baseline_config_sha256: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Evidence {
    pub(super) schema_version: u32,
    pub(super) plan_sha256: String,
    pub(super) cells: Vec<MeasuredCell>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct MeasuredCell {
    pub(super) cell: Cell,
    pub(super) model_sha256: String,
    pub(super) template_sha256: String,
    pub(super) sidecars_sha256: String,
    pub(super) workload_sha256: String,
    pub(super) pairs: Vec<[Trial; 2]>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Engine {
    Candidate,
    Baseline,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Trial {
    pub(super) engine: Engine,
    pub(super) binary_sha256: String,
    pub(super) config_sha256: String,
    pub(super) host_profile_sha256: String,
    pub(super) raw_artifact_sha256: String,
    pub(super) requests: u32,
    pub(super) failed_requests: u32,
    pub(super) incorrect_outputs: u32,
    pub(super) requests_with_expected_cache_path: u32,
    pub(super) metrics: Metrics,
}

#[derive(Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Metrics {
    pub(super) agentx_score: f64,
    pub(super) inference_ttft_ms: Percentiles,
    pub(super) end_to_end_ttft_ms: Percentiles,
    pub(super) prefill_tokens_per_second: f64,
    pub(super) decode_tokens_per_second: f64,
    pub(super) peak_host_bytes: u64,
    pub(super) peak_device_bytes: u64,
}

#[derive(Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Percentiles {
    pub(super) p50: f64,
    pub(super) p90: f64,
    pub(super) p99: f64,
}

pub(super) fn is_digest(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
        && value.bytes().any(|byte| byte != b'0')
}

impl Plan {
    pub(super) fn validate(
        &self,
        manifest: &KvFixtureManifest,
        checkout: &Checkout,
    ) -> DynResult<()> {
        if self.schema_version != VERSION || !is_digest(&self.agentx_corpus_sha256, 64) {
            return Err("invalid parity plan schema or corpus digest".into());
        }
        for (field, planned, expected) in [
            ("source_sha", &self.source_sha, &checkout.source_sha),
            ("llama_pin", &self.llama_pin, &checkout.llama_pin),
            (
                "patch_queue_sha256",
                &self.patch_queue_sha256,
                &checkout.patch_queue_sha256,
            ),
            (
                "model_manifest_sha256",
                &self.model_manifest_sha256,
                &checkout.model_manifest_sha256,
            ),
            ("abi_version", &self.abi_version, &checkout.abi_version),
            (
                "harness_sha256",
                &self.harness_sha256,
                &checkout.harness_sha256,
            ),
            (
                "agentx_scorer_sha256",
                &self.agentx_scorer_sha256,
                &checkout.agentx_scorer_sha256,
            ),
        ] {
            if planned != expected {
                return Err(format!("parity plan {field} does not match the checkout").into());
            }
        }
        if self.cache_format_version != checkout.cache_format_version {
            return Err("parity plan cache_format_version does not match the checkout".into());
        }
        self.validate_backends()?;
        self.validate_cells(manifest)
    }

    fn validate_backends(&self) -> DynResult<()> {
        for (backend, os, arch) in [
            (Backend::Metal, "macos", "arm64"),
            (Backend::Cuda, "linux", "x86_64"),
        ] {
            let host = self.backends.get(backend);
            if (host.os.as_str(), host.arch.as_str()) != (os, arch)
                || ![
                    &host.host_profile_sha256,
                    &host.candidate_binary_sha256,
                    &host.baseline_binary_sha256,
                ]
                .into_iter()
                .all(|digest| is_digest(digest, 64))
                || host.candidate_binary_sha256 == host.baseline_binary_sha256
            {
                return Err(format!("invalid {backend:?} host identity").into());
            }
        }
        Ok(())
    }

    fn validate_cells(&self, manifest: &KvFixtureManifest) -> DynResult<()> {
        let mut keys = BTreeSet::new();
        for row in &self.cells {
            let fixture = manifest
                .models
                .iter()
                .find(|model| model.family.as_str() == row.cell.family);
            if !keys.insert(&row.cell)
                || !fixture.is_some_and(|model| {
                    model.sha256 == row.model_sha256
                        && model.chat_template_sha256 == row.template_sha256
                })
                || row.sidecars_sha256 != EMPTY_SIDECARS_SHA256
                || ![
                    &row.model_sha256,
                    &row.template_sha256,
                    &row.sidecars_sha256,
                    &row.workload_sha256,
                    &row.candidate_config_sha256,
                    &row.baseline_config_sha256,
                ]
                .into_iter()
                .all(|digest| is_digest(digest, 64))
            {
                return Err(format!("duplicate or invalid planned cell {:?}", row.cell).into());
            }
        }
        let expected = manifest.models.len() * 2 * 4 * 3;
        if keys.len() != expected {
            return Err(format!(
                "incomplete parity plan, expected {expected} cells and found {}",
                keys.len()
            )
            .into());
        }
        Ok(())
    }
}
