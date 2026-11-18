use super::{
    CacheState, DynResult, Metrics, Percentiles, REQUESTS, RawTrial, RequestResult, is_digest,
};
use sha2::{Digest, Sha256};

pub(in super::super) struct ObservedTrial {
    pub(in super::super) metrics: Metrics,
    pub(in super::super) failed_requests: u32,
    pub(in super::super) incorrect_outputs: u32,
    pub(in super::super) request_sequence_sha256: String,
}

impl RawTrial {
    pub(super) fn measure(&self) -> DynResult<ObservedTrial> {
        if self.duration_ns == 0 || self.requests.len() != REQUESTS as usize {
            return Err(
                "raw trial must record a positive duration and the full request count".into(),
            );
        }
        let mut sequence = Sha256::new();
        let mut inference_ttft = Vec::with_capacity(self.requests.len());
        let mut end_to_end_ttft = Vec::with_capacity(self.requests.len());
        let mut failed_requests = 0;
        let mut incorrect_outputs = 0;
        let (mut new_prompt, mut total_prompt, mut output) = (0_u64, 0_u64, 0_u64);
        let mut decoded = 0_u64;
        let (mut prefill_duration, mut decode_duration) = (0_u128, 0_u128);
        for (index, request) in self.requests.iter().enumerate() {
            if !is_digest(&request.request_sha256, 64) || request.prompt_tokens == 0 {
                return Err(format!(
                    "request {} has an invalid input digest or token length",
                    index + 1
                )
                .into());
            }
            sequence.update(request.request_sha256.as_bytes());
            sequence.update(request.prompt_tokens.to_be_bytes());
            let RequestResult::Completed {
                observed_cache,
                cached_tokens,
                output_tokens,
                output_correct,
                inference_ttft_ns,
                end_to_end_ttft_ns,
                prefill_ns,
                decode_ns,
            } = request.result
            else {
                failed_requests += 1;
                continue;
            };
            let correct_cache = observed_cache == self.cell.cache
                && cached_tokens <= request.prompt_tokens
                && (cached_tokens == 0) == (observed_cache == CacheState::ColdPrefill);
            let complete_timing = inference_ttft_ns > 0
                && inference_ttft_ns <= end_to_end_ttft_ns
                && end_to_end_ttft_ns <= self.duration_ns
                && prefill_ns <= inference_ttft_ns
                && decode_ns <= self.duration_ns - end_to_end_ttft_ns
                && (output_tokens > 1) == (decode_ns > 0)
                && (cached_tokens < request.prompt_tokens) == (prefill_ns > 0);
            if !correct_cache || !complete_timing || output_tokens == 0 {
                return Err(format!(
                    "request {} has inconsistent cache, token or timing observations",
                    index + 1
                )
                .into());
            }
            incorrect_outputs += u32::from(!output_correct);
            total_prompt += u64::from(request.prompt_tokens);
            new_prompt += u64::from(request.prompt_tokens - cached_tokens);
            output += u64::from(output_tokens);
            decoded += u64::from(output_tokens - 1);
            prefill_duration += u128::from(prefill_ns);
            decode_duration += u128::from(decode_ns);
            inference_ttft.push(inference_ttft_ns);
            end_to_end_ttft.push(end_to_end_ttft_ns);
        }
        if failed_requests > REQUESTS / 2 || new_prompt == 0 || decoded == 0 {
            return Err(
                "raw trial has insufficient completed requests or measured prefill or decode work"
                    .into(),
            );
        }
        let (peak_host_bytes, peak_device_bytes) = self.memory_peaks()?;
        Ok(ObservedTrial {
            metrics: Metrics {
                agentx_score: rate(total_prompt + output, u128::from(self.duration_ns)),
                inference_ttft_ms: percentiles(&mut inference_ttft),
                end_to_end_ttft_ms: percentiles(&mut end_to_end_ttft),
                prefill_tokens_per_second: rate(new_prompt, prefill_duration),
                decode_tokens_per_second: rate(decoded, decode_duration),
                peak_host_bytes,
                peak_device_bytes,
            },
            failed_requests,
            incorrect_outputs,
            request_sequence_sha256: hex::encode(sequence.finalize()),
        })
    }

    fn memory_peaks(&self) -> DynResult<(u64, u64)> {
        if self.memory.len() < 2
            || self
                .memory
                .first()
                .is_none_or(|sample| sample.offset_ns != 0)
            || self
                .memory
                .last()
                .is_none_or(|sample| sample.offset_ns != self.duration_ns)
            || self
                .memory
                .windows(2)
                .any(|pair| pair[0].offset_ns >= pair[1].offset_ns)
        {
            return Err(
                "raw trial memory samples must be ordered and span the measured interval".into(),
            );
        }
        let mut host = 0;
        let mut device = 0;
        for sample in &self.memory {
            host = host.max(sample.host_bytes);
            device = device.max(sample.device_bytes);
        }
        if host == 0 || device == 0 {
            return Err("raw trial is missing observed host or device memory use".into());
        }
        Ok((host, device))
    }
}

fn percentiles(samples: &mut [u64]) -> Percentiles {
    samples.sort_unstable();
    let milliseconds =
        |p| (samples[usize::div_ceil(p * samples.len(), 100) - 1] as f64) / 1_000_000.0;
    Percentiles {
        p50: milliseconds(50),
        p90: milliseconds(90),
        p99: milliseconds(99),
    }
}

fn rate(tokens: u64, elapsed_ns: u128) -> f64 {
    tokens as f64 * 1_000_000_000.0 / elapsed_ns as f64
}
