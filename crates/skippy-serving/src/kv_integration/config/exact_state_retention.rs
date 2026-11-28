//! Byte-pressure eviction for the exact-state catalog.
use std::sync::{Mutex, PoisonError};

use anyhow::Result;
use skippy_cache::{CacheBlobStore, UnifiedRadixCache};

use super::{ExactStateByteLimits, RadixExactEntry};
use crate::kv_integration::RadixResidentEntry;

pub(super) fn enforce_exact_state_limits(
    radix: &Mutex<UnifiedRadixCache<RadixResidentEntry, RadixExactEntry>>,
    blobs: &Mutex<CacheBlobStore>,
    limits: ExactStateByteLimits,
    max_entries: usize,
) -> Result<()> {
    // Exact snapshots are indivisible, so one long conversation's snapshot can
    // legitimately exceed the soft cap. Hold one conversation per lane past
    // the soft cap (never more than the configured entry limit) so concurrent
    // sessions on a stage stop evicting each other. The floor counts each
    // conversation's latest continuation: a checkpoint that a single longer
    // retained checkpoint extends is evicted first.
    let retention_floor = limits.working_set_entries.min(max_entries);
    let conversations = RetentionFloor::Conversations(retention_floor.max(1));
    evict_exact_entries_over(radix, blobs, limits.soft_bytes, conversations)?;
    // Zero means unbounded: no hard pass at all.
    if limits.hard_bytes > 0 {
        // The hard limit is a strict ceiling, derived or explicit: only one
        // indivisible snapshot may exceed it. Now that the stage budget counts
        // recurrent state, the derived ceiling of eight budgets holds a
        // snapshot per lane until their attention KV together passes four
        // context windows.
        evict_exact_entries_over(
            radix,
            blobs,
            limits.hard_bytes,
            RetentionFloor::SingleSnapshot,
        )?;
        let retained = blobs
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .physical_bytes();
        if retained > limits.hard_bytes && exact_state_over_limit_warning_due() {
            // Observable signal that the catalog stays above the hard limit,
            // because one snapshot alone exceeds it or in-flight restores
            // hold the others. Raise the exact-state budget when this repeats.
            let _ = skippy_events::diagnostics::emit(
                skippy_events::diagnostics::ServingDiagnostic::Warning {
                    message: "exact-state catalog retained more than its hard byte limit"
                        .to_string(),
                    context: Some(format!(
                        "retained_bytes={retained} soft_bytes={} hard_bytes={} explicit={} floor_entries={retention_floor}",
                        limits.soft_bytes, limits.hard_bytes, limits.hard_is_explicit
                    )),
                },
            );
        }
    }
    Ok(())
}

/// At most one over-limit warning per minute per process.
fn exact_state_over_limit_warning_due() -> bool {
    use std::sync::atomic::{AtomicU64, Ordering};
    static LAST_WARNED_SECS: AtomicU64 = AtomicU64::new(0);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.as_secs());
    let last = LAST_WARNED_SECS.load(Ordering::Relaxed);
    (last == 0 || now.saturating_sub(last) >= 60)
        && LAST_WARNED_SECS
            .compare_exchange(last, now.max(1), Ordering::Relaxed, Ordering::Relaxed)
            .is_ok()
}

#[derive(Clone, Copy)]
enum RetentionFloor {
    Conversations(usize),
    SingleSnapshot,
}

/// Evict superseded checkpoints first, then evict by recency above the floor.
fn evict_exact_entries_over(
    radix: &Mutex<UnifiedRadixCache<RadixResidentEntry, RadixExactEntry>>,
    blobs: &Mutex<CacheBlobStore>,
    limit_bytes: u64,
    floor: RetentionFloor,
) -> Result<()> {
    if limit_bytes == 0 {
        return Ok(());
    }
    loop {
        let over_bytes = blobs
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .physical_bytes()
            > limit_bytes;
        if !over_bytes {
            break;
        }
        let evicted = {
            let mut radix = radix.lock().unwrap_or_else(PoisonError::into_inner);
            radix.evict_lru_superseded_recurrent().or_else(|| {
                let above_floor = match floor {
                    RetentionFloor::Conversations(minimum) => {
                        radix.recurrent_leaf_entries() > minimum
                    }
                    RetentionFloor::SingleSnapshot => radix.stats().recurrent_entries > 1,
                };
                above_floor
                    .then(|| {
                        radix.evict_recurrent_by_retention(RadixExactEntry::retention_priority)
                    })
                    .flatten()
            })
        };
        let Some(evicted) = evicted else {
            break;
        };
        evicted
            .value
            .payload
            .release_from(&mut blobs.lock().unwrap_or_else(PoisonError::into_inner))?;
    }
    Ok(())
}
