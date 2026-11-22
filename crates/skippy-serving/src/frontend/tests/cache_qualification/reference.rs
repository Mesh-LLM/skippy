use super::conversation::Case;
use super::evidence::{Generation, Reference, TokenScore};
use super::{
    BATCH_TOKENS, Backend, CONTEXT_TOKENS, Fixture, LLAMA_PIN, MAX_TOKENS, TIMEOUT, TOP_K,
    file_sha256, required_path, sha256, structured,
};
use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::BTreeMap, fs, net::TcpListener, path::Path, process::Stdio, time::Duration,
};
use tokio::{
    process::Command,
    time::{Instant, sleep, timeout},
};

// Large pinned fixtures may load from shared storage. Keep this separate from
// the request deadline so slow initialization does not weaken generation checks.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(600);

pub(super) async fn observe(
    backend: Backend,
    directory: &Path,
    model: &Path,
    fixture: &Fixture,
    messages: &Value,
) -> Result<Reference> {
    let binary = required_path("SKIPPY_CACHE_QUAL_LLAMA_SERVER")?.canonicalize()?;
    let binary_sha256 = file_sha256(&binary)?;
    let version = timeout(
        Duration::from_secs(15),
        Command::new(&binary)
            .arg("--version")
            .kill_on_drop(true)
            .output(),
    )
    .await
    .context("llama-server --version timed out")??;
    ensure!(version.status.success(), "llama-server --version failed");
    let version = format!(
        "{}{}",
        String::from_utf8_lossy(&version.stdout),
        String::from_utf8_lossy(&version.stderr)
    );
    ensure!(
        version_matches(&version, LLAMA_PIN.trim()),
        "llama-server does not identify the pinned upstream commit {}: {}",
        LLAMA_PIN.trim(),
        version.trim()
    );

    let socket = TcpListener::bind(("127.0.0.1", 0))?;
    let port = socket.local_addr()?.port();
    let base = format!("http://127.0.0.1:{port}");
    let log_path = directory.join("llama-server.log");
    let log = fs::File::create(&log_path)?;
    drop(socket);
    let mut child = Command::new(&binary)
        .arg("--model")
        .arg(model)
        .arg("--alias")
        .arg(fixture.model_id())
        .args(["--host", "127.0.0.1", "--port", &port.to_string()])
        .args(["--ctx-size", &CONTEXT_TOKENS.to_string()])
        .args([
            "--batch-size",
            &BATCH_TOKENS.to_string(),
            "--ubatch-size",
            &BATCH_TOKENS.to_string(),
        ])
        .args([
            "--n-gpu-layers",
            "all",
            "--parallel",
            "1",
            "--flash-attn",
            "off",
            "--fit",
            "off",
            "--kv-unified",
            "--cache-type-k",
            "f16",
            "--cache-type-v",
            "f16",
            "--no-context-shift",
            "--jinja",
            "--cache-ram",
            "0",
            "--ctx-checkpoints",
            "0",
            "--verbose",
        ])
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log))
        .kill_on_drop(true)
        .spawn()
        .context("start pinned llama-server")?;

    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(TIMEOUT)
        .build()?;
    let result = async {
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        loop {
            if let Some(status) = child.try_wait()? {
                anyhow::bail!("llama-server exited before readiness: {status}");
            }
            let response = client
                .get(format!("{base}/health"))
                .timeout(Duration::from_secs(2))
                .send()
                .await;
            if response.is_ok_and(|response| response.status().is_success()) {
                break;
            }
            ensure!(
                Instant::now() < deadline,
                "timeout waiting for llama-server readiness"
            );
            sleep(Duration::from_millis(200)).await;
        }
        let layers = backend.offloaded_layers(&fs::read_to_string(&log_path)?)?;
        let props: Props = client
            .get(format!("{base}/props"))
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        ensure!(
            !props.chat_template.is_empty(),
            "llama-server returned an empty chat template"
        );
        let generation = observe_generation(&client, &base, messages).await?;
        let mut conversations = BTreeMap::new();
        for case in Case::ALL {
            let generation = observe_generation(&client, &base, &case.messages())
                .await
                .with_context(|| format!("llama.cpp {case:?}"))?;
            conversations.insert(case, generation);
        }
        let mut structured = BTreeMap::new();
        for case in structured::Case::ALL {
            let mut request = case.request(&fixture.model_id());
            request["cache_prompt"] = json!(false);
            let result = post(&client, &base, "/v1/chat/completions", request).await;
            structured.insert(case, structured::Attempt::reference(result));
        }
        ensure!(
            !fs::read_to_string(&log_path)?.contains("created context checkpoint"),
            "llama-server created a context checkpoint during cold inference"
        );
        ensure!(
            child.try_wait()?.is_none(),
            "llama-server exited during inference"
        );
        ensure!(
            file_sha256(&binary)? == binary_sha256,
            "llama-server executable changed during inference"
        );
        Ok(Reference {
            binary_sha256,
            version,
            offloaded_layers: layers,
            template_sha256: sha256(props.chat_template.as_bytes()),
            generation,
            conversations,
            structured,
        })
    }
    .await;
    let shutdown = child
        .kill()
        .await
        .context("stop llama-server and reclaim device resources");
    result.and_then(|observation| {
        shutdown?;
        Ok(observation)
    })
}

pub(super) fn version_matches(version: &str, expected: &str) -> bool {
    version.lines().any(|line| {
        line.starts_with("version: ")
            && line
                .split_once("commit ")
                .and_then(|(_, value)| value.strip_suffix(')'))
                .is_some_and(|commit| commit.len() >= 7 && expected.starts_with(commit))
    })
}

async fn observe_generation(
    client: &reqwest::Client,
    base: &str,
    messages: &Value,
) -> Result<Generation> {
    let template: Rendered = post(
        client,
        base,
        "/apply-template",
        json!({
            "messages": messages,
            "chat_template_kwargs": {"enable_thinking": false, "preserve_reasoning": false}
        }),
    )
    .await?;
    let tokenized: Tokenized = post(
        client,
        base,
        "/tokenize",
        json!({
            "content": template.prompt, "add_special": true, "parse_special": true
        }),
    )
    .await?;
    let completion: Completion = post(
        client,
        base,
        "/completion",
        json!({
            "prompt": &tokenized.tokens, "n_predict": MAX_TOKENS,
            "temperature": -1, "seed": 1, "cache_prompt": false,
            "return_tokens": true, "n_probs": TOP_K, "post_sampling_probs": false
        }),
    )
    .await?;
    ensure!(
        !completion.truncated
            && completion.tokens_evaluated == tokenized.tokens.len()
            && completion.timings.cache_n == 0
            && completion.timings.prompt_n == tokenized.tokens.len(),
        "llama-server did not evaluate the entire input cold"
    );
    ensure!(
        completion
            .completion_probabilities
            .iter()
            .map(|row| row.id)
            .eq(completion.tokens.iter().copied()),
        "llama-server probability rows do not align with generated token IDs"
    );
    Ok(Generation {
        prompt_sha256: sha256(template.prompt.as_bytes()),
        prompt_ids: tokenized.tokens,
        generated_ids: completion.tokens,
        content: completion.content,
        top_logprobs: completion
            .completion_probabilities
            .into_iter()
            .map(|row| row.top_logprobs)
            .collect(),
    })
}

async fn post<T: serde::de::DeserializeOwned>(
    client: &reqwest::Client,
    base: &str,
    route: &str,
    body: Value,
) -> Result<T> {
    let response = client
        .post(format!("{base}{route}"))
        .json(&body)
        .send()
        .await?;
    let status = response.status();
    let raw = response.bytes().await?;
    ensure!(
        status.is_success(),
        "llama-server {route} returned {status}: {}",
        String::from_utf8_lossy(&raw)
    );
    serde_json::from_slice(&raw).with_context(|| format!("decode llama-server {route} response"))
}

#[derive(Deserialize)]
struct Props {
    chat_template: String,
}

#[derive(Deserialize)]
struct Rendered {
    prompt: String,
}

#[derive(Deserialize)]
struct Tokenized {
    tokens: Vec<i32>,
}

#[derive(Deserialize)]
struct Completion {
    content: String,
    tokens: Vec<i32>,
    tokens_evaluated: usize,
    timings: Timings,
    truncated: bool,
    completion_probabilities: Vec<Distribution>,
}

#[derive(Deserialize)]
struct Distribution {
    id: i32,
    top_logprobs: Vec<TokenScore>,
}

#[derive(Deserialize)]
struct Timings {
    cache_n: usize,
    prompt_n: usize,
}

#[test]
fn reference_version_requires_pinned_commit() {
    let pin = "8954f194837d2ec4d5f67f20c81b84abe63883ba";
    assert!(version_matches(
        "version: 1 (build 3, commit 8954f1948)\nbuilt with clang",
        pin
    ));
    assert!(!version_matches(
        "version: 1 (build 3, commit 895)\nbuilt with clang",
        pin
    ));
    assert!(!version_matches(
        "version: 1 (build 3, commit 1234567)\nbuilt with clang",
        pin
    ));
}
