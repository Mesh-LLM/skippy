//! Check parsed OpenAI output and cache accounting against explicit answers.

use super::{CandidateRunner, Family, Fixture, evidence::Status, tiers};
use crate::kv_integration::KvStageIntegration;
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use skippy_inference_api::{
    InferenceBackend, InferenceRequestContext,
    chat::{ChatCompletionRequest, ChatCompletionResponse},
    generation::GenerationRequest,
};
use std::{collections::BTreeMap, fmt::Write as _};

const CODE: u32 = 1729;
const TOOL_NAME: &str = "record_code";
const MIN_LONG_PROMPT_TOKENS: u64 = 4096;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Case {
    JsonSchema,
    NamedTool,
    LongRetrieval,
}

impl Case {
    pub(super) const ALL: [Self; 3] = [Self::JsonSchema, Self::NamedTool, Self::LongRetrieval];

    pub(super) fn request(self, model: &str) -> Value {
        let mut schema = json!({
            "type": "object", "properties": {"code": {"type": "integer"}},
            "required": ["code"], "additionalProperties": false,
        });
        let message = match self {
            Self::JsonSchema => {
                super::user_content("Return a JSON object with the integer code 1729.")
            }
            Self::NamedTool => super::user_content("Call record_code with the integer code 1729."),
            Self::LongRetrieval => retrieval_prompt(),
        };
        if self == Self::JsonSchema {
            schema["properties"]["code"]["const"] = json!(CODE);
        }
        let mut request = json!({
            "model": model, "messages": [{"role": "user", "content": message}],
            "temperature": 0, "seed": 1, "max_tokens": 128, "reasoning_effort": "none",
        });
        if self == Self::NamedTool {
            request["tools"] = json!([{
                "type": "function",
                "function": {"name": TOOL_NAME, "description": "Record the provided code.", "parameters": schema},
            }]);
            request["tool_choice"] = json!({"type":"function", "function":{"name":TOOL_NAME}});
        } else {
            request["response_format"] = json!({
                "type": "json_schema", "json_schema": {"name": "lookup", "strict": true, "schema": schema},
            });
        }
        request
    }

    fn minimum_prompt_tokens(self) -> u64 {
        if self == Self::LongRetrieval {
            MIN_LONG_PROMPT_TOKENS
        } else {
            64
        }
    }
}

fn retrieval_prompt() -> String {
    let mut prompt = String::from("The access code for Harbor is 1729.\n");
    for row in 0..240 {
        writeln!(
            prompt,
            "Archive record {row:03}: miscellaneous harbor maintenance notes. Check the beacon housing, safety rails, water level, and west pier signs."
        )
        .expect("appending to a string is infallible");
    }
    prompt.push_str("Retrieve the Harbor access code from the first line. Put that code in the JSON field code as an integer.");
    prompt
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case", tag = "outcome")]
pub(super) enum Attempt {
    Captured {
        response: Value,
        prompt_digest: Option<String>,
        hit_kind: Option<String>,
        restore_source: Option<String>,
        payload_kind: Option<String>,
    },
    Error {
        error: String,
    },
}

impl Attempt {
    pub(super) fn reference(result: Result<Value>) -> Self {
        match result {
            Ok(response) => Self::Captured {
                response,
                prompt_digest: None,
                hit_kind: None,
                restore_source: None,
                payload_kind: None,
            },
            Err(error) => Self::Error {
                error: format!("{error:#}"),
            },
        }
    }
}

pub(super) struct Prepared {
    case: Case,
    request: GenerationRequest,
    context: InferenceRequestContext,
    cold: Attempt,
}

#[derive(Deserialize, Serialize)]
pub(super) struct Observation {
    cold: Attempt,
    seed: Attempt,
    warm: Attempt,
}

#[derive(Serialize)]
pub(super) struct ResultRow {
    pub(super) status: Status,
    pub(super) errors: Vec<String>,
}

pub(super) async fn observe_cold(
    fixture: &Fixture,
    runner: &CandidateRunner,
) -> Result<Vec<Prepared>> {
    let mut prepared = Vec::new();
    for case in Case::ALL {
        let request: GenerationRequest =
            serde_json::from_value::<ChatCompletionRequest>(case.request(&fixture.model_id()))?
                .into();
        let context = tiers::isolated_context();
        let cold = observe(runner, &request, &context).await;
        prepared.push(Prepared {
            case,
            request,
            context,
            cold,
        });
    }
    Ok(prepared)
}

pub(super) async fn observe_warm(
    prepared: Vec<Prepared>,
    runner: &CandidateRunner,
    cache: &KvStageIntegration,
    observations: &mut BTreeMap<Case, Observation>,
) -> Result<()> {
    for Prepared {
        case,
        request,
        context,
        cold,
    } in prepared
    {
        tiers::drain_workers(cache)?;
        let seed = observe(runner, &request, &context).await;
        tiers::drain_workers(cache)?;
        let warm = observe(runner, &request, &context).await;
        observations.insert(case, Observation { cold, seed, warm });
    }
    Ok(())
}

async fn observe(
    runner: &CandidateRunner,
    request: &GenerationRequest,
    context: &InferenceRequestContext,
) -> Attempt {
    let result = runner
        .backend
        .chat_completion_with_context(request.clone(), context.clone())
        .await;
    // The cache-disabled scheduler can finish after its session is dropped.
    // The start observation captures the actual prompt for either driver.
    let start = runner.starts.try_recv();
    let _ = runner.receipts.try_recv();
    let mut cache = super::evidence::CacheObservation::default();
    let restore = runner.observe_restore(&mut cache);
    let observation = (|| -> Result<Attempt> {
        let response = ChatCompletionResponse::from(result?);
        let start = start.context("missing native start for structured output")?;
        ensure!(
            start.prompt_token_ids.len() == response.usage.prompt_tokens as usize,
            "structured response usage disagrees with the native prompt"
        );
        let mut digest = Sha256::new();
        for token in start.prompt_token_ids.iter() {
            digest.update(token.to_le_bytes());
        }
        restore?;
        Ok(Attempt::Captured {
            response: serde_json::to_value(response)?,
            prompt_digest: Some(super::hex_digest(&digest.finalize())),
            hit_kind: cache.hit_kind,
            restore_source: cache.restore_source,
            payload_kind: cache.payload_kind,
        })
    })();
    observation.unwrap_or_else(|error| Attempt::Error {
        error: format!("{error:#}"),
    })
}

pub(super) fn validate(
    family: Family,
    reference: &BTreeMap<Case, Attempt>,
    observed: &BTreeMap<Case, Observation>,
) -> BTreeMap<Case, ResultRow> {
    Case::ALL
        .into_iter()
        .map(|case| {
            let result = (|| -> Result<Vec<String>> {
                let reference = reference
                    .get(&case)
                    .context("missing llama.cpp structured response")?;
                let observed = observed
                    .get(&case)
                    .context("missing Skippy structured responses")?;
                Ok(validate_attempts(case, family, reference, observed))
            })();
            let errors = result.unwrap_or_else(|error| vec![format!("{error:#}")]);
            let status = if errors.is_empty() {
                Status::Pass
            } else {
                Status::Fail
            };
            (case, ResultRow { status, errors })
        })
        .collect()
}

fn validate_attempts(
    case: Case,
    family: Family,
    reference: &Attempt,
    observed: &Observation,
) -> Vec<String> {
    [
        ("llama.cpp cold", reference, false, false),
        ("Skippy cold", &observed.cold, true, false),
        ("Skippy seed", &observed.seed, true, false),
        ("Skippy warm", &observed.warm, true, true),
    ]
    .into_iter()
    .filter_map(|(label, attempt, candidate, warm)| {
        validate_attempt(
            case,
            family,
            attempt,
            candidate.then_some(&observed.cold),
            warm,
        )
        .err()
        .map(|error| format!("{label} {case:?}: {error:#}"))
    })
    .collect()
}

fn validate_attempt(
    case: Case,
    family: Family,
    attempt: &Attempt,
    baseline: Option<&Attempt>,
    warm: bool,
) -> Result<()> {
    let (response, prompt_digest, hit_kind, restore_source, payload_kind) = match attempt {
        Attempt::Captured {
            response,
            prompt_digest,
            hit_kind,
            restore_source,
            payload_kind,
        } => (
            response,
            prompt_digest,
            hit_kind,
            restore_source,
            payload_kind,
        ),
        Attempt::Error { error } => anyhow::bail!("{error}"),
    };
    validate_answer(case, response)?;
    let prompt = response["usage"]["prompt_tokens"]
        .as_u64()
        .context("missing prompt-token usage")?;
    let cached = response["usage"]["prompt_tokens_details"]["cached_tokens"]
        .as_u64()
        .context("missing cache-token usage")?;
    ensure!(
        prompt >= case.minimum_prompt_tokens(),
        "prompt contains only {prompt} tokens"
    );
    if let Some(cold) = baseline {
        let Attempt::Captured {
            prompt_digest: Some(expected),
            ..
        } = cold
        else {
            anyhow::bail!("missing cold Skippy prompt token digest");
        };
        ensure!(
            prompt_digest.as_ref() == Some(expected),
            "Skippy prompt token digest changed"
        );
    }
    if !warm {
        ensure!(cached == 0, "cold response reused {cached} tokens");
        return Ok(());
    }
    let prefill = u64::from(family.prefill_tokens(u32::try_from(prompt)?));
    ensure!(
        cached >= 64 && cached <= prefill,
        "expected L1 reuse, observed {cached} cached tokens"
    );
    ensure!(
        response["timings"]["suffix_prefill_n"].as_u64() == Some(prompt - cached),
        "suffix prefill does not match cached-token accounting"
    );
    let recurrent = family.payload() == crate::kv_integration::StagePrefixCachePayload::KvRecurrent;
    if recurrent {
        ensure!(
            hit_kind.as_deref() == Some("resident_prefix")
                || (restore_source.as_deref() == Some("radix")
                    && payload_kind.as_deref() == Some("kv-recurrent")),
            "expected complete L1 KV and recurrent state"
        );
    } else {
        ensure!(
            restore_source.is_none() && cached == prefill,
            "expected full resident L1 prefix"
        );
    }
    Ok(())
}

fn validate_answer(case: Case, response: &Value) -> Result<()> {
    let choices = response["choices"]
        .as_array()
        .context("missing chat choices")?;
    ensure!(choices.len() == 1, "expected one chat choice");
    let choice = &choices[0];
    let message = &choice["message"];
    let answer = if case == Case::NamedTool {
        ensure!(
            choice["finish_reason"] == "tool_calls",
            "named tool call did not finish"
        );
        let calls = message["tool_calls"]
            .as_array()
            .context("missing parsed tool calls")?;
        ensure!(
            calls.len() == 1
                && calls[0]["type"] == "function"
                && calls[0]["function"]["name"] == TOOL_NAME,
            "expected one call to {TOOL_NAME}"
        );
        calls[0]["function"]["arguments"]
            .as_str()
            .context("missing tool arguments")?
    } else {
        ensure!(
            choice["finish_reason"] == "stop",
            "JSON response did not finish"
        );
        message["content"]
            .as_str()
            .context("missing JSON response content")?
    };
    ensure!(
        serde_json::from_str::<Value>(answer)? == json!({"code": CODE}),
        "wrong structured answer {answer:?}"
    );
    Ok(())
}

#[test]
fn structured_contract_rejects_wrong_answers_and_incomplete_tool_calls() {
    let json_response =
        |content: &str| json!({"choices":[{"finish_reason":"stop","message":{"content":content}}]});
    assert!(validate_answer(Case::LongRetrieval, &json_response(r#"{"code": 1729}"#)).is_ok());
    assert!(validate_answer(Case::LongRetrieval, &json_response(r#"{"code": 42}"#)).is_err());
    let mut tool = json!({"choices":[{"finish_reason":"tool_calls","message":{"tool_calls":[
        {"type":"function","function":{"name":"record_code","arguments":"{\"code\":1729}"}}
    ]}}]});
    assert!(validate_answer(Case::NamedTool, &tool).is_ok());
    tool["choices"][0]["finish_reason"] = json!("length");
    assert!(validate_answer(Case::NamedTool, &tool).is_err());
}
