//! Compare token metadata before choosing a warm alias or loading durable bytes.
use super::*;
use std::{
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};

pub(super) struct RestoreCandidate<'a> {
    pub(super) identity: &'a PrefillKvIdentity,
    pub(super) location: Option<&'a skippy_cache::L3Location>,
    pub(super) fill: DurableFill<'a>,
}

pub(super) struct DurableRestoreLookup<'a> {
    pub(super) started: Instant,
    pub(super) location: &'a skippy_cache::L3Location,
    pub(super) fill: DurableFill<'a>,
}

#[derive(Clone, Copy)]
pub(super) enum DurableFill<'a> {
    Inline,
    Staged(&'a StagedL3Restore),
    Skip,
}

struct StagingLease(Arc<AtomicBool>);

impl Drop for StagingLease {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

pub(super) struct StagedL3Restore {
    pub(super) fill: skippy_cache::L3Fill,
    pub(super) elapsed: Duration,
    _lease: StagingLease,
}

/// Durable metadata resolved before entering a scheduled runtime operation.
/// A missing location is a completed lookup, not permission to probe again.
pub(crate) struct PreparedExactStateRestore {
    identities: Vec<PrefillKvIdentity>,
    locations: Vec<Option<skippy_cache::L3Location>>,
    staged: Option<StagedL3Restore>,
    allow_inline_fill: bool,
}

impl PreparedExactStateRestore {
    /// The exact identity order paired with these completed durable probes.
    pub(crate) fn identities(&self) -> &[PrefillKvIdentity] {
        &self.identities
    }

    fn fill_for(&self, location: Option<&skippy_cache::L3Location>) -> DurableFill<'_> {
        if self.allow_inline_fill {
            return DurableFill::Inline;
        }
        match (&self.staged, location) {
            (Some(staged), Some(location))
                if staged.fill.manifest.payload_digest == location.manifest_key =>
            {
                DurableFill::Staged(staged)
            }
            _ => DurableFill::Skip,
        }
    }
}

impl KvStageIntegration {
    /// Locate durable candidates without holding the scheduler's runtime lock.
    /// Resident eligibility is deliberately checked later, during restore.
    pub(crate) fn prepare_exact_state_restore(
        &self,
        identities: &[PrefillKvIdentity],
        shared_durable_fallback: bool,
    ) -> PreparedExactStateRestore {
        let locations = identities
            .iter()
            .map(|identity| {
                if !self.should_lookup() || self.exact_state_payload().is_none() {
                    return None;
                }
                let l3 = self.l3.as_ref()?;
                let tokens = if shared_durable_fallback {
                    self.durable_exact_lookup_token_ids(&identity.token_ids)
                } else {
                    &identity.token_ids
                };
                // Corrupt/unavailable storage remains a cache miss. The
                // prepared None must never trigger a retry under runtime lock.
                let location = l3
                    .locate_longest(&identity.namespace, tokens, MAX_PREFIX_PROBES)
                    .ok()
                    .flatten();
                super::super::tier_metrics::record_lookup(
                    crate::telemetry::log_events::KvTier::L3,
                    location.as_ref().map(|location| location.token_count),
                );
                location
            })
            .collect();
        PreparedExactStateRestore {
            identities: identities.to_vec(),
            locations,
            staged: None,
            allow_inline_fill: true,
        }
    }

    /// Stage one disk candidate before the standalone request takes the
    /// runtime. A busy reader, expired budget or failed load is a cache miss.
    pub(crate) fn prepare_standalone_restore(
        &self,
        identities: &[PrefillKvIdentity],
        deadline: Instant,
        estimate_prefill_ms: impl FnOnce(usize) -> Option<f64>,
    ) -> PreparedExactStateRestore {
        let mut prepared = self.prepare_exact_state_restore(identities, false);
        prepared.allow_inline_fill = false;
        let candidate = self
            .ordered_restore_candidates(&prepared)
            .into_iter()
            .find(|candidate| {
                candidate
                    .location
                    .is_some_and(|location| preflight_l3_kv_location(location).is_ok())
            });
        if let Some(candidate) = candidate
            && let Some(location) = candidate.location
            && !self
                .l2
                .as_ref()
                .is_some_and(|tier| tier.contains_location(candidate.identity, location))
            && let Some(l3) = &self.l3
        {
            let resident = self.longest_resident(&prepared.identities);
            let saved = usize::try_from(location.token_count.saturating_sub(resident)).unwrap_or(0);
            let recompute_ms = estimate_prefill_ms(saved).filter(|ms| ms.is_finite() && *ms > 0.0);
            let wait = recompute_ms.map_or(Duration::from_secs(1), |ms| {
                Duration::from_secs_f64(ms.min(1000.0) / 1000.0)
            });
            let available = wait.min(deadline.saturating_duration_since(Instant::now()));
            if !available.is_zero() {
                let staged = self.stage_l3_fill(l3.clone(), location.clone(), available);
                if staged.is_some()
                    && let Some(sender) = self
                        .exact_state_record_worker
                        .durable_sender
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner)
                        .as_ref()
                {
                    sender.remember_loaded(
                        &candidate.identity.namespace,
                        &candidate.identity.token_ids,
                        location,
                    );
                }
                prepared.staged = staged;
            }
        }
        prepared
    }

    fn stage_l3_fill(
        &self,
        tier: Arc<skippy_cache::L3Tier>,
        location: skippy_cache::L3Location,
        wait: Duration,
    ) -> Option<StagedL3Restore> {
        self.l3_staging
            .compare_exchange(false, true, Ordering::Acquire, Ordering::Relaxed)
            .ok()?;
        let lease = StagingLease(self.l3_staging.clone());
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        std::thread::Builder::new()
            .name("skippy-l3-read".into())
            .spawn(move || {
                let start = Instant::now();
                if let Ok(fill) =
                    tier.load_bounded(&location, super::super::EXACT_STATE_RECORD_QUEUE_BYTES)
                {
                    let _ = tx.send(StagedL3Restore {
                        fill,
                        elapsed: start.elapsed(),
                        _lease: lease,
                    });
                }
            })
            .ok()?;
        rx.recv_timeout(wait).ok()
    }

    fn longest_resident(&self, identities: &[PrefillKvIdentity]) -> u64 {
        identities
            .iter()
            .filter_map(|identity| self.probe_resident_prefix(identity))
            .map(|hit| hit.token_count as u64)
            .max()
            .unwrap_or(0)
    }

    pub(super) fn ordered_restore_candidates<'a>(
        &self,
        prepared: &'a PreparedExactStateRestore,
    ) -> Vec<RestoreCandidate<'a>> {
        if !self.records_resident_prefixes() {
            return prepared
                .identities
                .iter()
                .zip(&prepared.locations)
                .map(|(identity, location)| RestoreCandidate {
                    identity,
                    location: location.as_ref(),
                    fill: prepared.fill_for(location.as_ref()),
                })
                .collect();
        }
        let resident = self.longest_resident(&prepared.identities);
        let mut durable = prepared
            .identities
            .iter()
            .zip(&prepared.locations)
            .filter_map(|(identity, location)| {
                let location = location.as_ref()?;
                preflight_l3_kv_location(location).ok()?;
                (location.token_count > resident).then_some((
                    location.token_count,
                    RestoreCandidate {
                        identity,
                        location: Some(location),
                        fill: prepared.fill_for(Some(location)),
                    },
                ))
            })
            .collect::<Vec<_>>();
        // Never load a candidate shorter than the native fallback. Ties stay
        // in shared memory. Actual fill still owns descriptor/cost admission.
        durable.sort_by_key(|(count, _)| std::cmp::Reverse(*count));
        durable
            .into_iter()
            .map(|(_, candidate)| candidate)
            .collect()
    }
}

#[cfg(test)]
#[path = "resident_preference/tests.rs"]
mod tests;
