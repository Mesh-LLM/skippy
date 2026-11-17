use super::*;
use crate::execution;
use serde_json::{Value, json};

pub fn run(model: &str, output: &Path) -> DynResult<()> {
    let data = tempfile::NamedTempFile::new()?;
    write_json_file(
        data.path(),
        &json!({"model":model,"messages":[{"role":"user","content":"What is 17 + 25? Reply with only the integer."}],"stream":true,"temperature":0,"seed":42,"max_tokens":16,"chat_template_kwargs":{"enable_thinking":false}}),
    )?;
    let response = execution::capture_for(
        "curl",
        &[
            "-fsS".into(),
            "--max-time".into(),
            "60".into(),
            "-H".into(),
            "Content-Type: application/json".into(),
            "--data-binary".into(),
            format!("@{}", data.path().display()),
            "http://127.0.0.1:9337/v1/chat/completions".into(),
        ],
        &[],
        65,
    )?;
    std::fs::write(output.join("streaming.sse"), &response)?;
    validate(&response)?;
    write_json_file(
        &output.join("smoke.json"),
        &json!({"status":"passed","streaming":true,"expected":"42","context":4096,"max_output_tokens":16,"request_timeout_s":60}),
    )
}

fn validate(response: &str) -> DynResult<()> {
    let mut text = String::new();
    let mut finished = false;
    let mut done = false;
    for line in response
        .lines()
        .filter_map(|line| line.strip_prefix("data: "))
    {
        if line == "[DONE]" {
            done = true;
            continue;
        }
        let chunk: Value = serde_json::from_str(line)?;
        if let Some(value) = chunk["choices"][0]["delta"]["content"].as_str() {
            text.push_str(value);
        }
        finished |= chunk["choices"][0]["finish_reason"] == "stop";
        if !chunk["error"].is_null() {
            return Err("stream returned an error".into());
        }
    }
    if !done || !finished || text.trim() != "42" {
        return Err("incomplete or incorrect streaming response".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn streaming_requires_the_answer_and_both_completion_markers() {
        let valid = "data: {\"choices\":[{\"delta\":{\"content\":\"42\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n";
        assert!(validate(valid).is_ok());
        assert!(validate(&valid.replace("42", "41")).is_err());
        assert!(validate(&valid.replace("data: [DONE]", "")).is_err());
        assert!(validate(&valid.replace("\"stop\"", "null")).is_err());
        assert!(validate("data: {\"error\":\"failed\"}\ndata: [DONE]\n").is_err());
    }
}
