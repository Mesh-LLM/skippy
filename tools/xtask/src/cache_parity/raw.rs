use super::{DynResult, contract::*};
use crate::artifact;
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, File},
    io::Read,
    path::Path,
};

pub(super) mod export;
mod measurements;
#[cfg(test)]
mod tests;

const RAW_VERSION: u32 = 1;
const MAX_TRIAL_BYTES: u64 = 8 * 1024 * 1024;

pub(super) fn scorer_sha256() -> DynResult<String> {
    artifact::digest(include_bytes!("raw/measurements.rs").as_slice())
}

pub(super) fn summarize(path: &Path) -> DynResult<Trial> {
    let (raw, digest) = read(path)?;
    raw.summary(digest)
}

fn read(path: &Path) -> DynResult<(RawTrial, String)> {
    if !fs::metadata(path)?.is_file() {
        return Err(format!("raw trial {} must be a regular file", path.display()).into());
    }
    let mut bytes = Vec::new();
    File::open(path)?
        .take(MAX_TRIAL_BYTES + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_TRIAL_BYTES {
        return Err(format!("raw trial {} exceeds the size limit", path.display()).into());
    }
    let digest = artifact::digest(bytes.as_slice())?;
    Ok((serde_json::from_slice(&bytes)?, digest))
}

pub(super) struct ArtifactSource<'a> {
    pub(super) directory: &'a Path,
    pub(super) plan_sha: &'a str,
}

impl ArtifactSource<'_> {
    pub(super) fn check_trial(
        &self,
        row: &PlannedCell,
        pair: usize,
        trial: &Trial,
    ) -> DynResult<String> {
        let digest = &trial.raw_artifact_sha256;
        if !is_digest(digest, 64) {
            return Err("invalid raw trial SHA-256".into());
        }
        let path = self.directory.join(format!("{digest}.json"));
        let (raw, actual_digest) =
            read(&path).map_err(|error| format!("read raw trial {}: {error}", path.display()))?;
        if &actual_digest != digest {
            return Err(format!("raw trial {} does not match its SHA-256", path.display()).into());
        }
        if raw.schema_version != RAW_VERSION
            || raw.plan_sha256 != self.plan_sha
            || raw.cell != row.cell
            || raw.workload_sha256 != row.workload_sha256
            || raw.pair != pair
            || raw.engine != trial.engine
            || raw.binary_sha256 != trial.binary_sha256
            || raw.config_sha256 != trial.config_sha256
            || raw.host_profile_sha256 != trial.host_profile_sha256
        {
            return Err(format!(
                "raw trial {} has a mismatched plan, cell, pair or engine identity",
                path.display()
            )
            .into());
        }
        let measured = raw.measure()?;
        if trial.requests != raw.requests.len() as u32
            || trial.failed_requests != measured.failed_requests
            || trial.incorrect_outputs != measured.incorrect_outputs
            || trial.requests_with_expected_cache_path
                != raw.requests.len() as u32 - measured.failed_requests
            || trial.metrics != measured.metrics
        {
            return Err(format!(
                "trial summary differs from raw measurements in {}",
                path.display()
            )
            .into());
        }
        Ok(measured.request_sequence_sha256)
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RawTrial {
    schema_version: u32,
    plan_sha256: String,
    workload_sha256: String,
    cell: Cell,
    pair: usize,
    engine: Engine,
    binary_sha256: String,
    config_sha256: String,
    host_profile_sha256: String,
    duration_ns: u64,
    requests: Vec<Request>,
    memory: Vec<MemorySample>,
}

impl RawTrial {
    fn summary(&self, digest: String) -> DynResult<Trial> {
        if self.schema_version != RAW_VERSION
            || !(1..=PAIRS).contains(&self.pair)
            || ![
                &self.plan_sha256,
                &self.workload_sha256,
                &self.binary_sha256,
                &self.config_sha256,
                &self.host_profile_sha256,
            ]
            .into_iter()
            .all(|value| is_digest(value, 64))
        {
            return Err("invalid raw trial identity".into());
        }
        let observed = self.measure()?;
        Ok(Trial {
            engine: self.engine,
            binary_sha256: self.binary_sha256.clone(),
            config_sha256: self.config_sha256.clone(),
            host_profile_sha256: self.host_profile_sha256.clone(),
            raw_artifact_sha256: digest,
            requests: REQUESTS,
            failed_requests: observed.failed_requests,
            incorrect_outputs: observed.incorrect_outputs,
            requests_with_expected_cache_path: REQUESTS - observed.failed_requests,
            metrics: observed.metrics,
        })
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Request {
    request_sha256: String,
    prompt_tokens: u32,
    result: RequestResult,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
enum RequestResult {
    Failed,
    Completed {
        observed_cache: CacheState,
        cached_tokens: u32,
        output_tokens: u32,
        output_correct: bool,
        inference_ttft_ns: u64,
        end_to_end_ttft_ns: u64,
        prefill_ns: u64,
        decode_ns: u64,
    },
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct MemorySample {
    offset_ns: u64,
    host_bytes: u64,
    device_bytes: u64,
}
