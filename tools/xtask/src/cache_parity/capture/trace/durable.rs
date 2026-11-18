use super::{DynResult, Engine, Request, Workload, write_json_file};
use crate::cache_parity::{
    capture::{host_ram, restart, server::Server},
    contract::CacheState,
};
use process_supervision::Cancellation;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
};

#[derive(Clone, Copy)]
pub(in crate::cache_parity::capture) enum Restore<'a> {
    L2 { seed: &'a [u8], eviction: &'a [u8] },
    L3 { seed: &'a [u8] },
}

impl Restore<'_> {
    pub(in crate::cache_parity::capture) fn state(&self) -> CacheState {
        match self {
            Self::L2 { .. } => CacheState::L2Restore,
            Self::L3 { .. } => CacheState::PostRestartL3,
        }
    }

    pub(in crate::cache_parity::capture) fn validate(
        self,
        workload: &Workload,
        skippy: &Server,
        llama: &Server,
    ) -> DynResult<()> {
        if workload.bursts.iter().any(|burst| burst.len() > 1) {
            return Err("independent restore diagnostics require one request per burst. Use per-request seeds for post-restart bursts".into());
        }
        restart::validate_args(skippy, llama)?;
        for request in workload.bursts.iter().flatten() {
            if serde_json::from_slice::<Value>(&request.bytes)?["cache_prompt"] != true {
                return Err("restore traces require cache_prompt on each replay".into());
            }
            if let Self::L2 { seed, eviction } = self {
                host_ram::validate(skippy, &request.bytes, eviction, Some(seed))?;
            }
        }
        Ok(())
    }

    pub(in crate::cache_parity::capture) fn capture(
        self,
        engine: Engine,
        server: &Server,
        workload: &Workload,
        output: &Path,
        cancellation: &Cancellation,
    ) -> DynResult<()> {
        fs::create_dir(output)?;
        let mut rows = Vec::new();
        for (index, request) in workload.bursts.iter().flatten().enumerate() {
            if cancellation.is_cancelled() {
                return Err("restore trace was interrupted".into());
            }
            let ordinal = index + 1;
            let relative = PathBuf::from(format!("request-{ordinal:03}"));
            let directory = output.join(&relative);
            let result = self
                .capture_request(engine, server, request, &directory, cancellation)
                .and_then(|()| self.measurement(&directory, request));
            let cleanup = remove_private_state(&directory);
            let (measurement, error) =
                match result.and_then(|measurement| cleanup.map(|()| measurement)) {
                    Ok(measurement) => (Some(measurement), None),
                    Err(error) => (None, Some(error.to_string())),
                };
            rows.push(Row {
                ordinal,
                request_sha256: &request.sha256,
                directory: relative,
                measurement,
                error,
            });
            write_json_file(
                &output.join("trace.json"),
                &json!({
                    "schema_version": 1,
                    "scope": "independent_cache_restores",
                    "engine": engine,
                    "cache_state": self.state(),
                    "requests": rows,
                }),
            )?;
        }
        if rows.iter().any(|row| row.error.is_some()) {
            return Err("one or more restore requests failed; see trace.json".into());
        }
        Ok(())
    }

    fn capture_request(
        self,
        engine: Engine,
        server: &Server,
        request: &Request,
        directory: &Path,
        cancellation: &Cancellation,
    ) -> DynResult<()> {
        match self {
            Self::L2 { seed, eviction } => host_ram::capture(
                engine,
                server,
                seed,
                eviction,
                &request.bytes,
                directory,
                cancellation,
            ),
            Self::L3 { seed } => restart::capture(
                engine,
                server,
                seed,
                &request.bytes,
                directory,
                cancellation,
            ),
        }
    }

    fn measurement(self, path: &Path, request: &Request) -> DynResult<Measurement> {
        let (relative_summary, relative_observation) = match self {
            Self::L2 { .. } => ("host-restore.json", "capture"),
            Self::L3 { .. } => ("restart.json", "replay/capture"),
        };
        let observed = restart::observation(&path.join(relative_observation))?;
        let restore: RestoreSummary =
            serde_json::from_reader(fs::File::open(path.join(relative_summary))?)?;
        Ok(Measurement {
            summary: restore,
            observation: PathBuf::from(relative_observation).join("observation.json"),
            output_matches: request
                .expected_content
                .as_ref()
                .map(|expected| observed["content"].as_str() == Some(expected)),
        })
    }
}

pub(super) fn remove_private_state(directory: &Path) -> DynResult<()> {
    let state = directory.join("state");
    if state.try_exists()? {
        fs::remove_dir_all(state)?;
    }
    Ok(())
}

#[derive(Deserialize, Serialize)]
struct RestoreSummary {
    cache_source: String,
    cached_tokens: u32,
    prompt_tokens: u32,
    end_to_end_ttft_ns: u64,
    sampled_ttft_ms: Option<f64>,
}

#[derive(Serialize)]
struct Measurement {
    #[serde(flatten)]
    summary: RestoreSummary,
    observation: PathBuf,
    output_matches: Option<bool>,
}

#[derive(Serialize)]
struct Row<'a> {
    ordinal: usize,
    request_sha256: &'a str,
    directory: PathBuf,
    measurement: Option<Measurement>,
    error: Option<String>,
}
