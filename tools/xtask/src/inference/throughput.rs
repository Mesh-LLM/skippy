//! Descriptive C1 rates from the first response of each scored smoke case.
//! Repeats are correctness checks, not additional performance samples.
use super::*;
use serde_json::Value;

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Rates {
    pub source_sha: String,
    pub configuration: String,
    pub job_id: String,
    pub run_id: String,
    pub requests: usize,
    pub prefill_tokens: u64,
    pub prefill_ms: f64,
    pub decode_tokens: u64,
    pub decode_ms: f64,
}

impl Rates {
    pub fn display(&self) -> (String, String) {
        (
            rate(self.prefill_tokens, self.prefill_ms),
            rate(self.decode_tokens, self.decode_ms),
        )
    }
}

fn rate(tokens: u64, milliseconds: f64) -> String {
    let value = tokens as f64 * 1000.0 / milliseconds;
    if tokens > 0 && milliseconds.is_finite() && milliseconds > 0.0 && value.is_finite() {
        format!("{value:.1}")
    } else {
        "Unavailable".into()
    }
}

fn successful_run(job: &Value) -> DynResult<&str> {
    let run = job["runs"]
        .as_array()
        .and_then(|runs| runs.last())
        .filter(|run| run["status"] == "succeeded")
        .and_then(|run| run["id"].as_str())
        .ok_or("missing successful native attempt")?;
    if !run.starts_with("run_") || !run.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') {
        return Err("invalid native attempt ID".into());
    }
    Ok(run)
}

pub(super) fn collect(job: &Value, row: &Row, sha: &str, output: &Path) -> DynResult<()> {
    let run = successful_run(job)?;
    let qualification = super::dispatch::api(
        "GET",
        &format!("/v1/artifacts/{run}/raw/qualification.json"),
        None,
    )?;
    let quality = super::dispatch::api(
        "GET",
        &format!("/v1/artifacts/{run}/raw/quality.json"),
        None,
    )?;
    let rates = summarize(job, row, sha, &qualification, &quality)?;
    write_json_file(&output.join(format!("{}-throughput.json", row.id)), &rates)
}

fn summarize(
    job: &Value,
    row: &Row,
    sha: &str,
    qualification: &Value,
    quality: &Value,
) -> DynResult<Rates> {
    let key = row
        .id
        .strip_suffix(&format!("-{}", row.mode))
        .ok_or("invalid configuration")?;
    if job["repo_rev"] != sha
        || job["status"] != "succeeded"
        || qualification["source_sha"] != sha
        || qualification["model"] != key
        || qualification["mode"] != row.mode
        || qualification["profile"] != "pr"
        || qualification["status"] != "passed"
    {
        return Err("throughput evidence identity mismatch".into());
    }
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("../../../../ci/inference-quality.json"))?;
    let samples = quality
        .as_array()
        .filter(|samples| samples.len() == cases.len())
        .ok_or("incomplete scored throughput evidence")?;
    let mut rates = Rates {
        source_sha: sha.into(),
        configuration: row.id.clone(),
        job_id: job["id"].as_str().ok_or("missing job ID")?.into(),
        run_id: successful_run(job)?.into(),
        requests: samples.len(),
        prefill_tokens: 0,
        prefill_ms: 0.0,
        decode_tokens: 0,
        decode_ms: 0.0,
    };
    let mut prefill_complete = true;
    let mut decode_complete = true;
    for (sample, case) in samples.iter().zip(cases) {
        if sample["passed"] != true || sample["case"] != case {
            return Err("unscored or mismatched throughput sample".into());
        }
        let response = &sample["response"];
        let timings = &response["timings"];
        let prompt = timings["prompt_n"].as_u64();
        let cached = response["usage"]["prompt_tokens_details"]["cached_tokens"].as_u64();
        let processed = prompt.zip(cached).and_then(|(p, c)| p.checked_sub(c));
        prefill_complete &= accumulate(
            processed,
            timings["prompt_ms"].as_f64(),
            &mut rates.prefill_tokens,
            &mut rates.prefill_ms,
        );
        decode_complete &= accumulate(
            timings["predicted_n"].as_u64(),
            timings["predicted_ms"].as_f64(),
            &mut rates.decode_tokens,
            &mut rates.decode_ms,
        );
    }
    // Do not publish a partial aggregate when any sample lacks usable timings.
    if !prefill_complete {
        rates.prefill_tokens = 0;
        rates.prefill_ms = 0.0;
    }
    if !decode_complete {
        rates.decode_tokens = 0;
        rates.decode_ms = 0.0;
    }
    Ok(rates)
}

fn accumulate(
    tokens: Option<u64>,
    ms: Option<f64>,
    total_tokens: &mut u64,
    total_ms: &mut f64,
) -> bool {
    let Some((tokens, ms)) = tokens
        .zip(ms)
        .filter(|(n, ms)| *n > 0 && ms.is_finite() && *ms > 0.0)
    else {
        return false;
    };
    let Some(sum) = total_tokens.checked_add(tokens) else {
        return false;
    };
    if !(*total_ms + ms).is_finite() {
        return false;
    }
    *total_tokens = sum;
    *total_ms += ms;
    true
}

pub(super) fn read(directory: &Path, row: &Row, sha: &str) -> DynResult<(String, String)> {
    let path = directory.join(format!("{}-throughput.json", row.id));
    if !path.is_file() {
        return Ok(("Unavailable".into(), "Unavailable".into()));
    }
    let rates: Rates = serde_json::from_slice(&std::fs::read(path)?)?;
    let job: Value = serde_json::from_slice(&std::fs::read(
        directory.join(format!("{}-job.json", row.id)),
    )?)?;
    if rates.source_sha != sha
        || rates.configuration != row.id
        || job["repo_rev"] != sha
        || job["status"] != "succeeded"
        || job["id"] != rates.job_id
        || successful_run(&job)? != rates.run_id
        || rates.requests != 3
    {
        return Err("comment throughput evidence identity mismatch".into());
    }
    Ok(rates.display())
}

#[cfg(test)]
#[path = "throughput_tests.rs"]
mod tests;
