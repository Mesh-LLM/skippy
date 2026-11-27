use crate::frontend::generation::StageOpenAiBackend;
use crate::frontend::iteration_scheduler::iteration_work::{IterationWork, PromptStart};
use skippy_inference_api::{CancellationToken, InferenceError, InferenceResult};
use skippy_runtime::SamplingConfig;
use std::time::Instant;

const MAX_NATIVE_ITERATION_TOKENS: usize = 2_048;

fn suffix_chunk_sample_flags(
    suffix_len: usize,
    chunk_tokens: usize,
    sample_last: bool,
) -> Vec<bool> {
    let chunk_count = suffix_len.div_ceil(chunk_tokens);
    (0..chunk_count)
        .map(|index| sample_last && index == chunk_count.saturating_sub(1))
        .collect()
}

/// One prompt's cooperative prefill: what all of its segments and chunks
/// share. Begun once per prompt, so a checkpoint boundary cannot send the rest
/// of the prompt behind prompts that began later.
pub(super) struct PromptPrefill<'a> {
    session_id: &'a str,
    start: PromptStart,
    deadline: Instant,
    cancellation: Option<&'a CancellationToken>,
}

impl<'a> PromptPrefill<'a> {
    pub(super) fn begin(
        session_id: &'a str,
        deadline: Instant,
        cancellation: Option<&'a CancellationToken>,
    ) -> Self {
        Self {
            session_id,
            start: PromptStart::next(),
            deadline,
            cancellation,
        }
    }

    fn ensure_active(&self) -> InferenceResult<()> {
        if self
            .cancellation
            .is_some_and(CancellationToken::is_cancelled)
        {
            return Err(InferenceError::cancelled(
                "request cancelled during deferred suffix prefill",
            ));
        }
        if Instant::now() >= self.deadline {
            return Err(InferenceError::timeout(
                "cache operation deadline exceeded during deferred suffix prefill",
            ));
        }
        Ok(())
    }
}

pub(in crate::frontend) struct SuffixPrefillOutcome {
    pub(in crate::frontend) predicted: Option<i32>,
    pub(in crate::frontend) chunk_count: usize,
    pub(in crate::frontend) max_batch_size: usize,
    pub(in crate::frontend) runtime_lock_wait_ms: f64,
    pub(in crate::frontend) runtime_lock_hold_ms: f64,
}

impl StageOpenAiBackend {
    /// Submit deferred suffix chunks as first-class native iterations.
    /// Concurrent requests can therefore share a mixed batch instead of each
    /// cache-aware restore closure monopolizing the runtime through its whole
    /// suffix. Sampling is deliberately restricted to the final chunk. Every
    /// chunk carries the prompt's start, so the batch policy keeps the
    /// prompt's place between chunks.
    pub(super) fn prefill_suffix(
        &self,
        prompt: &PromptPrefill<'_>,
        suffix: &[i32],
        sampling: Option<&SamplingConfig>,
        sample_last: bool,
    ) -> InferenceResult<SuffixPrefillOutcome> {
        if suffix.is_empty() {
            return Err(InferenceError::backend(
                "deferred suffix prefill requires at least one token",
            ));
        }
        let chunk_tokens = usize::try_from(self.config.n_ubatch.unwrap_or(256))
            .unwrap_or(MAX_NATIVE_ITERATION_TOKENS)
            .clamp(1, MAX_NATIVE_ITERATION_TOKENS);
        let sample_flags = suffix_chunk_sample_flags(suffix.len(), chunk_tokens, sample_last);
        let channel = self.iteration_scheduler.direct_iteration_channel();
        let mut predicted = None;
        let mut chunk_count = 0usize;
        let mut max_batch_size = 0usize;
        let mut runtime_lock_wait_ms = 0.0;
        let mut runtime_lock_hold_ms = 0.0;
        for (chunk, should_sample) in suffix.chunks(chunk_tokens).zip(sample_flags) {
            prompt.ensure_active()?;
            let outcome = self.iteration_scheduler.execute_iteration_on(
                &channel,
                prompt.session_id,
                chunk,
                &[],
                sampling,
                should_sample,
                IterationWork::Prompt(prompt.start),
                Some(prompt.deadline),
                prompt.cancellation,
            )?;
            chunk_count = chunk_count.saturating_add(1);
            max_batch_size = max_batch_size.max(outcome.batch_size);
            runtime_lock_wait_ms += outcome.runtime_lock_wait_ms;
            runtime_lock_hold_ms += outcome.runtime_lock_hold_ms;
            if should_sample {
                predicted = Some(outcome.predicted);
            }
        }
        Ok(SuffixPrefillOutcome {
            predicted,
            chunk_count,
            max_batch_size,
            runtime_lock_wait_ms,
            runtime_lock_hold_ms,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::suffix_chunk_sample_flags;

    #[test]
    fn deferred_suffix_samples_only_the_final_chunk() {
        assert_eq!(
            suffix_chunk_sample_flags(7, 3, true),
            vec![false, false, true]
        );
        assert_eq!(
            suffix_chunk_sample_flags(7, 3, false),
            vec![false, false, false]
        );
    }

    #[test]
    fn resident_suffix_keeps_final_chunk_sampling() {
        assert_eq!(suffix_chunk_sample_flags(3, 3, true), vec![true]);
        assert_eq!(suffix_chunk_sample_flags(4, 3, true), vec![false, true]);
    }
}
