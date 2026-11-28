//! Sample the final prompt row without changing native prefill chunk boundaries.

use super::*;

impl StageOpenAiBackend {
    pub(super) fn prefill_sampled_chunks(
        &self,
        request: &LocalGeneration<'_>,
        session_id: &str,
        cache_stats: &mut GenerationCacheStats,
        chunk_tokens: std::num::NonZeroUsize,
    ) -> InferenceResult<Option<i32>> {
        let prefill_timer = PhaseTimer::start();
        let _prefill = self.enter_openai_phase(&prefill_timer);
        let runtime_sessions_before = self
            .iteration_scheduler
            .execute_runtime("feature-prefill-stats-before", |runtime| {
                Ok(runtime.session_stats())
            })?;
        let channel = self.iteration_scheduler.direct_iteration_channel();
        let chunks = request.prompt_token_ids.chunks(chunk_tokens.get());
        let chunk_count = chunks.len();
        let mut prompt_prefill_sample = None;
        let mut lock_wait_ms = 0.0;
        let mut lock_hold_ms = 0.0;
        for (index, tokens) in chunks.enumerate() {
            let sample_last = index + 1 == chunk_count;
            let outcome = self.iteration_scheduler.execute_iteration_on(
                &channel,
                session_id,
                tokens,
                &[],
                request.sampling.enabled.then_some(request.sampling),
                sample_last,
                skippy_runtime::IterationBatchPhase::Prefill,
                None,
                request.cancellation,
            )?;
            lock_wait_ms += outcome.runtime_lock_wait_ms;
            lock_hold_ms += outcome.runtime_lock_hold_ms;
            if sample_last {
                prompt_prefill_sample = Some(outcome.predicted);
            }
        }
        cache_stats.suffix_prefill_tokens = saturating_u32(request.prompt_token_ids.len());
        let runtime_sessions_after = self
            .iteration_scheduler
            .execute_runtime("feature-prefill-stats-after", |runtime| {
                Ok(runtime.session_stats())
            })?;
        let mut attrs = self.openai_attrs(request.ids);
        attrs.insert(
            "llama_stage.prefill_token_count".to_string(),
            json!(request.prompt_token_ids.len()),
        );
        attrs.insert(
            "llama_stage.prefill_chunk_count".to_string(),
            json!(chunk_count),
        );
        attrs.insert("skippy.kv.restored_prefill".to_string(), json!(false));
        attrs.insert("skippy.kv.restored_prefill_tokens".to_string(), json!(0));
        attrs.insert(
            "skippy.kv.prefill_suffix_tokens".to_string(),
            json!(request.prompt_token_ids.len()),
        );
        attrs.insert("skippy.kv.recorded_pages".to_string(), json!(0));
        attrs.insert(
            "llama_stage.runtime_lock_wait_ms".to_string(),
            json!(lock_wait_ms),
        );
        attrs.insert(
            "llama_stage.runtime_lock_hold_ms".to_string(),
            json!(lock_hold_ms),
        );
        attrs.insert(
            "llama_stage.runtime_lock_acquires".to_string(),
            json!(chunk_count),
        );
        Self::insert_runtime_session_stats(
            &mut attrs,
            "llama_stage.runtime_sessions_before",
            &runtime_sessions_before,
        );
        Self::insert_runtime_session_stats(
            &mut attrs,
            "llama_stage.runtime_sessions_after",
            &runtime_sessions_after,
        );
        cache_stats.prompt_ms = prefill_timer.elapsed_ms();
        self.emit_openai_phase("stage.openai_prefill", prefill_timer, attrs);
        Ok(prompt_prefill_sample)
    }
}
