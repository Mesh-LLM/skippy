use super::{
    Backend, CandidateRunner, Family, Fixture, Report, Status, evidence, file_sha256,
    generation_request, messages, ownership, required_path, stage_config, tiers,
};
use anyhow::{Context, Result, ensure};
use serde_json::json;
use std::{env, fs, path::Path};

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires the pinned dense fixture and actual qualification hardware"]
async fn packaged_cache_smoke() -> Result<()> {
    let directory = required_path("SKIPPY_CACHE_QUAL_REPORT_DIR")?;
    fs::create_dir(&directory)?;
    let backend = Backend::selected()?;
    let fixture = Fixture::load(Family::DenseAttention)?;
    let mut report = Report::new(backend, fixture);
    let result = observe(&directory, &mut report).await;
    match &result {
        Ok(()) => report.status = Status::Pass,
        Err(error) => report.error = Some(format!("{error:#}")),
    }
    fs::write(
        directory.join("smoke-result.json"),
        serde_json::to_vec_pretty(&report)?,
    )?;
    result
}

async fn observe(directory: &Path, report: &mut Report) -> Result<()> {
    let backend = report.backend;
    backend.require_host()?;
    skippy_runtime::test_support::require_native_runtime();
    let model = required_path("SKIPPY_CACHE_QUAL_MODEL")?.canonicalize()?;
    let fixture = &report.fixture;
    let family = fixture.family;
    let digest = fixture.verify_model(&model)?;
    report.model_sha256 = Some(digest.clone());
    report.skippy_binary_sha256 = Some(file_sha256(&env::current_exe()?)?);
    let config = stage_config(fixture, &model, &digest)?;
    let request = generation_request(fixture, &messages())?;
    let log = directory.join("skippy-native.log");
    skippy_runtime::redirect_native_logs_to_file(&log)?;
    let observed = (|| {
        let manager = tiers::open_store(directory)?;
        ensure!(manager.inventory()?.is_empty(), "new L3 store is not empty");
        let mut runner = CandidateRunner::new(config.clone(), family)?;
        report.cold = Some(runner.generate(&request)?);
        let cache = runner.enable_cache(Some(manager.clone()))?;
        report.seed = Some(runner.generate(&request)?);
        let checkpoint = tiers::published_checkpoint(
            &manager,
            &cache,
            report.seed.as_ref().context("missing cache seed")?,
        )?;
        tiers::drain_workers(&cache)?;
        report.replay = Some(runner.generate(&request)?);
        tiers::drain_workers(&cache)?;
        Ok::<_, anyhow::Error>(checkpoint)
    })();
    skippy_runtime::restore_native_logs();
    let checkpoint = observed?;
    report.skippy_offloaded_layers = Some(backend.offloaded_layers(&fs::read_to_string(log)?)?);
    evidence::validate_l1(report)?;
    let cold = &report
        .cold
        .as_ref()
        .context("missing cold generation")?
        .generation;
    let seed = &report
        .seed
        .as_ref()
        .context("missing cache seed")?
        .generation;
    let warm = &report
        .replay
        .as_ref()
        .context("missing L1 replay")?
        .generation;
    evidence::compare(cold, seed).context("seed output")?;
    evidence::compare(cold, warm).context("L1 output")?;

    let restarted = ownership::reopen(directory, config, family, backend).await?;
    ensure!(
        restarted.pid != std::process::id(),
        "L3 was not reopened in a fresh process"
    );
    evidence::validate_durable_replay(
        u32::try_from(cold.prompt_ids.len())?,
        &restarted.replay,
        "l3",
        checkpoint,
        family,
    )?;
    evidence::compare(cold, &restarted.replay.generation).context("L3 output")?;
    ensure!(
        file_sha256(&model)? == digest,
        "fixture changed during the run"
    );
    fs::write(
        directory.join("smoke-checkpoint.json"),
        serde_json::to_vec_pretty(&json!({"checkpoint_tokens": checkpoint}))?,
    )?;
    Ok(())
}
