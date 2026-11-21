use super::{
    Backend, CONTEXT_TOKENS, LLAMA_PIN, MAX_TOKENS, TOP_K,
    conversation::{self, Case},
    fixture::{Family, Fixture},
    reference, structured, template,
};
use crate::kv_integration::StagePrefixCachePayload;
use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use skippy_model_artifact::kv_fixtures::valid_sha256;
use std::collections::BTreeMap;

// Compare normalized logits from the unfiltered softmax at each decode step.
const LOGPROB_TOLERANCE: f64 = 0.05;
const SCHEMA_VERSION: u32 = 9;
const SCENARIO: &str = "cross_tier_replay";
const ABI: [u32; 3] = [
    skippy_runtime::ABI_VERSION_MAJOR,
    skippy_runtime::ABI_VERSION_MINOR,
    skippy_runtime::ABI_VERSION_PATCH,
];

#[derive(Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Status {
    Pass,
    Fail,
}

#[derive(Deserialize, Serialize)]
pub(super) struct Report {
    schema_version: u32,
    scenario: String,
    pub(super) backend: Backend,
    pub(super) status: Status,
    model_id: String,
    pub(super) fixture: Fixture,
    llama_pin: String,
    skippy_abi: [u32; 3],
    logprob_abs_tolerance: f64,
    context_tokens: u32,
    pub(super) model_sha256: Option<String>,
    pub(super) skippy_binary_sha256: Option<String>,
    pub(super) reference: Option<Reference>,
    pub(super) skippy_offloaded_layers: Option<usize>,
    pub(super) cold: Option<Candidate>,
    pub(super) seed: Option<Candidate>,
    pub(super) replay: Option<Candidate>,
    pub(super) conversations: BTreeMap<Case, conversation::Observation>,
    #[serde(skip_deserializing)]
    conversation_results: BTreeMap<Case, conversation::ResultRow>,
    pub(super) structured: BTreeMap<structured::Case, structured::Observation>,
    #[serde(skip_deserializing)]
    structured_results: BTreeMap<structured::Case, structured::ResultRow>,
    pub(super) template: Option<template::Observation>,
    #[serde(skip_deserializing)]
    template_result: Option<Status>,
    pub(super) durable: Option<DurableObservations>,
    pub(super) ownership: Option<OwnershipObservation>,
    pub(super) restart: Option<RestartObservation>,
    pub(super) error: Option<String>,
    #[serde(skip_deserializing)]
    validation_errors: Vec<String>,
}

impl Report {
    pub(super) fn new(backend: Backend, fixture: Fixture) -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            scenario: SCENARIO.into(),
            backend,
            status: Status::Fail,
            model_id: fixture.model_id(),
            fixture,
            llama_pin: LLAMA_PIN.trim().into(),
            skippy_abi: ABI,
            logprob_abs_tolerance: LOGPROB_TOLERANCE,
            context_tokens: CONTEXT_TOKENS,
            model_sha256: None,
            skippy_binary_sha256: None,
            reference: None,
            skippy_offloaded_layers: None,
            cold: None,
            seed: None,
            replay: None,
            conversations: BTreeMap::new(),
            conversation_results: BTreeMap::new(),
            structured: BTreeMap::new(),
            structured_results: BTreeMap::new(),
            template: None,
            template_result: None,
            durable: None,
            ownership: None,
            restart: None,
            error: None,
            validation_errors: Vec::new(),
        }
    }
}

#[derive(Deserialize, Serialize)]
pub(super) struct Reference {
    pub(super) binary_sha256: String,
    pub(super) version: String,
    pub(super) offloaded_layers: usize,
    pub(super) template_sha256: String,
    pub(super) generation: Generation,
    pub(super) conversations: BTreeMap<Case, Generation>,
    pub(super) structured: BTreeMap<structured::Case, structured::Attempt>,
}

#[derive(Deserialize, Serialize, Clone)]
pub(super) struct Candidate {
    pub(super) generation: Generation,
    pub(super) chat_checkpoint_tokens: Option<u32>,
    pub(super) cache: CacheObservation,
}

#[derive(Deserialize, Serialize, Clone)]
pub(super) struct Generation {
    pub(super) prompt_sha256: String,
    pub(super) prompt_ids: Vec<i32>,
    pub(super) generated_ids: Vec<i32>,
    pub(super) content: String,
    pub(super) top_logprobs: Vec<Vec<TokenScore>>,
}

#[derive(Deserialize, Serialize, Clone)]
pub(super) struct TokenScore {
    pub(super) id: i32,
    pub(super) logprob: Option<f64>,
}

#[derive(Default, Deserialize, Serialize, Clone)]
pub(super) struct CacheObservation {
    pub(super) status: String,
    pub(super) hit_kind: Option<String>,
    pub(super) restore_source: Option<String>,
    pub(super) payload_kind: Option<String>,
    pub(super) prompt_tokens: u32,
    pub(super) cached_tokens: u32,
    pub(super) matched_tokens: u32,
    pub(super) suffix_tokens: u32,
}

#[derive(Default, Deserialize, Serialize)]
pub(super) struct DurableObservations {
    pub(super) checkpoint_tokens: Option<u32>,
    pub(super) seed: Option<Candidate>,
    pub(super) first_l3: Option<Candidate>,
    pub(super) second_l3: Option<Candidate>,
    pub(super) l2: Option<Candidate>,
    pub(super) l2_disk_payload_bytes: Option<u64>,
    pub(super) namespace_miss: Option<Candidate>,
    pub(super) evictions: Vec<Eviction>,
}

#[derive(Deserialize, Serialize)]
pub(super) struct Eviction {
    pub(super) resident_entries: usize,
    pub(super) exact_entries: usize,
}

#[derive(Deserialize, Serialize)]
pub(super) struct CleanRestartObservation {
    pub(super) pid: u32,
    pub(super) offloaded_layers: usize,
    pub(super) replay: Candidate,
}

#[derive(Deserialize, Serialize)]
pub(super) struct OwnershipObservation {
    pub(super) publisher_pid: u32,
    pub(super) publisher_offloaded_layers: usize,
    pub(super) clean_restart: CleanRestartObservation,
    pub(super) lock_conflict: String,
    pub(super) contender: Candidate,
    pub(super) preserved_pack_sha256: String,
}

#[derive(Deserialize, Serialize)]
pub(super) struct RestartObservation {
    pub(super) pid: u32,
    pub(super) offloaded_layers: usize,
    pub(super) replay: Candidate,
    pub(super) corrupt_fallback: Candidate,
    pub(super) corrupt_objects: u64,
    pub(super) reconciled_temporary_files: u64,
}

pub(super) fn validate_measurements(report: &mut Report) -> Result<()> {
    require_expected_identity(report)?;
    validate(report)
}

fn require_expected_identity(report: &Report) -> Result<()> {
    ensure!(
        report.schema_version == SCHEMA_VERSION
            && report.scenario == SCENARIO
            && report.llama_pin == LLAMA_PIN.trim()
            && report.skippy_abi == ABI
            && report.context_tokens == CONTEXT_TOKENS
            && report.logprob_abs_tolerance == LOGPROB_TOLERANCE,
        "correctness evidence does not match this qualification contract"
    );
    let pinned = Fixture::load(report.fixture.family)?;
    ensure!(
        report.fixture == pinned
            && report.model_id == pinned.model_id()
            && report.model_sha256.as_deref() == Some(pinned.digest()),
        "correctness evidence does not match the pinned model identity"
    );
    let reference = report
        .reference
        .as_ref()
        .context("missing llama.cpp reference")?;
    ensure!(
        report
            .skippy_binary_sha256
            .as_deref()
            .is_some_and(valid_sha256)
            && valid_sha256(&reference.binary_sha256)
            && reference::version_matches(&reference.version, LLAMA_PIN.trim()),
        "correctness evidence lacks matching binary provenance"
    );
    Ok(())
}

pub(super) fn verify_saved(report: &mut Report) -> Result<()> {
    ensure!(
        report.status == Status::Pass && report.error.is_none(),
        "reported qualification run failed: {}",
        report.error.as_deref().unwrap_or("no error recorded")
    );
    validate_measurements(report)
}

pub(super) fn validate(report: &mut Report) -> Result<()> {
    let template = report
        .template
        .as_ref()
        .context("missing chat template identity observations")
        .and_then(|observation| observation.validate(report.fixture.family));
    report.template_result = Some(if template.is_ok() {
        Status::Pass
    } else {
        Status::Fail
    });
    let mut errors = Vec::new();
    for result in [
        validate_l1(report),
        validate_durable(report),
        validate_reference(report),
        template.context("chat template identity qualification"),
    ] {
        if let Err(error) = result {
            errors.push(format!("{error:#}"));
        }
    }
    if let Some(cold) = &report.cold {
        for (label, case) in replay_cases(report) {
            if let Some(candidate) = case
                && let Err(error) =
                    compare(&cold.generation, &candidate.generation).with_context(|| label)
            {
                errors.push(format!("{error:#}"));
            }
        }
    }
    report.conversation_results = conversation::validate(report);
    errors.extend(
        report
            .conversation_results
            .values()
            .flat_map(|row| row.errors.iter().cloned()),
    );
    let empty_reference = BTreeMap::new();
    let structured_reference = report
        .reference
        .as_ref()
        .map_or(&empty_reference, |value| &value.structured);
    report.structured_results = structured::validate(
        report.fixture.family,
        structured_reference,
        &report.structured,
    );
    errors.extend(
        report
            .structured_results
            .values()
            .flat_map(|row| row.errors.iter().cloned()),
    );
    report.validation_errors = errors;
    if let Some(first) = report.validation_errors.first() {
        bail!(
            "{} qualification checks failed. {first}",
            report.validation_errors.len()
        );
    }
    Ok(())
}

fn replay_cases(report: &Report) -> [(&'static str, Option<&Candidate>); 11] {
    let durable = report.durable.as_ref();
    let ownership = report.ownership.as_ref();
    let restart = report.restart.as_ref();
    [
        ("L1 seed against cold Skippy", report.seed.as_ref()),
        ("L1 replay against cold Skippy", report.replay.as_ref()),
        (
            "durable seed against cold Skippy",
            durable.and_then(|case| case.seed.as_ref()),
        ),
        (
            "first disk fill against cold Skippy",
            durable.and_then(|case| case.first_l3.as_ref()),
        ),
        (
            "second disk fill against cold Skippy",
            durable.and_then(|case| case.second_l3.as_ref()),
        ),
        (
            "host-RAM replay against cold Skippy",
            durable.and_then(|case| case.l2.as_ref()),
        ),
        (
            "clean restart disk replay against cold Skippy",
            ownership.map(|case| &case.clean_restart.replay),
        ),
        (
            "lock contention fallback against cold Skippy",
            ownership.map(|case| &case.contender),
        ),
        (
            "crash restart disk replay against cold Skippy",
            restart.map(|case| &case.replay),
        ),
        (
            "isolated execution scope against cold Skippy",
            durable.and_then(|case| case.namespace_miss.as_ref()),
        ),
        (
            "corrupt L3 fallback against cold Skippy",
            restart.map(|case| &case.corrupt_fallback),
        ),
    ]
}

fn validate_durable(report: &Report) -> Result<()> {
    let family = report.fixture.family;
    let cold = report.cold.as_ref().context("missing cold output")?;
    let prompt_tokens = u32::try_from(cold.generation.prompt_ids.len())?;
    let durable = report
        .durable
        .as_ref()
        .context("missing cross-tier evidence")?;
    let restart = report
        .restart
        .as_ref()
        .context("missing restart evidence")?;
    let ownership = report
        .ownership
        .as_ref()
        .context("missing exclusive ownership evidence")?;
    let checkpoint = durable
        .checkpoint_tokens
        .context("missing durable checkpoint token count")?;
    let seed = durable.seed.as_ref().context("missing durable seed")?;
    validate_miss(prompt_tokens, seed, family).context("durable seed accounting")?;
    ensure!(
        durable.evictions.len() == 3
            && durable
                .evictions
                .iter()
                .all(|eviction| eviction.exact_entries > 0)
            && (report.fixture.family.payload() != StagePrefixCachePayload::ResidentKv
                || durable.evictions[0].resident_entries > 0),
        "the expected in-memory cache entries were not evicted"
    );
    ensure!(
        durable.l2_disk_payload_bytes == Some(0),
        "L2 restore read a disk payload"
    );
    ensure!(
        ownership.publisher_offloaded_layers > 0
            && ownership.clean_restart.offloaded_layers > 0
            && restart.offloaded_layers > 0,
        "an L3 owner did not execute on the qualification GPU"
    );
    ensure!(
        ownership.publisher_pid != 0
            && ownership.clean_restart.pid != 0
            && restart.pid != 0
            && ownership.publisher_pid != ownership.clean_restart.pid
            && ownership.clean_restart.pid != restart.pid
            && ownership.publisher_pid != restart.pid
            && !ownership.lock_conflict.is_empty()
            && valid_sha256(&ownership.preserved_pack_sha256),
        "incomplete exclusive L3 ownership observations"
    );
    ensure!(
        restart.reconciled_temporary_files > 0,
        "replacement did not reconcile the interrupted write"
    );
    validate_miss(prompt_tokens, &ownership.contender, family)
        .context("lock contention fallback accounting")?;
    for (label, tier, replay) in [
        ("first disk fill", "l3", durable.first_l3.as_ref()),
        ("second disk fill", "l3", durable.second_l3.as_ref()),
        ("host-RAM replay", "l2", durable.l2.as_ref()),
        (
            "clean restart disk replay",
            "l3",
            Some(&ownership.clean_restart.replay),
        ),
        ("crash restart disk replay", "l3", Some(&restart.replay)),
    ] {
        let replay = replay.with_context(|| format!("missing {label}"))?;
        validate_durable_replay(prompt_tokens, replay, tier, checkpoint, family)
            .with_context(|| label)?;
        if report.fixture.family.payload() == StagePrefixCachePayload::KvRecurrent {
            ensure!(
                replay.cache.payload_kind.as_deref() == Some("kv-recurrent"),
                "{label} must restore KV and recurrent state"
            );
        }
    }
    let namespace = durable
        .namespace_miss
        .as_ref()
        .context("missing namespace-isolation evidence")?;
    validate_miss(prompt_tokens, namespace, family)
        .context("isolated execution scope accounting")?;
    ensure!(
        restart.corrupt_objects > 0,
        "L3 reported no corrupt object after truncation"
    );
    validate_miss(prompt_tokens, &restart.corrupt_fallback, family)
        .context("corrupt L3 fallback accounting")?;
    Ok(())
}

pub(super) fn validate_miss(
    prompt_tokens: u32,
    candidate: &Candidate,
    family: Family,
) -> Result<()> {
    let cache = &candidate.cache;
    ensure!(
        cache.status == "miss"
            && cache.hit_kind.is_none()
            && cache.restore_source.is_none()
            && cache.prompt_tokens == prompt_tokens
            && cache.cached_tokens == 0
            && cache.matched_tokens == 0
            && cache.suffix_tokens == family.prefill_tokens(prompt_tokens),
        "expected a cache miss and complete prefill"
    );
    Ok(())
}

pub(super) fn validate_durable_replay(
    prompt_tokens: u32,
    replay: &Candidate,
    tier: &str,
    checkpoint: u32,
    family: Family,
) -> Result<()> {
    ensure!(
        checkpoint > 0 && checkpoint < family.prefill_tokens(prompt_tokens),
        "invalid checkpoint length"
    );
    let cache = &replay.cache;
    ensure!(
        cache.status == "hit"
            && cache.hit_kind.as_deref() == Some("exact_prefix")
            && cache.restore_source.as_deref() == Some(tier),
        "expected a successful {tier} restore, observed {:?}",
        cache.restore_source
    );
    ensure!(
        cache.prompt_tokens == prompt_tokens
            && cache.cached_tokens == checkpoint
            && cache.matched_tokens == checkpoint
            && cache.suffix_tokens == family.prefill_tokens(prompt_tokens) - checkpoint,
        "incorrect token accounting for {tier} restore of {checkpoint} prefill tokens"
    );
    Ok(())
}

fn validate_reference(report: &Report) -> Result<()> {
    let reference = report
        .reference
        .as_ref()
        .context("missing llama.cpp reference")?;
    let cold = report.cold.as_ref().context("missing cold Skippy result")?;
    ensure!(
        reference.offloaded_layers > 0
            && report
                .skippy_offloaded_layers
                .is_some_and(|layers| layers > 0),
        "both engines must have executed on the qualification GPU"
    );
    ensure!(
        reference.template_sha256 == report.fixture.chat_template_sha256,
        "llama.cpp chat-template digest differs from the pinned fixture"
    );
    ensure!(
        reference.generation.generated_ids.len() == MAX_TOKENS as usize
            && !reference.generation.content.is_empty(),
        "reference must generate {MAX_TOKENS} tokens and nonempty output"
    );
    compare(&reference.generation, &cold.generation).context("cold Skippy against pinned llama.cpp")
}

pub(super) fn validate_l1(report: &Report) -> Result<()> {
    let cold = report.cold.as_ref().context("missing cold Skippy result")?;
    let seed = report.seed.as_ref().context("missing L1 seed")?;
    let replay = report.replay.as_ref().context("missing L1 replay")?;
    let prompt_tokens = u32::try_from(cold.generation.prompt_ids.len())?;
    ensure!(prompt_tokens > 64, "prompt is too short for L1 admission");
    for (case, expected_status) in [(cold, "disabled"), (seed, "miss")] {
        ensure!(
            case.cache.status == expected_status
                && case.cache.hit_kind.is_none()
                && case.cache.prompt_tokens == prompt_tokens
                && case.cache.cached_tokens == 0
                && case.cache.matched_tokens == 0,
            "{expected_status} generation reported unexpected cache reuse"
        );
    }
    let family = report.fixture.family;
    let expected_match = if family.payload() == StagePrefixCachePayload::ResidentKv {
        family.prefill_tokens(prompt_tokens)
    } else {
        seed.chat_checkpoint_tokens
            .context("hybrid seed did not render a chat checkpoint")?
    };
    validate_l1_replay(family, prompt_tokens, expected_match, replay)
}

pub(super) fn validate_l1_replay(
    family: Family,
    prompt_tokens: u32,
    expected_match: u32,
    replay: &Candidate,
) -> Result<()> {
    let prefill_tokens = family.prefill_tokens(prompt_tokens);
    ensure!(
        expected_match > 0 && expected_match <= prefill_tokens,
        "invalid L1 prefix length"
    );
    if family.payload() == StagePrefixCachePayload::ResidentKv {
        ensure!(
            replay.cache.status == "hit"
                && replay.cache.hit_kind.as_deref() == Some("resident_prefix")
                && replay.cache.restore_source.is_none(),
            "expected L1 resident_prefix hit, observed {:?}",
            replay.cache.hit_kind
        );
    } else {
        ensure!(
            replay.cache.status == "hit"
                && replay.cache.hit_kind.as_deref() == Some("exact_prefix")
                && replay.cache.restore_source.as_deref() == Some("radix")
                && replay.cache.payload_kind.as_deref() == Some("kv-recurrent"),
            "expected L1 radix restore of KV and recurrent state"
        );
    }
    ensure!(
        replay.cache.prompt_tokens == prompt_tokens
            && replay.cache.cached_tokens == expected_match
            && replay.cache.matched_tokens == expected_match
            && replay.cache.suffix_tokens == prefill_tokens - expected_match,
        "L1 did not restore its expected {expected_match}-token prefix"
    );
    Ok(())
}

pub(super) fn compare(expected: &Generation, actual: &Generation) -> Result<()> {
    ensure!(
        expected.prompt_sha256 == actual.prompt_sha256,
        "rendered prompt differs"
    );
    ensure!(
        expected.prompt_ids == actual.prompt_ids,
        "prompt token IDs differ"
    );
    if let Some((step, (left, right))) = expected
        .generated_ids
        .iter()
        .zip(&actual.generated_ids)
        .enumerate()
        .find(|(_, (left, right))| left != right)
    {
        bail!("greedy token IDs differ at decode step {step}: expected {left}, observed {right}");
    }
    ensure!(
        expected.generated_ids.len() == actual.generated_ids.len(),
        "greedy token counts differ"
    );
    ensure!(expected.content == actual.content, "output text differs");
    ensure!(
        expected.top_logprobs.len() == expected.generated_ids.len()
            && actual.top_logprobs.len() == actual.generated_ids.len(),
        "missing decoder checkpoint scores"
    );
    for (step, (left, right)) in expected
        .top_logprobs
        .iter()
        .zip(&actual.top_logprobs)
        .enumerate()
    {
        compare_scores(left, right).with_context(|| format!("decode checkpoint {step}"))?;
    }
    Ok(())
}

fn scores(row: &[TokenScore]) -> Result<BTreeMap<i32, f64>> {
    ensure!(row.len() == TOP_K as usize, "incomplete top-{TOP_K} row");
    let mut scores = BTreeMap::new();
    for token in row {
        let score = token
            .logprob
            .filter(|value| value.is_finite() && *value > f64::from(f32::MIN))
            .context("missing or non-finite logprob")?;
        ensure!(
            scores.insert(token.id, score).is_none(),
            "duplicate candidate token ID"
        );
    }
    Ok(scores)
}

fn compare_scores(left: &[TokenScore], right: &[TokenScore]) -> Result<()> {
    let left = scores(left)?;
    let right = scores(right)?;
    ensure!(
        left.keys().eq(right.keys()),
        "top-{TOP_K} alternatives differ"
    );
    for (token_id, expected) in left {
        let actual = right[&token_id];
        ensure!(
            (actual - expected).abs() <= LOGPROB_TOLERANCE,
            "normalized logit for token {token_id} differs: expected {expected}, observed {actual}"
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn passing_report() -> Report {
        let score = (0..TOP_K)
            .map(|value| TokenScore {
                id: value as i32,
                logprob: Some(-f64::from(value) - 0.5),
            })
            .collect::<Vec<_>>();
        let generation = Generation {
            prompt_sha256: "prompt".into(),
            prompt_ids: vec![1; 100],
            generated_ids: vec![2; MAX_TOKENS as usize],
            content: "output".into(),
            top_logprobs: vec![score; MAX_TOKENS as usize],
        };
        let cold = Candidate {
            generation: generation.clone(),
            chat_checkpoint_tokens: None,
            cache: CacheObservation {
                status: "disabled".into(),
                hit_kind: None,
                restore_source: None,
                payload_kind: None,
                prompt_tokens: 100,
                cached_tokens: 0,
                matched_tokens: 0,
                suffix_tokens: 0,
            },
        };
        let mut seed = cold.clone();
        seed.cache.status = "miss".into();
        let mut replay = cold.clone();
        replay.cache.status = "hit".into();
        replay.cache.hit_kind = Some("resident_prefix".into());
        replay.cache.cached_tokens = 99;
        replay.cache.matched_tokens = 99;
        let mut report = Report::new(
            Backend::Metal,
            Fixture::load(Family::DenseAttention).unwrap(),
        );
        report.reference = Some(Reference {
            binary_sha256: "reference".into(),
            version: "version".into(),
            offloaded_layers: 36,
            template_sha256: report.fixture.chat_template_sha256.clone(),
            generation,
            conversations: BTreeMap::new(),
            structured: BTreeMap::new(),
        });
        report.skippy_offloaded_layers = Some(36);
        report.cold = Some(cold);
        report.seed = Some(seed);
        report.replay = Some(replay);
        report
    }

    #[test]
    fn dense_evidence_accepts_a_matching_resident_replay() {
        validate_l1(&passing_report()).unwrap();
    }

    #[test]
    fn dense_evidence_rejects_wrong_output_and_wrong_tier() {
        let mut report = passing_report();
        report.replay.as_mut().unwrap().generation.generated_ids[0] ^= 1;
        report.reference.as_mut().unwrap().generation.generated_ids[1] ^= 1;
        assert!(validate(&mut report).is_err());
        assert!(
            report.validation_errors.iter().any(|error| error.contains(
                "L1 replay against cold Skippy: greedy token IDs differ at decode step 0"
            ))
        );
        assert!(report.validation_errors.iter().any(|error| error.contains(
            "cold Skippy against pinned llama.cpp: greedy token IDs differ at decode step 1"
        )));

        let mut report = passing_report();
        report.replay.as_mut().unwrap().cache.hit_kind = Some("exact_prefix".into());
        assert!(
            validate_l1(&report)
                .unwrap_err()
                .to_string()
                .contains("expected L1 resident_prefix")
        );
    }

    #[test]
    fn dense_evidence_rejects_wrong_accounting_or_incomplete_scores() {
        let mut report = passing_report();
        report.replay.as_mut().unwrap().cache.suffix_tokens = 1;
        assert!(
            validate_l1(&report)
                .unwrap_err()
                .to_string()
                .contains("expected 99-token prefix")
        );

        let mut report = passing_report();
        report.cold.as_mut().unwrap().generation.top_logprobs[0][0].logprob = Some(f64::NAN);
        assert!(
            format!("{:#}", validate_reference(&report).unwrap_err())
                .contains("non-finite logprob")
        );
    }

    #[test]
    fn durable_replay_requires_the_observed_tier_and_expected_suffix() {
        let mut candidate = passing_report().replay.unwrap();
        candidate.cache.hit_kind = Some("exact_prefix".into());
        candidate.cache.restore_source = Some("l3".into());
        candidate.cache.cached_tokens = 64;
        candidate.cache.matched_tokens = 64;
        candidate.cache.suffix_tokens = 35;
        let family = Family::DenseAttention;
        validate_durable_replay(100, &candidate, "l3", 64, family).unwrap();
        assert!(validate_durable_replay(100, &candidate, "l2", 64, family).is_err());
        candidate.cache.suffix_tokens += 1;
        assert!(validate_durable_replay(100, &candidate, "l3", 64, family).is_err());
    }
}
