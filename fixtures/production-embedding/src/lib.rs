use std::{
    env,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};

use anyhow::{Context, Result, ensure};
use skippy_api::{
    native_runtime::{ProductionRuntime, native_source_recipe},
    serving::{
        ModelLoadObservation, ModelLoadObservations, ModelLoadRecordKind, TerminalObservation,
        in_process::{
            GenerationMessage, GenerationRequest, InferenceRequestContext, LoadedModel,
            MessageContent,
        },
    },
};

pub const MODEL_ID: &str = "embedding-fixture";
const SOURCE_REVISION: &str = env!("SKIPPY_FIXTURE_REVISION");

pub fn required(name: &str) -> Result<String> {
    env::var(name).with_context(|| format!("supply {name}"))
}

pub fn open_runtime() -> Result<(ProductionRuntime, PathBuf)> {
    let bundle = PathBuf::from(required("SKIPPY_FIXTURE_BUNDLE")?);
    let model = PathBuf::from(required("SKIPPY_FIXTURE_MODEL")?);
    let manifest_sha = required("SKIPPY_FIXTURE_MANIFEST_SHA256")?;
    ensure!(model.is_file(), "supplied model fixture is missing");
    ensure!(
        ProductionRuntime::open_bundle(&bundle, &"0".repeat(64)).is_err(),
        "an incompatible runtime pin was accepted"
    );
    let runtime = ProductionRuntime::open_bundle(&bundle, &manifest_sha)?;
    let identity = runtime.identity();
    let (_, patch_queue) = native_source_recipe();
    println!(
        "skippy_revision={SOURCE_REVISION} runtime_id={} release={} abi={} manifest_sha256={} llama_upstream={} llama_patched={} runtime_patch_digest={} source_patch_queue_sha256={patch_queue}",
        identity.runtime_id,
        identity.release,
        identity.abi,
        identity.manifest_sha256,
        identity.llama_upstream_sha,
        identity.llama_patched_sha,
        identity.llama_patch_digest,
    );
    Ok((runtime, model))
}

#[derive(Clone, Default)]
pub struct LoadEvidence(Arc<Mutex<Vec<ModelLoadObservation>>>);

impl LoadEvidence {
    pub fn observe(&self) -> ModelLoadObservations {
        let events = self.0.clone();
        ModelLoadObservations::Observe(Arc::new(move |observation| {
            if let ModelLoadObservation::Native(record) = &observation {
                match record.kind {
                    ModelLoadRecordKind::Progress {
                        current,
                        total,
                        unit,
                    } => {
                        println!(
                            "load progress {} {current}/{total} {unit:?}",
                            record.sequence
                        );
                    }
                    ModelLoadRecordKind::Finished | ModelLoadRecordKind::HandledFailure(_) => {
                        println!("load terminal {:?} {}", record.kind, record.detail);
                    }
                    _ => {}
                }
            }
            events.lock().unwrap().push(observation);
        }))
    }

    pub fn verify(&self) -> Result<()> {
        let events = self.0.lock().unwrap();
        ensure!(
            events
                .iter()
                .any(|event| matches!(event, ModelLoadObservation::Native(record)
                if matches!(record.kind, ModelLoadRecordKind::Progress { .. }))),
            "the supplied runtime emitted no typed progress"
        );
        let Some(ModelLoadObservation::Summary(summary)) = events.last() else {
            anyhow::bail!("typed load summary missing");
        };
        ensure!(
            summary.runtime_opened && summary.terminal == TerminalObservation::Consistent,
            "runtime terminal diagnostics were incomplete: {summary:?}"
        );
        Ok(())
    }
}

pub async fn exercise_streams(model: &LoadedModel) -> Result<()> {
    ensure!(!model.models().await?.is_empty(), "no loaded model listed");
    tokio::time::timeout(Duration::from_secs(120), async {
        cancel_after_output(model).await?;
        complete_stream(model).await
    })
    .await
    .context("timed out exercising generation streams")?
}

fn request(tokens: u32) -> GenerationRequest {
    GenerationRequest {
        model: MODEL_ID.into(),
        messages: vec![GenerationMessage {
            role: "user".into(),
            content: Some(MessageContent::Text(
                "List ten colors, one color per line.".into(),
            )),
            extra: Default::default(),
        }],
        max_tokens: Some(tokens),
        temperature: Some(0.0),
        ..Default::default()
    }
}

fn contains_text(event: &skippy_api::serving::in_process::GenerationEvent) -> bool {
    event.choices.iter().any(|choice| {
        [
            choice.delta.content.as_deref(),
            choice.delta.reasoning_content.as_deref(),
        ]
        .into_iter()
        .flatten()
        .any(|text| !text.is_empty())
    })
}

async fn cancel_after_output(model: &LoadedModel) -> Result<()> {
    let context = InferenceRequestContext::new();
    let mut stream = model.stream(request(128), context.clone()).await?;
    while let Some(event) = stream.next_event().await {
        let event = event?;
        if contains_text(&event)
            && event
                .choices
                .iter()
                .all(|choice| choice.finish_reason.is_none())
        {
            stream.cancel_and_drain().await;
            ensure!(context.is_cancelled(), "cancellation was not propagated");
            return Ok(());
        }
    }
    anyhow::bail!("stream emitted no cancellable output")
}

async fn complete_stream(model: &LoadedModel) -> Result<()> {
    let context = InferenceRequestContext::new();
    let mut stream = model.stream(request(24), context.clone()).await?;
    let (mut text, mut terminal) = (false, false);
    while let Some(event) = stream.next_event().await {
        let event = event?;
        text |= contains_text(&event);
        terminal |= event
            .choices
            .iter()
            .any(|choice| choice.finish_reason.is_some());
    }
    ensure!(
        text && terminal,
        "expected streamed text and a finish reason"
    );
    ensure!(!context.is_cancelled(), "successful request was cancelled");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn runtime_manifest_pin_fails_closed_offline() {
        let bundle = tempfile::tempdir().unwrap();
        std::fs::write(bundle.path().join("manifest.json"), b"{}").unwrap();
        let error = ProductionRuntime::open_bundle(bundle.path(), &"0".repeat(64)).unwrap_err();
        assert!(error.to_string().contains("manifest SHA-256 mismatch"));
        assert_eq!(SOURCE_REVISION.len(), 40);
    }
}
