use super::{
    Backend, CandidateRunner, EXPECTED_CODE, Family, Fixture, StageConfig,
    evidence::{Candidate, compare, validate_durable_replay, validate_miss},
    generation_request, messages, required_path, stage_config, tiers,
};
use crate::frontend::EmbeddedOpenAiRequestDefaults;
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{env, fs, path::Path};

const BODY: &str = concat!(
    "{% for message in messages %}",
    "{{ '<|im_start|>' + message.role + '\n' + message.content + '<|im_end|>\n' }}",
    "{% endfor %}{% if add_generation_prompt %}{{ '<|im_start|>assistant\n' }}{% endif %}"
);

#[derive(Default, Deserialize, Serialize)]
pub(super) struct Observation {
    offloaded_layers: Option<usize>,
    checkpoint: Option<u32>,
    cold: Option<Candidate>,
    seed: Option<Candidate>,
    restored: Vec<Candidate>,
    revised: Option<Candidate>,
    revised_disk_payload_bytes: Option<u64>,
}

impl Observation {
    pub(super) fn validate(&self) -> Result<()> {
        ensure!(
            self.offloaded_layers.is_some_and(|layers| layers > 0),
            "template case lacks GPU offload evidence"
        );
        let cold = self
            .cold
            .as_ref()
            .context("missing cold template observation")?;
        let seed = self.seed.as_ref().context("missing template seed")?;
        let checkpoint = self.checkpoint.context("missing template checkpoint")?;
        let prompt_tokens = u32::try_from(cold.generation.prompt_ids.len())?;
        validate_miss(prompt_tokens, seed).context("original template seed")?;
        compare(&cold.generation, &seed.generation, EXPECTED_CODE)
            .context("original template seed")?;
        ensure!(
            self.restored.len() == 3,
            "incomplete template restore sequence"
        );
        for (source, replay) in ["l3", "l3", "l2"].into_iter().zip(&self.restored) {
            validate_durable_replay(prompt_tokens, replay, source, checkpoint)
                .with_context(|| format!("original template {source} restore"))?;
            compare(&cold.generation, &replay.generation, EXPECTED_CODE)
                .with_context(|| format!("original template {source} output"))?;
        }
        let revised = self
            .revised
            .as_ref()
            .context("missing revised template observation")?;
        validate_miss(prompt_tokens, revised).context("revised template prefill")?;
        compare(&cold.generation, &revised.generation, EXPECTED_CODE)
            .context("revised template output")?;
        ensure!(
            self.revised_disk_payload_bytes == Some(0),
            "a changed renderer read a stale disk payload"
        );
        println!(
            "Equivalent template revision missed all tiers for {prompt_tokens} identical tokens"
        );
        Ok(())
    }
}

#[test]
#[ignore = "requires a pinned model and the selected cache qualification GPU"]
fn equivalent_renderers_do_not_share_state() -> Result<()> {
    let backend = Backend::selected()?;
    backend.require_host()?;
    let family = env::var("SKIPPY_CACHE_QUAL_FAMILY")
        .map_or(Ok(Family::DenseAttention), |family| Family::parse(&family))?;
    let fixture = Fixture::load(family)?;
    let model = required_path("SKIPPY_CACHE_QUAL_MODEL")?.canonicalize()?;
    let model_sha256 = fixture.verify_model(&model)?;
    let directory = required_path("SKIPPY_CACHE_QUAL_REPORT_DIR")?;
    let config = stage_config(&fixture, &model, &model_sha256)?;
    let mut observation = Observation::default();
    capture(&directory, backend, config, &fixture, &mut observation)?;
    observation.validate()
}

pub(super) fn capture(
    directory: &Path,
    backend: Backend,
    config: StageConfig,
    fixture: &Fixture,
    observation: &mut Observation,
) -> Result<()> {
    fs::create_dir(directory)?;
    let config = tiers::with_host_cache(config)?;
    let mut original = generation_request(fixture, &messages())?;
    original.extra.insert("chat_template".into(), json!(BODY));
    let mut revised = original.clone();
    revised.extra.insert(
        "chat_template".into(),
        json!(format!("{{# an equivalent revision #}}{BODY}")),
    );
    let native_log = directory.join("template-native.log");
    skippy_runtime::redirect_native_logs_to_file(&native_log)?;
    let result = (|| {
        let mut runner = CandidateRunner::new(config, fixture.family)?;
        runner.backend.request_defaults = EmbeddedOpenAiRequestDefaults {
            allow_request_chat_template: Some(true),
            ..Default::default()
        }
        .into();
        observe(directory, runner, &original, &revised, observation)
    })();
    skippy_runtime::restore_native_logs();
    result?;
    observation.offloaded_layers =
        Some(backend.offloaded_layers(&fs::read_to_string(native_log)?)?);
    Ok(())
}

fn observe(
    directory: &Path,
    mut runner: CandidateRunner,
    original: &skippy_inference_api::generation::GenerationRequest,
    revised: &skippy_inference_api::generation::GenerationRequest,
    observation: &mut Observation,
) -> Result<()> {
    observation.cold = Some(runner.generate(original)?);
    let manager = tiers::open_store(directory)?;
    let cache = runner.enable_cache(Some(manager.clone()))?;
    let seed = observation.seed.insert(runner.generate(original)?);
    observation.checkpoint = Some(tiers::published_checkpoint(runner.family, &manager, seed)?);
    tiers::evict_l1(&runner, &cache)?;
    for attempt in 0..3 {
        if attempt > 0 {
            tiers::evict_l1(&runner, &cache)?;
        }
        observation.restored.push(runner.generate(original)?);
        tiers::drain_workers(&cache)?;
    }
    let disk_before = manager.activity()?.bytes_read;
    observation.revised = Some(runner.generate(revised)?);
    observation.revised_disk_payload_bytes =
        manager.activity()?.bytes_read.checked_sub(disk_before);
    tiers::drain_workers(&cache)?;
    Ok(())
}
