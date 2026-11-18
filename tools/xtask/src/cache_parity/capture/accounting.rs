use super::{DynResult, Engine};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum StopType {
    Eos,
    Word,
    Limit,
}

#[derive(Deserialize)]
pub(super) struct NativeTermination {
    pub(super) stop_type: StopType,
    tokens_predicted: u32,
}

pub(super) fn completion_tokens(
    engine: Option<Engine>,
    reported: u32,
    finish: &str,
    termination: Option<&NativeTermination>,
) -> DynResult<Option<u32>> {
    match engine {
        None => Ok(None),
        Some(Engine::Candidate) => Ok(Some(reported)),
        Some(Engine::Baseline) => baseline_tokens(reported, finish, termination),
    }
}

fn baseline_tokens(
    reported: u32,
    finish: &str,
    termination: Option<&NativeTermination>,
) -> DynResult<Option<u32>> {
    let Some(termination) = termination else {
        // The pinned server reports `length` at a limit. Other finish reasons
        // need the native stop type to distinguish EOG from a stop word.
        return Ok((finish == "length").then_some(reported));
    };
    if reported != termination.tokens_predicted {
        return Err("llama-server native and OpenAI completion counts differ".into());
    }
    match (termination.stop_type, finish) {
        (StopType::Eos, "stop" | "tool_calls") => reported
            .checked_sub(1)
            .map(Some)
            .ok_or_else(|| "llama-server EOS requires a sampled token".into()),
        (StopType::Word, "stop" | "tool_calls") | (StopType::Limit, "length") => Ok(Some(reported)),
        _ => Err("llama-server native and OpenAI stop reasons disagree".into()),
    }
}
