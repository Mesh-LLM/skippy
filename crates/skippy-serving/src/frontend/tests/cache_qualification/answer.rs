//! Validate the same chat content that the production response exposes.
use super::{
    CandidateRunner, Family, Fixture, chat_template_options, evidence, generation_request,
    required_path, stage_config,
};
use crate::frontend::generation::{StageOpenAiBackend, chat_output_parser_required};
use anyhow::{Context, Result, ensure};
use serde_json::json;
use skippy_inference_api::generation::GenerationRequest;
use skippy_runtime::ChatTemplateOptions;

pub(super) fn content(
    backend: &StageOpenAiBackend,
    request: &GenerationRequest,
    options: &ChatTemplateOptions,
    metadata: Option<&str>,
    raw: &str,
) -> Result<String> {
    if !chat_output_parser_required(request, options) {
        return Ok(raw.to_owned());
    }
    Ok(
        match backend.parse_chat_output(raw, request, metadata, false)? {
            Some(parsed) => parsed
                .content
                .context("chat parser returned no answer content")?,
            None => raw.to_owned(),
        },
    )
}

#[test]
#[ignore = "requires the pinned dense model and native chat parser"]
fn schema_answer_uses_parsed_content_and_rejects_wrong_values() -> Result<()> {
    skippy_runtime::test_support::require_native_runtime();
    let fixture = Fixture::load(Family::DenseAttention)?;
    let model = required_path("SKIPPY_CACHE_QUAL_MODEL")?.canonicalize()?;
    let digest = fixture.verify_model(&model)?;
    let runner = CandidateRunner::new(stage_config(&fixture, &model, &digest)?, fixture.family)?;
    let request = generation_request(
        &fixture,
        &super::conversation::Case::DivergentTail.messages(),
    )?;
    let defaults = runner.backend.request_defaults.snapshot();
    let options = chat_template_options(&request, &defaults)?;
    ensure!(
        chat_output_parser_required(&request, &options),
        "control must exercise native chat parsing"
    );
    let prompt = runner
        .backend
        .prepare_chat_prompt(&request, options.clone())?;
    for raw in [
        "{\"code\":1618}",
        "```json\n{\n  \"code\": 1618\n}\n```",
        "```json\n{\"code\":1729}\n```",
    ] {
        let parsed = content(
            &runner.backend,
            &request,
            &options,
            prompt.chat_parse_metadata.as_deref(),
            raw,
        )?;
        let expected = if raw.contains("1729") { 1729 } else { 1618 };
        ensure!(
            serde_json::from_str::<serde_json::Value>(&parsed)? == json!({"code":expected}),
            "parser changed the answer"
        );
        let generation = evidence::Generation {
            prompt_sha256: "parser-control".into(),
            prompt_ids: vec![1],
            generated_ids: vec![2],
            content: parsed,
            raw_content: Some(raw.into()),
            top_logprobs: vec![],
        };
        ensure!(
            evidence::validate_answer(&generation, 1618).is_ok() == (expected == 1618),
            "known-answer check accepted the wrong value"
        );
    }
    Ok(())
}
