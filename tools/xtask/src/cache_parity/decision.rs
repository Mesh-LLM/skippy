use super::{DynResult, contract::*};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

const MIN_PASSING_PAIRS: usize = 15;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Outcome {
    Pass,
    Inconclusive,
    Regression,
}

#[derive(Debug, Serialize)]
pub(super) struct Summary {
    schema_version: u32,
    plan_sha256: String,
    pub(super) outcome: Outcome,
    pub(super) cells: Vec<CellSummary>,
}

#[derive(Debug, Serialize)]
pub(super) struct CellSummary {
    cell: Cell,
    pub(super) outcome: Outcome,
    metrics: BTreeMap<&'static str, MetricSummary>,
}

#[derive(Debug, Serialize)]
struct MetricSummary {
    passing_pairs: usize,
    outcome: Outcome,
}

pub(super) fn verify(
    plan: &Plan,
    plan_sha: &str,
    evidence: &Evidence,
    mut check_raw: impl FnMut(&PlannedCell, usize, &Trial) -> DynResult<String>,
) -> DynResult<Summary> {
    if evidence.schema_version != VERSION || evidence.plan_sha256 != plan_sha {
        return Err(
            "parity evidence does not match the expected plan bytes or schema version".into(),
        );
    }
    let mut measured = BTreeMap::new();
    for row in &evidence.cells {
        if measured.insert(&row.cell, row).is_some() {
            return Err(format!("duplicate measured cell {:?}", row.cell).into());
        }
    }
    let mut cells = Vec::with_capacity(plan.cells.len());
    let mut artifacts = BTreeSet::new();
    for row in &plan.cells {
        let observed = measured
            .remove(&row.cell)
            .ok_or_else(|| format!("missing measured cell {:?}", row.cell))?;
        cells.push(
            summarize(
                row,
                observed,
                plan.backends.get(row.cell.backend),
                &mut artifacts,
                &mut check_raw,
            )
            .map_err(|error| format!("cell {:?}: {error}", row.cell))?,
        );
    }
    if let Some(unexpected) = measured.first_key_value() {
        return Err(format!("unexpected measured cell {:?}", unexpected.0).into());
    }
    Ok(Summary {
        schema_version: VERSION,
        plan_sha256: plan_sha.to_owned(),
        outcome: cells
            .iter()
            .map(|cell| cell.outcome)
            .max()
            .ok_or("empty parity plan")?,
        cells,
    })
}

fn summarize<'a>(
    expected: &PlannedCell,
    observed: &'a MeasuredCell,
    host: &Host,
    artifacts: &mut BTreeSet<&'a str>,
    check_raw: &mut impl FnMut(&PlannedCell, usize, &Trial) -> DynResult<String>,
) -> DynResult<CellSummary> {
    if observed.model_sha256 != expected.model_sha256
        || observed.template_sha256 != expected.template_sha256
        || observed.sidecars_sha256 != expected.sidecars_sha256
        || observed.workload_sha256 != expected.workload_sha256
        || observed.pairs.len() != PAIRS
    {
        return Err("unexpected fixture, workload, or pair count".into());
    }
    let mut passing_pairs = BTreeMap::new();
    for (index, pair) in observed.pairs.iter().enumerate() {
        let candidate_index = index % 2;
        let candidate = &pair[candidate_index];
        let baseline = &pair[1 - candidate_index];
        for (trial, engine, binary, config) in [
            (
                candidate,
                Engine::Candidate,
                &host.candidate_binary_sha256,
                &expected.candidate_config_sha256,
            ),
            (
                baseline,
                Engine::Baseline,
                &host.baseline_binary_sha256,
                &expected.baseline_config_sha256,
            ),
        ] {
            if trial.engine != engine
                || &trial.binary_sha256 != binary
                || &trial.config_sha256 != config
                || trial.host_profile_sha256 != host.host_profile_sha256
                || !is_digest(&trial.raw_artifact_sha256, 64)
                || !artifacts.insert(&trial.raw_artifact_sha256)
                || !trial.valid()
            {
                return Err(format!(
                    "invalid {engine:?} identity, ordering, artifact, or measurements in pair {}",
                    index + 1
                )
                .into());
            }
        }
        if check_raw(expected, index + 1, candidate)? != check_raw(expected, index + 1, baseline)? {
            return Err(format!("pair {} has different request sequences", index + 1).into());
        }
        if baseline.metrics.agentx_score == 0.0 {
            return Err(format!("zero baseline AgentX score in pair {}", index + 1).into());
        }
        for (metric, pass) in compare(candidate, baseline) {
            *passing_pairs.entry(metric).or_insert(0) += usize::from(pass);
        }
    }
    let metrics: BTreeMap<_, _> = passing_pairs
        .into_iter()
        .map(|(metric, passing_pairs)| {
            let outcome = metric_outcome(metric, passing_pairs);
            (
                metric,
                MetricSummary {
                    passing_pairs,
                    outcome,
                },
            )
        })
        .collect();
    Ok(CellSummary {
        cell: expected.cell.clone(),
        outcome: metrics
            .values()
            .map(|metric| metric.outcome)
            .max()
            .ok_or("no metrics")?,
        metrics,
    })
}

impl Trial {
    fn valid(&self) -> bool {
        self.requests == REQUESTS
            && self.failed_requests <= REQUESTS / 2
            && self.incorrect_outputs <= self.requests - self.failed_requests
            && self.requests_with_expected_cache_path == self.requests - self.failed_requests
            && self.metrics.valid()
    }
}

impl Metrics {
    fn valid(&self) -> bool {
        self.agentx_score.is_finite()
            && self.agentx_score >= 0.0
            && self.peak_host_bytes > 0
            && self.peak_device_bytes > 0
            && [
                self.prefill_tokens_per_second,
                self.decode_tokens_per_second,
            ]
            .into_iter()
            .all(positive)
            && self.inference_ttft_ms.valid()
            && self.end_to_end_ttft_ms.valid()
            && self.inference_ttft_ms.p50 <= self.end_to_end_ttft_ms.p50
            && self.inference_ttft_ms.p90 <= self.end_to_end_ttft_ms.p90
            && self.inference_ttft_ms.p99 <= self.end_to_end_ttft_ms.p99
    }
}

impl Percentiles {
    fn valid(&self) -> bool {
        positive(self.p50)
            && positive(self.p90)
            && positive(self.p99)
            && self.p50 <= self.p90
            && self.p90 <= self.p99
    }
}

fn positive(value: f64) -> bool {
    value.is_finite() && value > 0.0
}

fn compare(candidate: &Trial, baseline: &Trial) -> [(&'static str, bool); 13] {
    let c = &candidate.metrics;
    let b = &baseline.metrics;
    [
        ("agentx_score", c.agentx_score >= b.agentx_score * 0.98),
        (
            "inference_ttft_p50",
            c.inference_ttft_ms.p50 <= b.inference_ttft_ms.p50 * 1.02,
        ),
        (
            "inference_ttft_p90",
            c.inference_ttft_ms.p90 <= b.inference_ttft_ms.p90 * 1.02,
        ),
        (
            "inference_ttft_p99",
            c.inference_ttft_ms.p99 <= b.inference_ttft_ms.p99 * 1.02,
        ),
        (
            "end_to_end_ttft_p50",
            c.end_to_end_ttft_ms.p50 <= b.end_to_end_ttft_ms.p50 * 1.02,
        ),
        (
            "end_to_end_ttft_p90",
            c.end_to_end_ttft_ms.p90 <= b.end_to_end_ttft_ms.p90 * 1.02,
        ),
        (
            "end_to_end_ttft_p99",
            c.end_to_end_ttft_ms.p99 <= b.end_to_end_ttft_ms.p99 * 1.02,
        ),
        (
            "prefill_throughput",
            c.prefill_tokens_per_second >= b.prefill_tokens_per_second * 0.98,
        ),
        (
            "decode_throughput",
            c.decode_tokens_per_second >= b.decode_tokens_per_second * 0.98,
        ),
        (
            "peak_host_memory",
            memory_within_limit(c.peak_host_bytes, b.peak_host_bytes),
        ),
        (
            "peak_device_memory",
            memory_within_limit(c.peak_device_bytes, b.peak_device_bytes),
        ),
        (
            "failed_requests",
            candidate.failed_requests <= baseline.failed_requests,
        ),
        (
            "incorrect_outputs",
            candidate.incorrect_outputs <= baseline.incorrect_outputs,
        ),
    ]
}

fn memory_within_limit(candidate: u64, baseline: u64) -> bool {
    u128::from(candidate) * 100 <= u128::from(baseline) * 102
}

fn metric_outcome(metric: &str, passing: usize) -> Outcome {
    match (metric, passing) {
        ("failed_requests" | "incorrect_outputs", PAIRS) => Outcome::Pass,
        ("failed_requests" | "incorrect_outputs", _) => Outcome::Regression,
        (_, MIN_PASSING_PAIRS..) => Outcome::Pass,
        (_, 0..=5) => Outcome::Regression,
        _ => Outcome::Inconclusive,
    }
}
