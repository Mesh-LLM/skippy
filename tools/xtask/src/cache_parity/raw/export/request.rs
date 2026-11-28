use super::{CacheState, DynResult, Engine, Request, Trace, load_json, ram, restart};
use crate::{
    artifact,
    cache_parity::{capture::replay_transcript, raw::RequestResult},
};
use serde::Deserialize;
use serde_json::Value;
use std::{fs, path::Path};

#[derive(Deserialize)]
pub(super) struct Input {
    request_sha256: String,
    expected_content: Option<String>,
    prompt_tokens: Option<u32>,
    pub(super) seed: Option<Seed>,
    pub(super) eviction: Option<Seed>,
}

#[derive(Deserialize)]
pub(super) struct Seed {
    pub(super) request_sha256: String,
}

#[derive(Deserialize)]
pub(super) struct Receipt {
    ordinal: usize,
    burst: usize,
    pub(super) started_ns: u64,
    pub(super) finished_ns: u64,
    request_sha256: String,
    output_matches: Option<bool>,
    request_error: Option<String>,
}

#[derive(Deserialize)]
struct TranscriptReceipt {
    schema_version: u32,
    engine: Engine,
    request_sha256: String,
    response_sha256: String,
    observation: Option<Value>,
    error: Option<String>,
}

pub(super) enum Source {
    Stream(CacheState),
    Resident,
    Restarted(Vec<restart::Snapshot>),
    Ram(ram::Proof),
}

struct Tier {
    state: CacheState,
    restore_client_ns: u64,
    restore_engine_ns: u64,
    before_disk: Option<u64>,
    eviction_cached: Option<u64>,
}

impl Source {
    fn tier(&self, directory: &Path, slot: usize, body: &Value) -> DynResult<Tier> {
        if !matches!(self, Self::Stream(_))
            && (body["id_slot"] != slot || body["cache_prompt"] != true)
        {
            return Err(
                "measured replay did not pin its prepared slot and enable cache_prompt".into(),
            );
        }
        let mut tier = Tier {
            state: CacheState::PostRestartL3,
            restore_client_ns: 0,
            restore_engine_ns: 0,
            before_disk: None,
            eviction_cached: None,
        };
        match self {
            Self::Stream(state) => tier.state = *state,
            Self::Resident => tier.state = CacheState::WarmL1,
            Self::Restarted(snapshots) => {
                (tier.restore_client_ns, tier.restore_engine_ns) = match snapshots.get(slot) {
                    Some(snapshot) => snapshot.restore_time(directory)?,
                    None if snapshots.is_empty() => (0, 0),
                    None => return Err("missing native snapshot for replay slot".into()),
                };
            }
            Self::Ram(proof) => {
                tier.state = CacheState::L2Restore;
                match proof {
                    ram::Proof::Candidate { disk_before } => tier.before_disk = Some(*disk_before),
                    ram::Proof::Baseline(slots) => {
                        let (ns, cached) = slots.get(slot).ok_or("missing native L2 slot proof")?;
                        tier.restore_engine_ns = *ns;
                        tier.eviction_cached = Some(*cached);
                    }
                }
            }
        }
        Ok(tier)
    }
}

pub(super) fn collect(
    bursts: &[Vec<Input>],
    trace: &Trace,
    root: &Path,
    leg: &Path,
    engine: Engine,
    source: Source,
) -> DynResult<Vec<Request>> {
    let mut previous_end = 0;
    let mut offset = 0;
    let mut rows = Vec::new();
    for (burst_index, burst) in bursts.iter().enumerate() {
        if !(1..=32).contains(&burst.len()) {
            return Err("invalid planned trace burst size".into());
        }
        let mut burst_end = previous_end;
        for (slot, input) in burst.iter().enumerate() {
            let timing = trace
                .requests
                .get(offset)
                .ok_or("trace omitted a planned request")?;
            offset += 1;
            if timing.ordinal != offset
                || timing.burst != burst_index + 1
                || timing.started_ns < previous_end
                || timing.finished_ns <= timing.started_ns
                || timing.finished_ns > trace.duration_ns
                || timing.request_sha256 != input.request_sha256
            {
                return Err(
                    format!("request {offset} has inconsistent workload order or timing").into(),
                );
            }
            let directory = leg.join(format!("request-{offset:03}"));
            rows.push(
                read(root, &directory, input, timing, engine, &source, slot)
                    .map_err(|error| format!("request {offset}: {error}"))?,
            );
            burst_end = burst_end.max(timing.finished_ns);
        }
        previous_end = burst_end;
    }
    if offset != trace.requests.len() {
        return Err("trace contains unplanned requests".into());
    }
    Ok(rows)
}

fn read(
    root: &Path,
    directory: &Path,
    input: &Input,
    timing: &Receipt,
    engine: Engine,
    source: &Source,
    slot: usize,
) -> DynResult<Request> {
    let receipt: TranscriptReceipt = load_json(&directory.join("observation.json"))?;
    if !crate::cache_parity::contract::is_digest(&input.request_sha256, 64)
        || receipt.schema_version != 3
        || receipt.engine != engine
        || receipt.request_sha256 != input.request_sha256
        || receipt.error != timing.request_error
    {
        return Err("transcript metadata does not match the trace".into());
    }
    let request = fs::read(directory.join("request.json"))?;
    let sealed = root
        .join("inputs")
        .join(format!("{}.json", input.request_sha256));
    let parsed: Value = serde_json::from_slice(&request)?;
    if artifact::digest(request.as_slice())? != input.request_sha256
        || fs::read(sealed)? != request
        || !parsed["chat_template"].is_null()
    {
        return Err(
            "request bytes or embedded-template contract does not match the workload".into(),
        );
    }
    let response = fs::read(directory.join("response.sse"))?;
    if artifact::digest(response.as_slice())? != receipt.response_sha256 {
        return Err("response transcript hash does not match its receipt".into());
    }
    let (prompt_tokens, result) = match (receipt.error, receipt.observation) {
        (None, Some(observation)) => {
            let tier = source.tier(directory, slot, &parsed)?;
            completed(input, timing, &observation, &response, engine, tier)?
        }
        (Some(_), None) if timing.output_matches.is_none() => (
            input
                .prompt_tokens
                .filter(|count| *count > 0)
                .ok_or("a failed request needs an independently pinned prompt token count")?,
            RequestResult::Failed,
        ),
        _ => return Err("invalid transcript outcome".into()),
    };
    Ok(Request {
        request_sha256: input.request_sha256.clone(),
        prompt_tokens,
        result,
    })
}

fn completed(
    input: &Input,
    timing: &Receipt,
    observation: &Value,
    response: &[u8],
    engine: Engine,
    tier: Tier,
) -> DynResult<(u32, RequestResult)> {
    let stream = replay_transcript(response, engine)?;
    for field in [
        "content",
        "finish_reason",
        "prompt_tokens",
        "normalized_completion_tokens",
        "cached_tokens",
        "sampled_phases",
        "final_server_timings",
    ] {
        if observation[field] != stream[field] {
            return Err(format!("retained {field} differs from the response transcript").into());
        }
    }
    let prompt_tokens = tokens(&stream["prompt_tokens"])?;
    let cached_tokens = tokens(&stream["cached_tokens"])?;
    let output_tokens = tokens(&stream["normalized_completion_tokens"])?;
    if input
        .prompt_tokens
        .is_some_and(|expected| expected != prompt_tokens)
    {
        return Err("prompt token count changed from the planned workload".into());
    }
    let expected = input
        .expected_content
        .as_ref()
        .ok_or("workload needs an exact text oracle")?;
    let output_correct = stream["content"].as_str() == Some(expected);
    if timing.output_matches != Some(output_correct) {
        return Err("retained output check differs from the frozen workload and response".into());
    }
    let observed_cache = observed_state(&stream, engine, cached_tokens, tier.state)?;
    if observed_cache != tier.state {
        return Err("measured request did not use the planned tier".into());
    }
    if let Some(before) = tier.before_disk
        && stream["final_server_timings"]["cache_disk_payload_bytes_read_total"].as_u64()
            != Some(before)
    {
        return Err("measured L2 replay read disk payload bytes".into());
    }
    if tier
        .eviction_cached
        .is_some_and(|n| n >= u64::from(cached_tokens))
    {
        return Err("native L2 replay did not reuse more than the eviction request".into());
    }
    let inference_ttft_ns = millis(&stream["sampled_phases"]["ttft_ms"])?
        .checked_add(tier.restore_engine_ns)
        .ok_or("native slot time overflowed")?;
    let decode_ns = millis(&stream["sampled_phases"]["decode_ms"])?;
    let end_to_end_ttft_ns = observation["first_token_ns"]
        .as_u64()
        .and_then(|ns| ns.checked_add(tier.restore_client_ns))
        .ok_or("missing client first-token timestamp")?;
    let last = observation["last_token_ns"]
        .as_u64()
        .and_then(|ns| ns.checked_add(tier.restore_client_ns))
        .ok_or("missing client last-token timestamp")?;
    if end_to_end_ttft_ns > last || last > timing.finished_ns - timing.started_ns {
        return Err("client token arrival lies outside the measured request window".into());
    }
    // Both engines report model-service time through the first sampled
    // token. That covers the final prompt evaluation, even on a resident hit.
    let prefill_ns = if cached_tokens == prompt_tokens {
        0
    } else {
        inference_ttft_ns
    };
    Ok((
        prompt_tokens,
        RequestResult::Completed {
            observed_cache,
            cached_tokens,
            output_tokens,
            output_correct,
            inference_ttft_ns,
            end_to_end_ttft_ns,
            prefill_ns,
            decode_ns,
        },
    ))
}

fn observed_state(
    stream: &Value,
    engine: Engine,
    cached: u32,
    planned: CacheState,
) -> DynResult<CacheState> {
    let native = &stream["final_server_timings"];
    match (engine, cached, native["cache_source"].as_str()) {
        (Engine::Candidate, 0, None) => Ok(CacheState::ColdPrefill),
        (Engine::Candidate, n, Some("resident" | "radix")) if n > 0 => Ok(CacheState::WarmL1),
        (Engine::Candidate, n, Some("l2")) if n > 0 => Ok(CacheState::L2Restore),
        (Engine::Candidate, n, Some("l3" | "l3-cachegen")) if n > 0 => {
            Ok(CacheState::PostRestartL3)
        }
        (Engine::Baseline, n, _) if native["cache_n"].as_u64() == Some(u64::from(n)) => {
            Ok(if n == 0 {
                CacheState::ColdPrefill
            } else if matches!(planned, CacheState::L2Restore | CacheState::PostRestartL3) {
                planned
            } else {
                CacheState::WarmL1
            })
        }
        _ => Err("ambiguous or unsupported cache-source observations".into()),
    }
}

fn tokens(value: &Value) -> DynResult<u32> {
    Ok(u32::try_from(
        value.as_u64().ok_or("missing measured token count")?,
    )?)
}

pub(super) fn millis(value: &Value) -> DynResult<u64> {
    let ms = value
        .as_f64()
        .filter(|ms| ms.is_finite() && *ms >= 0.0 && *ms <= u64::MAX as f64 / 1_000_000.0)
        .ok_or("missing or invalid native timing")?;
    Ok((ms * 1_000_000.0).round() as u64)
}

pub(super) fn prepared_stream(
    root: &Path,
    directory: &Path,
    engine: Engine,
    slot: usize,
    input: &Seed,
) -> DynResult<Value> {
    let digest = &input.request_sha256;
    if !crate::cache_parity::contract::is_digest(digest, 64) {
        return Err("preparation has an invalid planned digest".into());
    }
    let bytes = fs::read(directory.join("request.json"))?;
    let exact = root.join("inputs").join(format!("{digest}.json"));
    let body: Value = serde_json::from_slice(&bytes)?;
    if fs::read(exact)? != bytes
        || artifact::digest(&bytes[..])? != *digest
        || body["id_slot"] != slot
        || body["cache_prompt"] != true
        || !body["chat_template"].is_null()
    {
        return Err("preparation differs from its frozen slot input".into());
    }
    let response = fs::read(directory.join("response.sse"))?;
    let receipt: TranscriptReceipt = load_json(&directory.join("observation.json"))?;
    if receipt.schema_version != 3
        || receipt.engine != engine
        || receipt.request_sha256 != *digest
        || receipt.response_sha256 != artifact::digest(&response[..])?
        || receipt.error.is_some()
    {
        return Err("invalid preparation stream receipt".into());
    }
    replay_transcript(&response, engine)
}

pub(super) fn verify_baseline_resident(config: &Path) -> DynResult<()> {
    let config: Value = load_json(config)?;
    let args = config["llama_server"]["args"]
        .as_array()
        .ok_or("missing baseline arguments")?;
    let mut pref = None;
    for (index, argument) in args.iter().filter_map(Value::as_str).enumerate() {
        let (key, value) = argument
            .split_once('=')
            .map_or((argument, None), |(key, value)| (key, Some(value)));
        if key == "--slot-save-path" {
            return Err("resident export requires an engine without disk slot setup".into());
        }
        if matches!(key, "--cache-ram" | "-cram") {
            let value = value.or_else(|| args.get(index + 1).and_then(Value::as_str));
            if value != Some("0") || pref.replace(()).is_some() {
                return Err(
                    "resident export requires a single disabled baseline RAM-cache setting".into(),
                );
            }
        }
    }
    if pref.is_none() {
        return Err("resident export requires explicit baseline --cache-ram 0".into());
    }
    Ok(())
}
