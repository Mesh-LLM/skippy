//! Standalone disk cache administration.

mod admin;
mod report;

use std::{
    ffi::OsString,
    io::{self, IsTerminal, Write},
    path::PathBuf,
};

use anyhow::{Context, Result, bail, ensure};
use clap::{Args, Subcommand};
use serde::{Deserialize, Serialize};
use skippy_api::serving::LocalDiskCacheOptions;
use skippy_cache::{
    L3CacheManager,
    disk_policy::{DiskCacheBudget, acquire_disk_cache, parse_disk_budget},
};
use skippy_commands::console;

pub(crate) use admin::start as start_admin;
use report::{Outcome, Report, Status};

#[derive(Args)]
pub(crate) struct CacheArgs {
    /// The serving settings file used to configure this cache.
    #[arg(long, global = true)]
    settings: Option<PathBuf>,
    /// Disk cache budget (off, auto, or an IEC size).
    #[arg(long, global = true)]
    kv_cache_disk: Option<String>,
    /// Absolute path to the disk cache root.
    #[arg(long, global = true)]
    kv_cache_disk_dir: Option<PathBuf>,
    /// Minimum free space to keep on the disk cache filesystem.
    #[arg(long, global = true)]
    kv_cache_min_free: Option<String>,
    #[command(subcommand)]
    command: CacheCommand,
}

#[derive(Subcommand)]
enum CacheCommand {
    /// Report disk cache limits, state, activity and model inventory.
    Status,
    /// Evict inactive entries toward a byte target.
    Prune {
        /// Target for whole-cache usage, such as 4GiB. Defaults to the cache's low-water mark.
        #[arg(long, value_parser = target_bytes)]
        target: Option<u64>,
        #[command(flatten)]
        scope: MaintenanceScope,
    },
    /// Remove eligible inactive entries.
    Clear {
        #[command(flatten)]
        scope: MaintenanceScope,
    },
}

#[derive(Args)]
struct MaintenanceScope {
    /// Select the exact model identity shown by kv-cache status.
    #[arg(long, value_parser = exact_identity)]
    model_identity: Option<String>,
    /// Confirm removal of inactive entries.
    #[arg(long)]
    yes: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case", tag = "operation", deny_unknown_fields)]
enum Operation {
    Status,
    Prune {
        target_bytes: Option<u64>,
        model_identity: Option<String>,
    },
    Clear {
        model_identity: Option<String>,
    },
}

pub(crate) async fn run(args: CacheArgs) -> Result<()> {
    let settings = resolve_settings(&args)?;
    let Some(settings) = settings else {
        ensure!(
            matches!(args.command, CacheCommand::Status),
            "disk KV cache is disabled; use the disk cache settings from skippy serve"
        );
        return Report::status(Status::disabled()).present();
    };
    let operation = args.command.confirm()?;
    match admin::forward(&settings.directory, &operation).await {
        Ok(Some(report)) => return report.present(),
        Ok(None) => {}
        Err(error) if matches!(operation, Operation::Status) => {
            return Report::status(Status::unavailable(&settings, format!("{error:#}"))).present();
        }
        Err(error) => return Err(error),
    }
    tokio::task::spawn_blocking(move || offline(settings, operation))
        .await??
        .present()
}

fn resolve_settings(args: &CacheArgs) -> Result<Option<LocalDiskCacheOptions>> {
    // Reuse the serving parser so it applies the same settings sources,
    // aliases, relative paths and validation without preparing a model.
    let mut argv = vec![OsString::from("skippy"), OsString::from("serve")];
    for (flag, value) in [
        ("--settings", args.settings.as_ref().map(|x| x.as_os_str())),
        (
            "--kv-cache-disk",
            args.kv_cache_disk.as_ref().map(AsRef::as_ref),
        ),
        (
            "--kv-cache-disk-dir",
            args.kv_cache_disk_dir.as_ref().map(|x| x.as_os_str()),
        ),
        (
            "--kv-cache-min-free",
            args.kv_cache_min_free.as_ref().map(AsRef::as_ref),
        ),
    ] {
        if let Some(value) = value {
            argv.push(flag.into());
            argv.push(value.into());
        }
    }
    let crate::cli::Command::Serve(serve) = crate::serve_settings::parse(argv)?.command else {
        unreachable!("synthetic settings arguments always select serve");
    };
    crate::disk_cache::from_public_settings(
        serve.public.kv_cache_disk.as_deref(),
        serve.public.kv_cache_disk_dir,
        serve.public.kv_cache_min_free.as_deref(),
    )
}

impl CacheCommand {
    fn confirm(self) -> Result<Operation> {
        let (scope, target) = match self {
            Self::Status => return Ok(Operation::Status),
            Self::Prune { target, scope } => (scope, Some(target)),
            Self::Clear { scope } => (scope, None),
        };
        if !scope.yes {
            confirm_removal(scope.model_identity.as_deref())?;
        }
        let model_identity = scope.model_identity;
        Ok(match target {
            Some(target_bytes) => Operation::Prune {
                target_bytes,
                model_identity,
            },
            None => Operation::Clear { model_identity },
        })
    }
}

fn confirm_removal(model_identity: Option<&str>) -> Result<()> {
    let stdin = io::stdin();
    ensure!(
        stdin.is_terminal() && console::mode() == console::OutputMode::Human,
        "cache maintenance requires interactive confirmation or --yes"
    );
    let mut stderr = io::stderr().lock();
    match model_identity {
        Some(identity) => write!(
            stderr,
            "Remove eligible entries for model {identity:?} from the disk KV cache? [y/N] "
        )?,
        None => write!(
            stderr,
            "Remove eligible entries from the disk KV cache? [y/N] "
        )?,
    }
    stderr.flush()?;
    let mut answer = String::new();
    stdin.read_line(&mut answer)?;
    ensure!(
        answer.trim().eq_ignore_ascii_case("yes") || answer.trim().eq_ignore_ascii_case("y"),
        "cache maintenance cancelled"
    );
    Ok(())
}

fn target_bytes(value: &str) -> Result<u64> {
    match parse_disk_budget(value)? {
        DiskCacheBudget::Fixed(bytes) => Ok(bytes),
        _ => bail!("prune target must be a positive IEC size, such as 4GiB"),
    }
}

fn exact_identity(value: &str) -> Result<String> {
    ensure!(!value.trim().is_empty(), "model identity must be non-empty");
    Ok(value.to_owned())
}

fn offline(settings: LocalDiskCacheOptions, operation: Operation) -> Result<Report> {
    let manager = acquire_disk_cache(
        &settings.directory,
        settings.budget,
        settings.minimum_free_bytes,
    )
    .and_then(|manager| manager.context("automatic cache budget is zero"))
    .with_context(|| {
        format!(
            "cannot administer {} offline; check its configuration and permissions or stop the skippy serve process that owns it",
            settings.directory.display()
        )
    });
    match manager {
        Ok(manager) => operation.execute(&manager),
        Err(error) if matches!(operation, Operation::Status) => Ok(Report::status(
            Status::unavailable(&settings, format!("{error:#}")),
        )),
        Err(error) => Err(error),
    }
}

impl Operation {
    fn execute(&self, manager: &L3CacheManager) -> Result<Report> {
        let outcome = match self {
            Self::Status => return Ok(Report::status(Status::from_manager(manager)?)),
            Self::Prune {
                target_bytes,
                model_identity,
            } => {
                let target = target_bytes
                    .or_else(|| manager.limits().low_water_bytes())
                    .context("an uncapped cache requires an explicit --target")?;
                let freed_bytes = match model_identity {
                    Some(identity) => manager.prune_model_to(&exact_identity(identity)?, target)?,
                    None => manager.prune_to(target)?,
                };
                let used_bytes = manager.usage()?.used_bytes;
                Outcome::Prune {
                    model_identity: model_identity.clone(),
                    target_bytes: target,
                    freed_bytes,
                    used_bytes,
                    target_reached: used_bytes <= target,
                }
            }
            Self::Clear { model_identity } => {
                let freed_bytes = match model_identity {
                    Some(identity) => manager.clear_model(&exact_identity(identity)?)?,
                    None => manager.clear()?,
                };
                Outcome::Clear {
                    model_identity: model_identity.clone(),
                    freed_bytes,
                    used_bytes: manager.usage()?.used_bytes,
                }
            }
        };
        Ok(Report::new(outcome))
    }
}
