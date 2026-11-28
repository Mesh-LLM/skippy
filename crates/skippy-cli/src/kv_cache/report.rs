use std::{
    collections::BTreeMap,
    io::{self, Write},
};

use anyhow::Result;
use serde::{Deserialize, Serialize};
use skippy_api::serving::LocalDiskCacheOptions;
use skippy_cache::{
    L3ActivitySnapshot, L3CacheManager, L3EffectiveState, L3StateReason, StoreReconciliation,
    StoreUsage, disk_policy::DiskCacheBudget,
};
use skippy_commands::console;

#[derive(Debug, Deserialize, Serialize)]
pub(super) struct Report {
    pub(super) schema_version: u32,
    #[serde(flatten)]
    outcome: Outcome,
}

impl Report {
    pub(super) const VERSION: u32 = 1;

    pub(super) fn new(outcome: Outcome) -> Self {
        Self {
            schema_version: Self::VERSION,
            outcome,
        }
    }

    pub(super) fn status(status: Status) -> Self {
        Self::new(Outcome::Status(Box::new(status)))
    }

    pub(super) fn present(&self) -> Result<()> {
        console::present(self, |output| match &self.outcome {
            Outcome::Status(status) => status.render(output),
            Outcome::Prune {
                freed_bytes,
                used_bytes,
                target_bytes,
                target_reached,
                ..
            } => {
                writeln!(
                    output,
                    "Freed {freed_bytes} bytes from the disk KV cache. {used_bytes} bytes remain."
                )?;
                if !target_reached {
                    writeln!(
                        output,
                        "The cache could not reach the {target_bytes}-byte target with the eligible entries."
                    )?;
                }
                Ok(())
            }
            Outcome::Clear {
                freed_bytes,
                used_bytes,
                ..
            } => writeln!(
                output,
                "Freed {freed_bytes} bytes from the disk KV cache. {used_bytes} bytes remain."
            ),
        })
    }
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case", tag = "operation")]
pub(super) enum Outcome {
    Status(Box<Status>),
    Prune {
        model_identity: Option<String>,
        target_bytes: u64,
        freed_bytes: u64,
        used_bytes: u64,
        target_reached: bool,
    },
    Clear {
        model_identity: Option<String>,
        freed_bytes: u64,
        used_bytes: u64,
    },
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum State {
    #[default]
    Disabled,
    Unavailable,
    Active,
    ReadOnlyLowSpace,
    Degraded,
}

impl State {
    fn label(self) -> &'static str {
        match self {
            Self::Disabled => "disabled",
            Self::Unavailable => "unavailable",
            Self::Active => "active",
            Self::ReadOnlyLowSpace => "read-only due to low disk space",
            Self::Degraded => "degraded",
        }
    }
}

#[derive(Debug, Default, Deserialize, Serialize)]
pub(super) struct Status {
    enabled: bool,
    available: bool,
    directory: Option<String>,
    state: State,
    reason: Option<L3StateReason>,
    detail: Option<String>,
    budget_bytes: Option<u64>,
    minimum_free_bytes: Option<u64>,
    low_water_bytes: Option<u64>,
    usage: Option<StoreUsage>,
    reconciliation: Option<StoreReconciliation>,
    activity: Option<L3ActivitySnapshot>,
    models: Vec<ModelInventory>,
}

#[derive(Debug, Default, Deserialize, Serialize)]
struct ModelInventory {
    model_identity: String,
    manifests: u64,
    logical_bytes: u64,
}

impl Status {
    pub(super) fn disabled() -> Self {
        Self::default()
    }

    pub(super) fn unavailable(settings: &LocalDiskCacheOptions, detail: String) -> Self {
        Self {
            enabled: true,
            directory: Some(settings.directory.display().to_string()),
            state: State::Unavailable,
            detail: Some(detail),
            budget_bytes: match settings.budget {
                DiskCacheBudget::Fixed(bytes) => Some(bytes),
                _ => None,
            },
            minimum_free_bytes: Some(settings.minimum_free_bytes),
            ..Self::default()
        }
    }

    pub(super) fn from_manager(manager: &L3CacheManager) -> Result<Self> {
        let usage = manager.usage()?;
        let limits = manager.limits();
        let effective = manager.effective_status();
        let (state, reason) = match effective.state {
            L3EffectiveState::Degraded => (State::Degraded, effective.reason),
            _ if usage.filesystem_available_bytes <= limits.minimum_free_bytes => (
                State::ReadOnlyLowSpace,
                Some(L3StateReason::ReadOnlyLowSpace),
            ),
            L3EffectiveState::ReadOnlyLowSpace => (State::ReadOnlyLowSpace, effective.reason),
            L3EffectiveState::Active => (State::Active, None),
        };
        let mut models = BTreeMap::<String, ModelInventory>::new();
        for entry in manager.inventory()? {
            let model = models.entry(entry.model_identity).or_default();
            model.logical_bytes = model.logical_bytes.saturating_add(entry.total_bytes);
            model.manifests += 1;
        }
        Ok(Self {
            enabled: true,
            available: true,
            directory: Some(manager.root().display().to_string()),
            state,
            reason,
            detail: None,
            budget_bytes: Some(limits.budget_bytes),
            minimum_free_bytes: Some(limits.minimum_free_bytes),
            low_water_bytes: limits.low_water_bytes(),
            usage: Some(usage),
            reconciliation: Some(manager.reconciliation()),
            activity: Some(manager.activity()?),
            models: models
                .into_iter()
                .map(|(model_identity, model)| ModelInventory {
                    model_identity,
                    ..model
                })
                .collect(),
        })
    }

    fn render(&self, output: &mut dyn Write) -> io::Result<()> {
        writeln!(output, "Disk KV cache is {}.", self.state.label())?;
        if let Some(directory) = &self.directory {
            writeln!(output, "Directory  {directory}")?;
        }
        if let Some(detail) = &self.detail {
            writeln!(output, "{detail}")?;
        }
        if let Some(usage) = self.usage {
            writeln!(
                output,
                "Usage      {} bytes, {} manifests, {} unique segments",
                usage.used_bytes, usage.manifests, usage.unique_segments
            )?;
            writeln!(
                output,
                "Limits     {} bytes cache budget, {} bytes minimum filesystem free space",
                usage.budget_bytes, usage.minimum_free_bytes
            )?;
            writeln!(
                output,
                "Available  {} bytes on the filesystem",
                usage.filesystem_available_bytes
            )?;
        }
        if let Some(activity) = &self.activity {
            writeln!(
                output,
                "Activity   {} hits, {} misses, {} fills, {} writes, {} evictions, {} corrupt objects",
                activity.hits,
                activity.misses,
                activity.fills,
                activity.writes,
                activity.evictions,
                activity.corrupt_entries
            )?;
            if let Some(error) = &activity.last_error {
                writeln!(output, "Last error {error}")?;
            }
        }
        if let Some(reconciliation) = self.reconciliation {
            writeln!(
                output,
                "Recovery   {} temporary files removed, {} manifests quarantined, {} prefix links removed, {} orphan bytes removed",
                reconciliation.removed_temporary_files,
                reconciliation.quarantined_manifests,
                reconciliation.removed_prefix_links,
                reconciliation.removed_orphan_bytes
            )?;
        }
        for model in &self.models {
            writeln!(
                output,
                "Model {:?} has {} manifests and {} logical bytes.",
                model.model_identity, model.manifests, model.logical_bytes
            )?;
        }
        Ok(())
    }
}
