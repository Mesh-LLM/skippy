use super::{StageConfig, StageKvCacheMode, support::local_openai_backend};
use crate::{
    frontend::{
        generation::{
            GenerationTokenLimit, InferenceCacheHints, StageOpenAiBackend, generation_ids,
        },
        generation_receipt::{
            GenerationLifecycleIngress, GenerationLifecycleObservation, GenerationReceipt,
            GenerationReceiptConfig, GenerationStart,
        },
        request::{chat_sampling_config, chat_template_options},
    },
    kv_integration::KvStageIntegration,
    runtime_state::{loaded_memory_cache_capabilities, loaded_model_state_kind},
    telemetry::{CapturedEvent, Telemetry, TelemetryLevel},
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use skippy_cache::L3CacheManager;
use skippy_inference_api::{
    InferenceRequestContext, chat::ChatCompletionRequest, generation::GenerationRequest,
};
use std::{
    env, fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    sync::{Arc, mpsc},
    time::Duration,
};

mod backend;
mod conversation;
mod evidence;
mod fixture;
mod ownership;
mod reference;
mod structured;
mod template;
mod tiers;

use backend::Backend;
use evidence::{CacheObservation, Candidate, Generation, Report, Status, TokenScore};
use fixture::{Family, Fixture};

const LLAMA_PIN: &str = include_str!("../../../../../llama_cpp/upstream.txt");
const MAX_TOKENS: u32 = 8;
const TOP_K: u32 = 5;
const CONTEXT_TOKENS: u32 = 8192;
const BATCH_TOKENS: u32 = 512;
const TIMEOUT: Duration = Duration::from_secs(120);

#[test]
#[ignore = "requires retained JSON evidence from a real hardware qualification"]
fn verify_report() -> Result<()> {
    let path = required_path("SKIPPY_CACHE_QUAL_VERIFY_REPORT")?;
    let mut report: Report = serde_json::from_reader(BufReader::new(fs::File::open(path)?))?;
    ensure!(
        report.backend == Backend::selected()?,
        "report backend does not match the requested qualification profile"
    );
    evidence::verify_saved(&mut report)
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires the pinned llama-server, an admitted model and actual qualification hardware"]
async fn cross_tier() -> Result<()> {
    let directory = required_path("SKIPPY_CACHE_QUAL_REPORT_DIR")?;
    fs::create_dir(&directory)
        .with_context(|| format!("create fresh report directory {}", directory.display()))?;

    let family = match env::var("SKIPPY_CACHE_QUAL_FAMILY") {
        Ok(value) => Family::parse(&value)?,
        Err(env::VarError::NotPresent) => Family::DenseAttention,
        Err(error) => return Err(error.into()),
    };
    let mut report = Report::new(Backend::selected()?, Fixture::load(family)?);
    let result = qualify(&directory, &mut report).await;
    match &result {
        Ok(()) => report.status = Status::Pass,
        Err(error) => report.error = Some(format!("{error:#}")),
    }
    let report_path = directory.join("result.json");
    let mut file = fs::File::create(&report_path)?;
    serde_json::to_writer_pretty(&mut file, &report)?;
    writeln!(file)?;
    println!("Cache qualification report: {}", report_path.display());
    result
}

async fn qualify(directory: &Path, report: &mut Report) -> Result<()> {
    let backend = report.backend;
    backend.require_host()?;
    let model = required_path("SKIPPY_CACHE_QUAL_MODEL")?.canonicalize()?;
    let fixture = report.fixture.clone();
    let model_sha256 = fixture.verify_model(&model)?;
    report.model_sha256 = Some(model_sha256.clone());
    report.skippy_binary_sha256 = Some(file_sha256(&env::current_exe()?)?);

    let messages = messages();
    let request = generation_request(&fixture, &messages)?;
    // Run the two engines sequentially so the reference cannot change the
    // candidate's device resource budget.
    report.reference =
        Some(reference::observe(backend, directory, &model, &fixture, &messages).await?);
    let config = stage_config(&fixture, &model, &model_sha256)?;
    let native_log = directory.join("skippy-native.log");
    skippy_runtime::redirect_native_logs_to_file(&native_log)?;
    let result = observe_candidate(fixture.family, config.clone(), &request, report).await;
    skippy_runtime::restore_native_logs();
    result?;
    report.skippy_offloaded_layers =
        Some(backend.offloaded_layers(&fs::read_to_string(native_log)?)?);
    ownership::observe(directory, config.clone(), report).await?;
    template::capture(
        &directory.join("template-identity"),
        backend,
        config,
        &fixture,
        report.template.get_or_insert_default(),
    )?;
    ensure!(
        file_sha256(&model)? == model_sha256,
        "fixture changed during the run"
    );

    evidence::validate_measurements(report)
}

fn messages() -> Value {
    json!([{"role":"user","content": user_content(
        "Continue this sequence, starting immediately with 5: 1, 2, 3, 4,"
    )}])
}

fn user_content(question: &str) -> String {
    format!(
        "{}\n\n{question}",
        include_str!("../../../tests/fixtures/skippy-cache-prompt-input.txt")
    )
}

fn generation_request(fixture: &Fixture, messages: &Value) -> Result<GenerationRequest> {
    Ok(serde_json::from_value::<ChatCompletionRequest>(json!({
        "model": fixture.model_id(),
        "messages": messages,
        "temperature": 0,
        "seed": 1,
        "max_tokens": MAX_TOKENS,
        "logprobs": true,
        "top_logprobs": TOP_K,
        "reasoning_effort": "none"
    }))?
    .into())
}

fn stage_config(fixture: &Fixture, model: &Path, sha256: &str) -> Result<StageConfig> {
    Ok(serde_json::from_value(json!({
        "run_id": "cache-qualification",
        "topology_id": "standalone",
        "model_id": fixture.model_id(),
        "model_path": model,
        "source_model_path": model,
        "source_model_sha256": sha256,
        "source_model_bytes": fs::metadata(model)?.len(),
        "stage_id": "stage-0",
        "stage_index": 0,
        "layer_start": 0,
        "layer_end": fixture.layers,
        "ctx_size": CONTEXT_TOKENS,
        "lane_count": 1,
        "n_batch": BATCH_TOKENS,
        "n_ubatch": BATCH_TOKENS,
        "n_gpu_layers": -1,
        "cache_type_k": "f16",
        "cache_type_v": "f16",
        "kv_unified": true,
        "flash_attn_type": "disabled",
        "native_mtp_enabled": false,
        "execution_contract": "",
        "load_mode": "runtime-slice",
        "bind_addr": "127.0.0.1:0",
        "kv_cache": {"mode":"lookup-record", "min_tokens":64, "max_entries":8}
    }))?)
}

async fn observe_candidate(
    family: Family,
    config: StageConfig,
    request: &GenerationRequest,
    report: &mut Report,
) -> Result<()> {
    let mut candidate = CandidateRunner::new(config, family)?;
    report.cold = Some(candidate.generate(request)?);
    let structured_cases = structured::observe_cold(&report.fixture, &candidate).await?;
    let mut conversations = Vec::new();
    for case in conversation::Case::ALL {
        let request = generation_request(&report.fixture, &case.messages())?;
        let cold = candidate
            .generate(&request)
            .with_context(|| format!("cold {case:?}"))?;
        conversations.push((case, request, cold));
    }
    let cache = candidate.enable_cache(None)?;
    ensure!(
        cache.l2.is_none(),
        "host-RAM reuse must be disabled for the L1 control"
    );
    report.seed = Some(candidate.generate(request)?);
    tiers::drain_workers(&cache)?;
    report.replay = Some(candidate.generate(request)?);
    for (case, request, cold) in conversations {
        tiers::drain_workers(&cache)?;
        let warm = candidate
            .generate(&request)
            .with_context(|| format!("warm {case:?}"))?;
        report
            .conversations
            .insert(case, conversation::Observation { cold, warm });
    }
    structured::observe_warm(structured_cases, &candidate, &cache, &mut report.structured).await?;
    Ok(())
}

struct CandidateRunner {
    backend: StageOpenAiBackend,
    family: Family,
    receipts: mpsc::Receiver<GenerationReceipt>,
    starts: mpsc::Receiver<GenerationStart>,
    events: mpsc::Receiver<CapturedEvent>,
}

impl CandidateRunner {
    fn new(config: StageConfig, family: Family) -> Result<Self> {
        let (sender, receipts) = mpsc::sync_channel(1);
        let (start_sender, starts) = mpsc::sync_channel(1);
        let (telemetry, events) = Telemetry::captured(config.clone(), TelemetryLevel::Debug);
        let model_id = config.model_id.clone();
        let mut backend = local_openai_backend(config, model_id)?;
        backend.telemetry = telemetry;
        backend.generation_receipt = Some(GenerationReceiptConfig::from_lifecycle_ingress(
            Arc::new(ReceiptIngress {
                receipts: sender,
                starts: start_sender,
            }),
        ));
        Ok(Self {
            backend,
            family,
            receipts,
            starts,
            events,
        })
    }

    fn enable_cache(&mut self, manager: Option<L3CacheManager>) -> Result<Arc<KvStageIntegration>> {
        let cache = Arc::new(
            KvStageIntegration::from_loaded_model_with_l3_manager(
                &self.backend.config,
                loaded_model_state_kind(Some(&self.backend.runtime)),
                loaded_memory_cache_capabilities(Some(&self.backend.runtime)),
                manager,
                None,
            )?
            .context("loaded runtime did not admit cache reuse")?,
        );
        ensure!(
            cache.payload == self.family.payload()
                && self
                    .backend
                    .config
                    .kv_cache
                    .as_ref()
                    .is_some_and(|kv| kv.mode == StageKvCacheMode::LookupRecord),
            "fixture cache mode {:?} must admit {:?} lookup and recording",
            cache.payload,
            self.family.payload()
        );
        self.backend.kv = Some(Arc::clone(&cache));
        Ok(cache)
    }

    fn generate(&self, request: &GenerationRequest) -> Result<Candidate> {
        self.generate_with_context(request, &InferenceRequestContext::new())
    }

    fn generate_with_context(
        &self,
        request: &GenerationRequest,
        context: &InferenceRequestContext,
    ) -> Result<Candidate> {
        let result = generate(&self.backend, request, &self.receipts, context);
        let start = self.starts.try_recv();
        let mut observation = result?;
        let start = start.context("missing native generation start")?;
        ensure!(
            start.prompt_token_ids.as_ref() == observation.generation.prompt_ids.as_slice(),
            "native prompt changed before the generation receipt"
        );
        self.observe_restore(&mut observation.cache)?;
        Ok(observation)
    }

    fn observe_restore(&self, cache: &mut CacheObservation) -> Result<()> {
        let mut restores = self
            .events
            .try_iter()
            .filter(|event| event.event == "stage.openai_kv_lookup_decision")
            .filter(|event| event.attributes.contains_key("skippy.exact_cache.source"));
        if let Some(event) = restores.next() {
            let attr = |key| {
                event
                    .attributes
                    .get(key)
                    .and_then(Value::as_str)
                    .map(str::to_owned)
            };
            cache.restore_source = attr("skippy.exact_cache.source");
            cache.payload_kind = attr("skippy.exact_cache.payload_kind");
        }
        ensure!(
            restores.next().is_none(),
            "request reported multiple exact restore sources"
        );
        Ok(())
    }
}

fn generate(
    backend: &crate::frontend::generation::StageOpenAiBackend,
    request: &GenerationRequest,
    receipts: &mpsc::Receiver<GenerationReceipt>,
    context: &InferenceRequestContext,
) -> Result<Candidate> {
    let defaults = backend.request_defaults.snapshot();
    let prompt =
        backend.prepare_chat_prompt(request, chat_template_options(request, &defaults)?)?;
    let prompt_sha256 = sha256(prompt.text.as_bytes());
    let checkpoint = prompt
        .recurrent_cache_prefix_text
        .as_deref()
        .map(|text| backend.tokenize(text))
        .transpose()?;
    let sampling = chat_sampling_config(request, &defaults)?;
    let ids = generation_ids(
        InferenceCacheHints::from_chat_request(request),
        None,
        context,
    )
    .with_chat_template(prompt.cache_template_digest);
    let output = backend.generate_text(
        prompt,
        GenerationTokenLimit::Explicit(MAX_TOKENS),
        None,
        (0, 0),
        request.stop.as_ref(),
        sampling,
        None,
        None,
        None,
        ids,
        |_| Ok(()),
    )?;
    let receipt = receipts
        .try_recv()
        .context("missing native generation receipt")?;
    let chat_checkpoint_tokens = checkpoint
        .filter(|tokens| {
            !tokens.is_empty()
                && tokens.len() < receipt.prompt_token_ids.len()
                && receipt.prompt_token_ids.starts_with(tokens)
        })
        .map(|tokens| u32::try_from(tokens.len()))
        .transpose()?;
    let generation = Generation {
        prompt_sha256,
        prompt_ids: receipt.prompt_token_ids.to_vec(),
        generated_ids: receipt.generated_token_ids.into_vec(),
        content: output.text,
        top_logprobs: output
            .token_logprobs
            .context("missing Skippy logprobs")?
            .into_iter()
            .map(|row| {
                row.top
                    .into_iter()
                    .map(|score| TokenScore {
                        id: score.token_id,
                        logprob: score.logprob.map(f64::from),
                    })
                    .collect()
            })
            .collect(),
    };
    Ok(Candidate {
        generation,
        chat_checkpoint_tokens,
        cache: CacheObservation {
            status: output.cache_status.into(),
            hit_kind: output.cache_hit_kind.map(str::to_owned),
            restore_source: None,
            payload_kind: None,
            prompt_tokens: output.prompt_tokens,
            cached_tokens: output.cached_prompt_tokens,
            matched_tokens: output.matched_prefix_tokens,
            suffix_tokens: output.suffix_prefill_tokens,
        },
    })
}

struct ReceiptIngress {
    receipts: mpsc::SyncSender<GenerationReceipt>,
    starts: mpsc::SyncSender<GenerationStart>,
}

impl GenerationLifecycleIngress for ReceiptIngress {
    fn try_submit(&self, observation: GenerationLifecycleObservation) -> Result<()> {
        match observation {
            GenerationLifecycleObservation::Started(start) => self
                .starts
                .try_send(start)
                .context("duplicate or undeliverable generation start")?,
            GenerationLifecycleObservation::Completed(receipt) => {
                self.receipts
                    .try_send(receipt)
                    .context("duplicate or undeliverable generation receipt")?
            }
            _ => {}
        }
        Ok(())
    }
}

fn required_path(key: &str) -> Result<PathBuf> {
    env::var_os(key)
        .map(PathBuf::from)
        .with_context(|| format!("set {key}"))
}

fn sha256(bytes: &[u8]) -> String {
    hex_digest(&Sha256::digest(bytes))
}

fn file_sha256(path: &Path) -> Result<String> {
    let file = fs::File::open(path).with_context(|| format!("open {}", path.display()))?;
    let mut reader = BufReader::with_capacity(64 * 1024, file);
    let mut digest = Sha256::new();
    loop {
        let bytes = reader.fill_buf()?;
        if bytes.is_empty() {
            break;
        }
        digest.update(bytes);
        let count = bytes.len();
        reader.consume(count);
    }
    Ok(hex_digest(&digest.finalize()))
}

fn hex_digest(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
