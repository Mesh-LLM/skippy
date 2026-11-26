//! Native memory determines whether prefix matches may truncate attention state.
use super::*;
use crate::kv_integration::RadixExactEntry;
use skippy_cache::RadixEvictionCandidate;
use skippy_scheduler::{EvictableCacheEntry, rank_eviction_candidates};

/// Keep reused prefixes through a one-pass scan. An unreferenced entry remains
/// reclaimable under pressure, and stops getting preference after two cache
/// turnovers without another successful restore.
pub(super) fn rank_resident_candidates<'a>(
    candidates: impl Iterator<Item = &'a RadixEvictionCandidate<RadixResidentEntry>>,
    max_entries: usize,
) -> impl Iterator<Item = EvictableCacheEntry> {
    let reuse_window = u64::try_from(max_entries)
        .unwrap_or(u64::MAX)
        .saturating_mul(2);
    let mut unreused = Vec::new();
    let mut reused = Vec::new();
    for candidate in candidates {
        let ranked = EvictableCacheEntry {
            id: candidate.value.seq_id.to_string(),
            units: candidate
                .value
                .token_count
                .max(candidate.tokens.len() as u64),
            recompute_cost: candidate.value.recompute_cost,
            last_used: candidate.last_used,
        };
        if candidate
            .insertions_since_reuse
            .is_some_and(|age| age < reuse_window)
        {
            reused.push(ranked);
        } else {
            unreused.push(ranked);
        }
    }
    rank_eviction_candidates(&mut unreused);
    rank_eviction_candidates(&mut reused);
    unreused.into_iter().chain(reused)
}

impl KvStageIntegration {
    pub(crate) fn retains_recurrent_checkpoints(&self) -> bool {
        self.payload_is_exact_state() || self.resident_requires_checkpoint()
    }

    pub(crate) fn resident_requires_checkpoint(&self) -> bool {
        self.loaded_memory_cache
            .is_some_and(|caps| caps.resident_checkpoint)
    }

    pub(crate) fn peek_native_prefix(
        &self,
        radix: &skippy_cache::UnifiedRadixCache<RadixResidentEntry, RadixExactEntry>,
        namespace: &str,
        tokens: &[i32],
    ) -> Option<skippy_cache::RadixMatch<RadixResidentEntry>> {
        if self.resident_requires_checkpoint() {
            radix.peek_resident_checkpoint(namespace, tokens)
        } else {
            radix.peek_resident(namespace, tokens)
        }
    }

    /// What the cache held for identities whose lookup restored nothing.
    /// Reads without changing recency or references.
    pub(crate) fn diagnose_resident_miss(
        &self,
        identities: &[PrefillKvIdentity],
    ) -> ResidentMissDiagnosis {
        if !self.should_lookup() || self.payload != StagePrefixCachePayload::ResidentKv {
            return ResidentMissDiagnosis {
                reason: ResidentMissReason::LookupOff,
                resident_entries: 0,
                shared_prefix_tokens: 0,
                stored_prefix_tokens: 0,
                checkpoint_prefix_tokens: 0,
            };
        }
        let radix = self
            .radix
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let resident_entries = radix.stats().resident_entries;
        let (shared_prefix_tokens, stored_prefix_tokens) = identities
            .iter()
            .filter_map(|identity| radix.peek_resident(&identity.namespace, &identity.token_ids))
            .map(|shared| (shared.matched_tokens, shared.stored_tokens.len()))
            .max()
            .unwrap_or_default();
        let checkpoint_prefix_tokens = identities
            .iter()
            .filter_map(|identity| {
                self.peek_native_prefix(&radix, &identity.namespace, &identity.token_ids)
            })
            .map(|checkpoint| checkpoint.matched_tokens)
            .max()
            .unwrap_or_default();
        ResidentMissDiagnosis {
            reason: ResidentMissReason::classify(
                resident_entries,
                shared_prefix_tokens,
                checkpoint_prefix_tokens,
                self.meets_shared_prefix_min_tokens(checkpoint_prefix_tokens),
            ),
            resident_entries,
            shared_prefix_tokens,
            stored_prefix_tokens,
            checkpoint_prefix_tokens,
        }
    }
}
