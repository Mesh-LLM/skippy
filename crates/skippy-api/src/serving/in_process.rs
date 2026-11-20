//! Owning in-process entry points for production dynamic serving.

use std::{future::poll_fn, path::PathBuf, sync::Arc};

use anyhow::{Context, Result, ensure};
use skippy_serving::EmbeddedRuntimeOptions;

use super::{
    InferenceOptions, LoadedModelBackend, LocalDiskCacheOptions, ModelLoadObservations,
    ModelLoadRequest, ServingTelemetryOptions,
};
use crate::{SingleStageOptions, native_runtime::ProductionRuntime, single_stage_config, source};

use skippy_inference_api::generation::GenerationEventStream;
pub use skippy_inference_api::{
    InferenceHookPolicy, InferenceRequestContext, InferenceResult, ModelObject,
    generation::{
        GenerationEvent, GenerationMessage, GenerationRequest, MessageContent, MessageContentPart,
    },
    thinking::ThinkingControls,
};

/// Policy and explicit source for one full local model.
pub struct LocalModelOptions {
    run_id: String,
    model: SingleStageOptions,
    default_max_tokens: u32,
    observations: ModelLoadObservations,
    telemetry: ServingTelemetryOptions,
    hook_policy: Option<Arc<dyn InferenceHookPolicy>>,
    disk_cache: Option<LocalDiskCacheOptions>,
}

impl LocalModelOptions {
    pub fn new(
        run_id: impl Into<String>,
        model_id: impl Into<String>,
        model_path: impl Into<PathBuf>,
    ) -> Self {
        let mut model = SingleStageOptions::new(model_id, model_path);
        model.native_mtp_enabled = false;
        Self {
            run_id: run_id.into(),
            model,
            default_max_tokens: 256,
            observations: ModelLoadObservations::Disabled,
            telemetry: ServingTelemetryOptions::off(),
            hook_policy: None,
            disk_cache: None,
        }
    }

    #[must_use]
    pub fn with_limits(
        mut self,
        context_tokens: u32,
        concurrency: usize,
        output_tokens: u32,
    ) -> Self {
        self.model.ctx_size = context_tokens;
        self.model.generation_concurrency = concurrency;
        self.default_max_tokens = output_tokens;
        self
    }

    #[must_use]
    pub fn with_gpu_layers(mut self, layers: i32) -> Self {
        self.model.n_gpu_layers = layers;
        self
    }

    #[must_use]
    pub fn with_observations(mut self, observations: ModelLoadObservations) -> Self {
        self.observations = observations;
        self
    }

    #[must_use]
    pub fn with_telemetry(mut self, telemetry: ServingTelemetryOptions) -> Self {
        self.telemetry = telemetry;
        self
    }

    #[must_use]
    pub fn with_hook_policy(mut self, policy: Arc<dyn InferenceHookPolicy>) -> Self {
        self.hook_policy = Some(policy);
        self
    }

    /// Choose an explicit disk budget. An unavailable cache falls back to cold prefill.
    #[must_use]
    pub fn with_disk_cache(
        mut self,
        directory: impl Into<PathBuf>,
        capacity_bytes: std::num::NonZeroU64,
        minimum_free_bytes: u64,
    ) -> Self {
        self.disk_cache = Some(LocalDiskCacheOptions {
            directory: directory.into(),
            budget: skippy_cache::disk_policy::DiskCacheBudget::Fixed(capacity_bytes.get()),
            minimum_free_bytes,
        });
        self
    }

    fn validate(&self) -> Result<()> {
        self.model.validate()?;
        ensure!(!self.run_id.trim().is_empty(), "a run ID is required");
        ensure!(
            !self.model.model_id.trim().is_empty(),
            "a model ID is required"
        );
        ensure!(
            self.model
                .model_path
                .to_str()
                .is_some_and(|path| !path.is_empty()),
            "a UTF-8 model path is required"
        );
        ensure!(
            u32::try_from(self.model.generation_concurrency).is_ok(),
            "generation concurrency exceeds the maximum lane count"
        );
        ensure!(
            (1..=self.model.ctx_size).contains(&self.default_max_tokens),
            "default output tokens must fit the context"
        );
        ensure!(
            self.model.n_gpu_layers >= -1,
            "GPU layers must be -1 or nonnegative"
        );
        Ok(())
    }

    /// Verify the local source and synchronously load an in-process backend.
    /// Call on a blocking executor when invoked by an async host.
    pub fn load(self, runtime: &ProductionRuntime) -> Result<LoadedModel> {
        self.validate()?;
        let identity = source::synthetic_direct_gguf_package(
            &self.model.model_id,
            &self.model.model_path,
            None,
        )
        .context("verify local model source")?;
        let config = single_stage_config(&self.model, identity.into(), self.run_id)?;
        let openai = InferenceOptions::direct_single_stage_defaults(
            self.model.model_id,
            self.default_max_tokens,
            self.model.generation_concurrency,
            false,
        );
        let mut load = ModelLoadRequest::new(runtime_defaults(config, self.telemetry), openai);
        load.hook_policy = self.hook_policy;
        LoadedModel::load_prepared(
            runtime,
            load.with_disk_cache(self.disk_cache),
            self.observations,
        )
    }
}

fn runtime_defaults(
    config: skippy_protocol::StageConfig,
    telemetry: ServingTelemetryOptions,
) -> EmbeddedRuntimeOptions {
    EmbeddedRuntimeOptions {
        config,
        topology: None,
        n_threads: None,
        n_threads_batch: None,
        mtp_source: skippy_runtime::MtpSource::Disabled,
        recurrent_rollback_tokens: 0,
        metrics_otlp_grpc: telemetry.metrics_otlp_grpc,
        telemetry_queue_capacity: telemetry.queue_capacity,
        telemetry_level: telemetry.level,
        session_lifecycle_observer: None,
    }
}

/// Owns the model, generation backend and any stage-zero return listener.
pub struct LoadedModel(LoadedModelBackend);

impl LoadedModel {
    /// Open a specialist entry stage with caller-supplied admitted configuration and hooks.
    /// Build its configuration with `crate::stage_load::admitted_stage_config`.
    pub fn load_prepared(
        _runtime: &ProductionRuntime,
        request: ModelLoadRequest,
        observations: ModelLoadObservations,
    ) -> Result<Self> {
        Ok(Self(request.load_observed(observations)?))
    }

    pub async fn models(&self) -> InferenceResult<Vec<ModelObject>> {
        self.0.backend.models().await
    }

    pub fn thinking(&self) -> Option<&ThinkingControls> {
        self.0.thinking.as_ref()
    }

    /// Dropping the admission future or its returned stream signals cancellation.
    pub async fn stream(
        &self,
        mut request: GenerationRequest,
        context: InferenceRequestContext,
    ) -> InferenceResult<ModelStream<'_>> {
        let cancellation = CancelOnDrop(Some(context.clone()));
        request.stream = true;
        let events = self
            .0
            .backend
            .chat_completion_stream(request, context)
            .await?;
        Ok(ModelStream {
            events,
            cancellation,
            _model: self,
        })
    }

    /// Release model ownership. The borrow on each stream requires the host to
    /// finish or cancel its request streams before consuming this handle.
    pub fn shutdown(self) {
        let LoadedModelBackend {
            backend,
            runtime,
            prediction_return_listener,
            ..
        } = self.0;
        drop(backend);
        drop(runtime);
        drop(prediction_return_listener);
    }
}

/// An in-process generation stream. Consume it to EOF to drain a request.
pub struct ModelStream<'model> {
    cancellation: CancelOnDrop,
    events: GenerationEventStream,
    _model: &'model LoadedModel,
}

impl ModelStream<'_> {
    pub async fn next_event(&mut self) -> Option<InferenceResult<GenerationEvent>> {
        self.cancellation.0.as_ref()?;
        let next = poll_fn(|cx| self.events.as_mut().poll_next(cx)).await;
        if next.is_none() {
            self.cancellation.0 = None;
        }
        next
    }

    pub fn cancel(&self) {
        if let Some(context) = &self.cancellation.0 {
            context.cancel();
        }
    }

    /// Signal cancellation and wait for the stream producer to close.
    pub async fn cancel_and_drain(mut self) {
        self.cancel();
        while self.next_event().await.is_some() {}
    }
}

struct CancelOnDrop(Option<InferenceRequestContext>);

impl Drop for CancelOnDrop {
    fn drop(&mut self) {
        if let Some(context) = &self.0 {
            context.cancel();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::LocalModelOptions;

    #[test]
    fn invalid_limits_and_missing_ids_fail_before_model_io() {
        assert!(
            LocalModelOptions::new("run", "model", "missing.gguf")
                .validate()
                .is_ok()
        );
        assert!(
            LocalModelOptions::new("", "model", "missing.gguf")
                .validate()
                .is_err()
        );
        assert!(
            LocalModelOptions::new("run", " ", "missing.gguf")
                .validate()
                .is_err()
        );
        assert!(
            LocalModelOptions::new("run", "model", "")
                .validate()
                .is_err()
        );
        assert!(
            LocalModelOptions::new("run", "model", "missing.gguf")
                .with_limits(128, 1, 256)
                .validate()
                .is_err()
        );
        assert!(
            LocalModelOptions::new("run", "model", "missing.gguf")
                .with_limits(128, 0, 32)
                .validate()
                .is_err()
        );
    }
}
