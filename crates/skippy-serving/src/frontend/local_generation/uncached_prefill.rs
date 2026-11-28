//! Use the same chunk and sampling boundaries for cold and cache-aware prefill.
use super::cache_deadline::cache_operation_deadline;
use crate::frontend::generation::{
    GenerationCacheStats, LocalGeneration, PhaseTimer, StageOpenAiBackend,
};
use crate::frontend::util::saturating_u32;
use serde_json::json;
use skippy_inference_api::InferenceResult;

impl StageOpenAiBackend {
    pub(super) fn prefill_uncached_prompt(
        &self,
        request: &LocalGeneration<'_>,
        session_id: &str,
        cache_stats: &mut GenerationCacheStats,
    ) -> InferenceResult<Option<i32>> {
        let prefill_timer = PhaseTimer::start();
        let _prefill = self.enter_openai_phase(&prefill_timer);
        let runtime_sessions_before = self
            .iteration_scheduler
            .execute_runtime("feature-prefill-stats-before", |runtime| {
                Ok(runtime.session_stats())
            })?;
        let outcome = self.prefill_suffix(
            session_id,
            request.prompt_token_ids,
            request.sampling.enabled.then_some(request.sampling),
            true,
            cache_operation_deadline(
                self.generation_admission_timeout,
                request.prompt_token_ids.len(),
            ),
            request.cancellation,
        )?;
        let prompt_prefill_sample = outcome.predicted;
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
            json!(outcome.chunk_count),
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
            json!(outcome.runtime_lock_wait_ms),
        );
        attrs.insert(
            "llama_stage.runtime_lock_hold_ms".to_string(),
            json!(outcome.runtime_lock_hold_ms),
        );
        attrs.insert(
            "llama_stage.runtime_lock_acquires".to_string(),
            json!(outcome.chunk_count),
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
