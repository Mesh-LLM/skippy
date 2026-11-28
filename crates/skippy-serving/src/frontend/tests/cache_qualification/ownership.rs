//! Exercise L3 ownership changes with separate operating-system processes.

use super::{
    Backend, CandidateRunner, Family, Fixture, Report, StageConfig,
    evidence::{
        CleanRestartObservation, DurableObservations, OwnershipObservation, RestartObservation,
    },
    file_sha256, generation_request, messages, required_path, tiers,
};
use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use skippy_cache::CacheRootInUse;
use std::{
    env, fs,
    path::{Path, PathBuf},
    process::{ExitStatus, Stdio},
    time::Duration,
};
use tokio::{
    process::{Child, Command},
    time::{Instant, sleep, timeout},
};

const PROCESS_TIMEOUT: Duration = Duration::from_secs(300);
const REQUEST_FILE: &str = "l3-process-input.json";
const INTERRUPTED_WRITE: &str = "l3/segments/.tmp-interrupted-qualification";

#[derive(Clone, Copy)]
enum Phase {
    Publish,
    Reopen,
    CleanRestart,
    CrashRestart,
}

impl Phase {
    fn name(self) -> &'static str {
        match self {
            Self::Publish => "publish",
            Self::Reopen => "reopen",
            Self::CleanRestart => "clean-restart",
            Self::CrashRestart => "crash-restart",
        }
    }
}

#[derive(Deserialize, Serialize)]
struct ProcessInput {
    config: StageConfig,
    family: Family,
    backend: Backend,
}

#[derive(Deserialize, Serialize)]
struct Publication {
    pid: u32,
    offloaded_layers: usize,
    pack_name: PathBuf,
    durable: DurableObservations,
}

pub(super) async fn observe(
    directory: &Path,
    config: StageConfig,
    report: &mut Report,
) -> Result<()> {
    let input = ProcessInput {
        config,
        family: report.fixture.family,
        backend: report.backend,
    };
    fs::write(directory.join(REQUEST_FILE), serde_json::to_vec(&input)?)?;
    let (mut publisher, publisher_pid) = start(directory, Phase::Publish)?;
    wait_success(&mut publisher, Phase::Publish).await?;
    let publication: Publication = read_observation(directory, Phase::Publish)?;
    ensure!(publisher_pid == publication.pid, "publication PID mismatch");
    report.durable = Some(publication.durable);

    let (mut owner, owner_pid) = start(directory, Phase::CleanRestart)?;
    let clean_restart = wait_for_owner(directory, &mut owner).await?;
    ensure!(owner_pid == clean_restart.pid, "clean restart PID mismatch");
    let pack = directory.join("l3/packs").join(publication.pack_name);
    let preserved_pack_sha256 = file_sha256(&pack)?;
    let error = tiers::open_store(directory).expect_err("live owner must prevent a second open");
    ensure!(
        error.downcast_ref::<CacheRootInUse>().is_some(),
        "second process failed to open L3 for an unexpected reason: {error:#}"
    );
    let contender = generate_without_l3(directory, &input)?;
    ensure!(
        owner.try_wait()?.is_none() && file_sha256(&pack)? == preserved_pack_sha256,
        "the original owner or its committed pack did not survive lock contention"
    );
    report.ownership = Some(OwnershipObservation {
        publisher_pid,
        publisher_offloaded_layers: publication.offloaded_layers,
        clean_restart,
        lock_conflict: error.to_string(),
        contender,
        preserved_pack_sha256,
    });

    owner.start_kill()?;
    let status = timeout(PROCESS_TIMEOUT, owner.wait()).await??;
    ensure!(
        was_killed(status),
        "expected abrupt termination, got {status}"
    );
    let (mut recovery, recovery_pid) = start(directory, Phase::CrashRestart)?;
    wait_success(&mut recovery, Phase::CrashRestart).await?;
    let restart: RestartObservation = read_observation(directory, Phase::CrashRestart)?;
    ensure!(recovery_pid == restart.pid, "crash restart PID mismatch");
    report.restart = Some(restart);
    Ok(())
}

pub(super) async fn reopen(
    directory: &Path,
    config: StageConfig,
    family: Family,
    backend: Backend,
) -> Result<CleanRestartObservation> {
    let input = ProcessInput {
        config,
        family,
        backend,
    };
    fs::write(directory.join(REQUEST_FILE), serde_json::to_vec(&input)?)?;
    let (mut child, pid) = start(directory, Phase::Reopen)?;
    wait_success(&mut child, Phase::Reopen).await?;
    let observation: CleanRestartObservation = read_observation(directory, Phase::Reopen)?;
    ensure!(pid == observation.pid, "reopened cache PID mismatch");
    Ok(observation)
}

fn start(directory: &Path, phase: Phase) -> Result<(Child, u32)> {
    let name = phase.name();
    let log = fs::File::create(directory.join(format!("l3-{name}-process.log")))?;
    let child = Command::new(env::current_exe()?)
        .args([
            "frontend::tests::cache_qualification::ownership::l3_owner",
            "--ignored",
            "--exact",
            "--nocapture",
        ])
        .env("SKIPPY_CACHE_QUAL_OWNER_DIR", directory.canonicalize()?)
        .env("SKIPPY_CACHE_QUAL_OWNER_PHASE", name)
        .stdin(Stdio::piped())
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log))
        .kill_on_drop(true)
        .spawn()?;
    let pid = child.id().context("L3 process PID missing")?;
    Ok((child, pid))
}

async fn wait_success(child: &mut Child, phase: Phase) -> Result<()> {
    let status = timeout(PROCESS_TIMEOUT, child.wait())
        .await
        .with_context(|| format!("L3 {} timed out", phase.name()))??;
    ensure!(
        status.success(),
        "L3 {} failed with {status}. See l3-{}-process.log",
        phase.name(),
        phase.name()
    );
    Ok(())
}

async fn wait_for_owner(directory: &Path, owner: &mut Child) -> Result<CleanRestartObservation> {
    let deadline = Instant::now() + PROCESS_TIMEOUT;
    let path = observation_path(directory, Phase::CleanRestart);
    loop {
        if let Some(status) = owner.try_wait()? {
            bail!("L3 owner exited early with {status}. See l3-clean-restart-process.log");
        }
        if path.try_exists()? {
            return read_observation(directory, Phase::CleanRestart);
        }
        ensure!(
            Instant::now() < deadline,
            "L3 owner did not report readiness"
        );
        sleep(Duration::from_millis(50)).await;
    }
}

#[cfg(unix)]
fn was_killed(status: ExitStatus) -> bool {
    use std::os::unix::process::ExitStatusExt;
    status.signal() == Some(9)
}

#[cfg(not(unix))]
fn was_killed(status: ExitStatus) -> bool {
    !status.success()
}

#[test]
#[ignore = "spawned by the GPU qualification to publish and reopen an exclusive L3 root"]
fn l3_owner() -> Result<()> {
    let directory = required_path("SKIPPY_CACHE_QUAL_OWNER_DIR")?;
    let phase = match env::var("SKIPPY_CACHE_QUAL_OWNER_PHASE")?.as_str() {
        "publish" => Phase::Publish,
        "reopen" => Phase::Reopen,
        "clean-restart" => Phase::CleanRestart,
        "crash-restart" => Phase::CrashRestart,
        value => bail!("invalid L3 process phase {value}"),
    };
    let input: ProcessInput = serde_json::from_slice(&fs::read(directory.join(REQUEST_FILE))?)?;
    let fixture = Fixture::load(input.family)?;
    ensure!(
        input.config.model_id == fixture.model_id(),
        "model identity mismatch"
    );
    skippy_runtime::test_support::require_native_runtime();
    let native_log = directory.join(format!("l3-{}-native.log", phase.name()));
    skippy_runtime::redirect_native_logs_to_file(&native_log)?;
    let result = run_phase(&directory, input, &fixture, phase, &native_log);
    skippy_runtime::restore_native_logs();
    result
}

fn run_phase(
    directory: &Path,
    input: ProcessInput,
    fixture: &Fixture,
    phase: Phase,
    native_log: &Path,
) -> Result<()> {
    let request = generation_request(fixture, &messages())?;
    let offloaded = || {
        input
            .backend
            .offloaded_layers(&fs::read_to_string(native_log)?)
    };
    let pid = std::process::id();
    if matches!(phase, Phase::Publish) {
        let mut durable = DurableObservations::default();
        let pack_name = tiers::observe(
            input.family,
            directory,
            input.config,
            &request,
            &mut durable,
        )?;
        return write_observation(
            directory,
            phase,
            &Publication {
                pid,
                offloaded_layers: offloaded()?,
                pack_name,
                durable,
            },
        );
    }

    let manager = tiers::open_store(directory)?;
    let mut runner = CandidateRunner::new(input.config, input.family)?;
    if matches!(phase, Phase::CleanRestart | Phase::Reopen) {
        let cache = runner.enable_cache(Some(manager.clone()))?;
        let replay = runner.generate(&request)?;
        tiers::drain_workers(&cache)?;
        drop(cache);
        drop(runner);
        if matches!(phase, Phase::CleanRestart) {
            fs::write(
                directory.join(INTERRUPTED_WRITE),
                b"unpublished checkpoint bytes",
            )?;
        }
        write_observation(
            directory,
            phase,
            &CleanRestartObservation {
                pid,
                offloaded_layers: offloaded()?,
                replay,
            },
        )?;
        if matches!(phase, Phase::Reopen) {
            return Ok(());
        }
        // Retain exclusive ownership until the parent deliberately kills us.
        let mut line = String::new();
        let _ = std::io::stdin().read_line(&mut line)?;
        drop(manager);
        bail!("L3 owner exited without abrupt termination");
    }

    let publication: Publication = read_observation(directory, Phase::Publish)?;
    let reconciled_temporary_files = manager.reconciliation().removed_temporary_files;
    ensure!(
        !directory.join(INTERRUPTED_WRITE).try_exists()?,
        "reconciliation left the interrupted write in place"
    );
    let (replay, corrupt_fallback, corrupt_objects) = tiers::observe_recovery(
        directory,
        &publication.pack_name,
        &manager,
        runner,
        &request,
    )?;
    write_observation(
        directory,
        phase,
        &RestartObservation {
            pid,
            offloaded_layers: offloaded()?,
            replay,
            corrupt_fallback,
            corrupt_objects,
            reconciled_temporary_files,
        },
    )
}

fn generate_without_l3(directory: &Path, input: &ProcessInput) -> Result<super::Candidate> {
    let native_log = directory.join("l3-contender-native.log");
    skippy_runtime::redirect_native_logs_to_file(&native_log)?;
    let result = (|| {
        let fixture = Fixture::load(input.family)?;
        let mut runner = CandidateRunner::new(input.config.clone(), input.family)?;
        let cache = runner.enable_cache(None)?;
        ensure!(
            cache.l3.is_none() && cache.l2.is_none(),
            "contender attached to durable state"
        );
        runner.generate(&generation_request(&fixture, &messages())?)
    })();
    skippy_runtime::restore_native_logs();
    input
        .backend
        .offloaded_layers(&fs::read_to_string(native_log)?)?;
    result
}

fn observation_path(directory: &Path, phase: Phase) -> PathBuf {
    directory.join(format!("l3-{}-observation.json", phase.name()))
}

fn write_observation(directory: &Path, phase: Phase, value: &impl Serialize) -> Result<()> {
    let path = observation_path(directory, phase);
    let temp = path.with_extension("tmp");
    fs::write(&temp, serde_json::to_vec(value)?)?;
    fs::rename(temp, path)?;
    Ok(())
}

fn read_observation<T: DeserializeOwned>(directory: &Path, phase: Phase) -> Result<T> {
    let path = observation_path(directory, phase);
    let bytes = fs::read(&path).with_context(|| format!("read {}", path.display()))?;
    Ok(serde_json::from_slice(&bytes)?)
}
