use super::phases::{NativeSamples, SampledPhases};
use super::{
    DynResult, Engine,
    accounting::{self, NativeTermination, StopType},
};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Default)]
pub(super) struct Capture {
    tail: Vec<u8>,
    data: Vec<u8>,
    event: String,
    done: bool,
    first_token_ns: Option<u64>,
    last_token_ns: u64,
    content: String,
    finish_reason: Option<String>,
    native_termination: Option<NativeTermination>,
    usage: Option<Usage>,
    first_server_timings: Option<Value>,
    final_server_timings: Option<Value>,
    native_samples: NativeSamples,
}

#[derive(Serialize)]
pub(super) struct Observation {
    first_token_ns: u64,
    last_token_ns: u64,
    pub(super) content: String,
    finish_reason: String,
    prompt_tokens: u32,
    completion_tokens: u32,
    normalized_completion_tokens: Option<u32>,
    native_stop_type: Option<StopType>,
    cached_tokens: Option<u32>,
    first_server_timings: Option<Value>,
    final_server_timings: Option<Value>,
    sampled_phases: Option<SampledPhases>,
}

#[derive(Deserialize)]
struct Usage {
    prompt_tokens: u32,
    completion_tokens: u32,
    total_tokens: u32,
    prompt_tokens_details: Option<PromptTokens>,
}

#[derive(Deserialize)]
struct PromptTokens {
    cached_tokens: Option<u32>,
}

impl Capture {
    pub(super) fn push(&mut self, bytes: &[u8], elapsed_ns: u64) -> DynResult<()> {
        let mut tail = std::mem::take(&mut self.tail);
        tail.extend_from_slice(bytes);
        let mut lines = tail.split_inclusive(|byte| *byte == b'\n');
        for line in lines.by_ref() {
            let Some(line) = line.strip_suffix(b"\n") else {
                self.tail.extend_from_slice(line);
                break;
            };
            self.line(line.strip_suffix(b"\r").unwrap_or(line), elapsed_ns)?;
        }
        Ok(())
    }

    fn line(&mut self, line: &[u8], elapsed_ns: u64) -> DynResult<()> {
        if line.is_empty() {
            let data = std::mem::take(&mut self.data);
            let event = std::mem::take(&mut self.event);
            if event == "error" {
                return Err("chat stream sent an error event".into());
            }
            if !data.is_empty() {
                self.event_data(&data, elapsed_ns)?;
            }
        } else if let Some(data) = line.strip_prefix(b"data:") {
            if !self.data.is_empty() {
                self.data.push(b'\n');
            }
            self.data
                .extend_from_slice(data.strip_prefix(b" ").unwrap_or(data));
        } else if let Some(event) = line.strip_prefix(b"event:") {
            self.event = std::str::from_utf8(event)?.trim().into();
        }
        Ok(())
    }

    fn event_data(&mut self, data: &[u8], elapsed_ns: u64) -> DynResult<()> {
        if self.done {
            return Err("chat stream sent data after [DONE]".into());
        }
        if data == b"[DONE]" {
            self.done = true;
            return Ok(());
        }
        let chunk: Value = serde_json::from_slice(data)?;
        if !chunk["error"].is_null() {
            return Err("chat stream sent a structured error".into());
        }
        if !chunk["usage"].is_null()
            && self
                .usage
                .replace(serde_json::from_value(chunk["usage"].clone())?)
                .is_some()
        {
            return Err("chat stream sent repeated usage".into());
        }
        let choice = &chunk["choices"][0];
        let delta = &choice["delta"];
        let content = delta["content"].as_str().unwrap_or_default();
        self.content.push_str(content);
        if !content.is_empty()
            || delta["reasoning_content"]
                .as_str()
                .is_some_and(|value| !value.is_empty())
            || delta["tool_calls"]
                .as_array()
                .is_some_and(|calls| !calls.is_empty())
        {
            self.first_token_ns.get_or_insert(elapsed_ns);
            self.last_token_ns = elapsed_ns;
        }
        if let Some(reason) = choice["finish_reason"].as_str() {
            if self.finish_reason.replace(reason.into()).is_some() {
                return Err("chat stream sent repeated finish reasons".into());
            }
            if let Some(verbose) = chunk
                .get("__verbose")
                .filter(|verbose| verbose.get("stop_type").is_some())
            {
                self.native_termination = Some(serde_json::from_value(verbose.clone())?);
            }
        }
        if let Some(timings) = chunk.get("timings").filter(|timings| timings.is_object()) {
            self.native_samples.record(timings);
            self.first_server_timings
                .get_or_insert_with(|| timings.clone());
            self.final_server_timings = Some(timings.clone());
        }
        Ok(())
    }

    pub(super) fn finish(self, engine: Option<Engine>) -> DynResult<Observation> {
        if !self.done || !self.tail.is_empty() || !self.data.is_empty() {
            return Err("incomplete chat stream".into());
        }
        let usage = self.usage.ok_or("chat stream omitted token usage")?;
        let cached_tokens = usage
            .prompt_tokens_details
            .and_then(|details| details.cached_tokens);
        if usage.prompt_tokens == 0
            || usage.completion_tokens == 0
            || usage.prompt_tokens.checked_add(usage.completion_tokens) != Some(usage.total_tokens)
            || cached_tokens.is_some_and(|cached| cached > usage.prompt_tokens)
        {
            return Err("chat stream sent inconsistent token usage".into());
        }
        let finish_reason = self
            .finish_reason
            .ok_or("chat stream omitted finish reason")?;
        let normalized_completion_tokens = accounting::completion_tokens(
            engine,
            usage.completion_tokens,
            &finish_reason,
            self.native_termination.as_ref(),
        )?;
        let sampled_phases = self.native_samples.phases(
            engine,
            normalized_completion_tokens,
            self.final_server_timings.as_ref(),
        );
        Ok(Observation {
            first_token_ns: self
                .first_token_ns
                .ok_or("chat stream omitted generated deltas")?,
            last_token_ns: self.last_token_ns,
            content: self.content,
            finish_reason,
            prompt_tokens: usage.prompt_tokens,
            completion_tokens: usage.completion_tokens,
            normalized_completion_tokens,
            native_stop_type: self
                .native_termination
                .map(|termination| termination.stop_type),
            cached_tokens,
            first_server_timings: self.first_server_timings,
            final_server_timings: self.final_server_timings,
            sampled_phases,
        })
    }
}

#[cfg(test)]
mod tests;
