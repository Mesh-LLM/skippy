use super::evidence::{Candidate, DurableObservations};
use super::{CandidateRunner, Family, StageConfig};
use crate::{kv_integration::KvStageIntegration, runtime_state::panic_recovery::lock_runtime};
use anyhow::{Context, Result, anyhow, ensure};
use skippy_cache::{L3CacheManager, StoreLimits};
use skippy_inference_api::{
    InferenceExecutionLease, InferenceExecutionScope, InferenceRequestContext,
    generation::GenerationRequest,
};
use std::{
    ffi::OsStr,
    fs,
    path::{Path, PathBuf},
    sync::Arc,
    thread,
    time::{Duration, Instant},
};

const STORE_LIMIT: u64 = 2 * 1024 * 1024 * 1024;
const HOST_LIMIT: u64 = 256 * 1024 * 1024;
const WORKER_TIMEOUT: Duration = Duration::from_secs(15);

pub(super) fn observe(
    family: Family,
    directory: &Path,
    config: StageConfig,
    request: &GenerationRequest,
    observations: &mut DurableObservations,
) -> Result<PathBuf> {
    let config = with_host_cache(config)?;
    let manager = open_store(directory)?;
    ensure!(manager.inventory()?.is_empty(), "new L3 store is not empty");
    let mut runner = CandidateRunner::new(config, family)?;
    let cache = runner.enable_cache(Some(manager.clone()))?;
    let host = cache
        .l2
        .as_ref()
        .context("loaded runtime did not admit L2")?;

    let seed = observations.seed.insert(runner.generate(request)?);
    observations.evictions.push(evict_l1(&runner, &cache)?);
    observations.checkpoint_tokens = Some(published_checkpoint(&manager, seed)?);
    let pack_name = published_pack_name(directory)?;

    // For shorter checkpoints the production host policy promotes after
    // two disk fills. Drain L1 before each fill while retaining the same L2.
    observations.first_l3 = Some(runner.generate(request)?);
    observations.evictions.push(evict_l1(&runner, &cache)?);
    observations.second_l3 = Some(runner.generate(request)?);
    drain_workers(&cache)?;
    ensure!(
        host.stats().entries > 0,
        "L3 fills did not publish a host-RAM entry"
    );

    observations.evictions.push(evict_l1(&runner, &cache)?);
    let disk_before = manager.activity()?.bytes_read;
    observations.l2 = Some(runner.generate(request)?);
    let disk_after = manager.activity()?.bytes_read;
    observations.l2_disk_payload_bytes = Some(disk_after.saturating_sub(disk_before));
    drain_workers(&cache)?;

    let scope = isolated_context();
    observations.namespace_miss = Some(runner.generate_with_context(request, &scope)?);
    drain_workers(&cache)?;
    Ok(pack_name)
}

pub(super) fn published_checkpoint(manager: &L3CacheManager, seed: &Candidate) -> Result<u32> {
    let prefill = seed
        .generation
        .prompt_ids
        .len()
        .checked_sub(1)
        .context("empty prompt")?;
    let tokens = seed
        .chat_checkpoint_tokens
        .map(u64::from)
        .unwrap_or(prefill as u64);
    wait_for_checkpoint(manager, tokens)?;
    Ok(u32::try_from(tokens)?)
}

#[derive(Debug)]
struct QualificationLease;

impl InferenceExecutionLease for QualificationLease {}

pub(super) fn isolated_context() -> InferenceRequestContext {
    InferenceRequestContext::new()
        .with_execution_scope(InferenceExecutionScope::new(), Arc::new(QualificationLease))
}

pub(super) fn with_host_cache(mut config: StageConfig) -> Result<StageConfig> {
    config
        .kv_cache
        .as_mut()
        .context("missing cache configuration")?
        .l2_max_bytes = HOST_LIMIT;
    Ok(config)
}

pub(super) fn open_store(directory: &Path) -> Result<L3CacheManager> {
    L3CacheManager::acquire(
        directory.canonicalize()?.join("l3"),
        StoreLimits::new(STORE_LIMIT, 0),
    )
}

fn published_pack_name(directory: &Path) -> Result<PathBuf> {
    let mut packs = fs::read_dir(directory.join("l3/packs"))?;
    let pack = packs.next().context("missing published disk payload")??;
    ensure!(
        pack.file_type()?.is_file() && packs.next().is_none(),
        "expected one packed object in the new qualification cache"
    );
    Ok(pack.file_name().into())
}

pub(super) fn wait_for_checkpoint(manager: &L3CacheManager, tokens: u64) -> Result<()> {
    let deadline = Instant::now() + WORKER_TIMEOUT;
    loop {
        if manager
            .inventory()?
            .iter()
            .any(|entry| entry.token_count == tokens && entry.total_bytes > 0)
        {
            return Ok(());
        }
        ensure!(
            Instant::now() < deadline,
            "L3 checkpoint with {tokens} tokens was not published: {:?}",
            manager.activity()?
        );
        thread::sleep(Duration::from_millis(10));
    }
}

pub(super) fn drain_workers(cache: &KvStageIntegration) -> Result<()> {
    cache
        .wait_for_exact_state_captures_idle(WORKER_TIMEOUT)
        .map_err(|error| anyhow!(error))?;
    cache
        .wait_for_exact_state_recording(WORKER_TIMEOUT)
        .map_err(|error| anyhow!(error))
}

pub(super) fn evict_l1(
    runner: &CandidateRunner,
    cache: &KvStageIntegration,
) -> Result<super::evidence::Eviction> {
    drain_workers(cache)?;
    // Drive production resident eviction so native sequences and ownership
    // counters are released together. Both L1 representations must be gone.
    let session_id = "cache-qualification-eviction";
    let mut runtime = lock_runtime(&runner.backend.runtime);
    runtime.ensure_session_active(session_id)?;
    let resident = cache.evict_resident_prefix_for_tokens(&mut runtime, session_id, u64::MAX);
    runtime.drop_session_timed(session_id)?;
    let resident = resident?;
    drop(runtime);

    let mut radix = cache.radix.lock().unwrap();
    let exact = std::iter::from_fn(|| radix.evict_lru_recurrent())
        .map(|entry| entry.value.payload)
        .collect::<Vec<_>>();
    let stats = radix.stats();
    drop(radix);
    let mut blobs = cache.exact_blobs.lock().unwrap();
    for payload in &exact {
        payload.release_from(&mut blobs)?;
    }
    ensure!(
        stats.resident_entries == 0 && stats.recurrent_entries == 0,
        "L1 entries survived forced eviction"
    );
    Ok(super::evidence::Eviction {
        resident_entries: resident.evicted_entries,
        exact_entries: exact.len(),
    })
}

pub(super) fn observe_recovery(
    directory: &Path,
    pack_name: &Path,
    manager: &L3CacheManager,
    mut runner: CandidateRunner,
    request: &GenerationRequest,
) -> Result<(Candidate, Candidate, u64)> {
    let cache = runner.enable_cache(Some(manager.clone()))?;
    let replay = runner.generate(request)?;
    evict_l1(&runner, &cache)?;

    ensure!(
        pack_name.components().count() == 1 && pack_name.extension() == Some(OsStr::new("pack")),
        "expected a packed object name in the qualification store"
    );
    let pack = directory.join("l3/packs").join(pack_name);
    ensure!(
        fs::symlink_metadata(&pack)?.file_type().is_file(),
        "cache pack is not a regular file"
    );
    // The isolated test root has no other readers or writers. Damage the
    // actual on-disk object after the successful restore, leaving its index
    // in place so the serving lookup is responsible for detecting damage.
    let corrupt_before = manager.activity()?.corrupt_entries;
    fs::OpenOptions::new().write(true).open(&pack)?.set_len(1)?;
    let fallback = runner.generate(request)?;
    let corrupt_count = manager
        .activity()?
        .corrupt_entries
        .saturating_sub(corrupt_before);
    drain_workers(&cache)?;
    Ok((replay, fallback, corrupt_count))
}
