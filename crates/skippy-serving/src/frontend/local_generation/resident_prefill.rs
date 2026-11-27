use crate::frontend::generation::StageOpenAiBackend;
use crate::frontend::iteration_scheduler::DirectSession;
use crate::frontend::iteration_scheduler::iteration_work::{IterationWork, PromptStart};
use skippy_inference_api::{CancellationToken, InferenceError, InferenceResult};
use skippy_protocol::{BatchConfig, PrefillChunk};
use skippy_runtime::SamplingConfig;
use std::num::NonZeroUsize;
use std::time::Instant;

const MAX_NATIVE_ITERATION_TOKENS: usize = 2_048;

/// How many of a prompt's tokens each step of its prefill takes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum PrefillChunking {
    /// The same count every step.
    Fixed(NonZeroUsize),
    /// A whole step while no other request is generating tokens. While some
    /// are, `shared` tokens less one for each of them, whose next tokens join
    /// the step: the prompt then holds them up by one shared step at a time.
    Adaptive {
        alone: NonZeroUsize,
        shared: NonZeroUsize,
    },
}

impl PrefillChunking {
    /// `step_tokens` is the most one direct iteration may carry.
    fn resolve(batch: &BatchConfig, n_ubatch: Option<u32>, step_tokens: usize) -> Self {
        let microbatch = usize::try_from(n_ubatch.unwrap_or(256))
            .unwrap_or(MAX_NATIVE_ITERATION_TOKENS)
            .clamp(1, MAX_NATIVE_ITERATION_TOKENS);
        let step = step_tokens.clamp(1, MAX_NATIVE_ITERATION_TOKENS);
        let tokens = |count: usize| NonZeroUsize::new(count).unwrap_or(NonZeroUsize::MIN);
        match batch.prefill_chunk.unwrap_or(PrefillChunk::Microbatch) {
            PrefillChunk::Microbatch => Self::Fixed(tokens(microbatch)),
            PrefillChunk::Adaptive => Self::Adaptive {
                alone: tokens(step),
                shared: tokens(
                    batch
                        .prefill_shared_tokens
                        .and_then(|shared| usize::try_from(shared.get()).ok())
                        .unwrap_or(microbatch)
                        .min(step),
                ),
            },
        }
    }

    /// The tokens the next step takes of the `remaining` ones while
    /// `generating` other sessions generate tokens. At least one while any
    /// remain.
    fn next(self, remaining: usize, generating: usize) -> usize {
        let tokens = match self {
            Self::Fixed(tokens) => tokens.get(),
            Self::Adaptive { alone, .. } if generating == 0 => alone.get(),
            Self::Adaptive { shared, .. } => shared.get().saturating_sub(generating).max(1),
        };
        tokens.min(remaining)
    }
}

/// One prompt's cooperative prefill: what all of its segments and chunks
/// share. Begun once per prompt, so a checkpoint boundary cannot send the rest
/// of the prompt behind prompts that began later.
pub(super) struct PromptPrefill<'a> {
    session_id: &'a str,
    start: PromptStart,
    deadline: Instant,
    cancellation: Option<&'a CancellationToken>,
    /// Counts the prompt among the sessions the scheduler waits for, across
    /// its checkpoint boundaries too.
    _prefilling: DirectSession,
}

impl<'a> PromptPrefill<'a> {
    pub(super) fn begin(
        session_id: &'a str,
        deadline: Instant,
        cancellation: Option<&'a CancellationToken>,
        prefilling: DirectSession,
    ) -> Self {
        Self {
            session_id,
            start: PromptStart::next(),
            deadline,
            cancellation,
            _prefilling: prefilling,
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
    /// prompt's place between chunks. Each chunk is sized when it is sent, from
    /// the sessions generating tokens then.
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
        let chunking = PrefillChunking::resolve(
            &self.config.batch,
            self.config.n_ubatch,
            self.iteration_scheduler.max_iteration_tokens(),
        );
        let channel = self.iteration_scheduler.direct_iteration_channel();
        let mut predicted = None;
        let mut chunk_count = 0usize;
        let mut max_batch_size = 0usize;
        let mut runtime_lock_wait_ms = 0.0;
        let mut runtime_lock_hold_ms = 0.0;
        let mut rest = suffix;
        while !rest.is_empty() {
            prompt.ensure_active()?;
            let generating = self.iteration_scheduler.generating_sessions();
            let (chunk, tail) = rest.split_at(chunking.next(rest.len(), generating));
            let should_sample = sample_last && tail.is_empty();
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
            rest = tail;
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
    use super::PrefillChunking;
    use skippy_protocol::{BatchConfig, PrefillChunk};
    use std::num::NonZeroU32;

    fn adaptive(shared: Option<u32>) -> BatchConfig {
        BatchConfig {
            prefill_chunk: Some(PrefillChunk::Adaptive),
            prefill_shared_tokens: shared.and_then(NonZeroU32::new),
            ..BatchConfig::default()
        }
    }

    #[test]
    fn microbatch_chunks_do_not_follow_load() {
        let chunking = PrefillChunking::resolve(&BatchConfig::default(), Some(512), 2048);
        assert_eq!(chunking.next(4620, 0), 512);
        assert_eq!(chunking.next(4620, 3), 512);
        assert_eq!(chunking.next(7, 0), 7);
    }

    #[test]
    fn an_adaptive_prompt_takes_a_whole_step_alone_and_leaves_room_for_generated_tokens() {
        let chunking = PrefillChunking::resolve(&adaptive(None), Some(512), 2048);
        assert_eq!(chunking.next(4620, 0), 2048);
        assert_eq!(chunking.next(4620, 3), 509);
        assert_eq!(chunking.next(100, 3), 100);
        assert_eq!(chunking.next(4620, 600), 1);
    }

    #[test]
    fn the_shared_budget_never_exceeds_one_step() {
        let chunking = PrefillChunking::resolve(&adaptive(Some(4096)), Some(512), 2048);
        assert_eq!(chunking.next(4620, 1), 2047);
        // A stage that exports activations steps one microbatch at a time.
        let chunking = PrefillChunking::resolve(&adaptive(Some(4096)), Some(512), 512);
        assert_eq!(chunking.next(4620, 0), 512);
        assert_eq!(chunking.next(4620, 1), 511);
    }
}
