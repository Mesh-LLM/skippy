//! Exact-state snapshot retention limits, including indivisible payloads.
use super::super::ExactStateByteLimits;

// Exact-state snapshots are indivisible, so a single long conversation's
// snapshot can exceed the soft cap. Falling to one entry turns the catalog
// into a single-entry cache, where two concurrent sessions on the same stage
// evict each other on every request. The soft cap keeps one conversation per
// lane, and at least this many.
pub(super) const EXACT_STATE_MIN_RETAINED_ENTRIES: usize = 4;

/// One conversation per lane, at least the minimum, never past the entry limit.
pub(super) fn exact_state_working_set(lane_count: u32, max_entries: usize) -> usize {
    EXACT_STATE_MIN_RETAINED_ENTRIES
        .max(usize::try_from(lane_count).unwrap_or(usize::MAX))
        .min(max_entries)
}

// That retention floor is not allowed to grow without bound. Once the catalog
// crosses this multiple of the soft cap it evicts again, down to the single
// entry that keeps exact prefix reuse alive for the stage. That indivisible
// entry may itself exceed the limit.
const EXACT_STATE_HARD_CAP_MULTIPLE: u64 = 8;

/// Resolves the exact-state byte budget from the stage cache cap.
///
/// `configured_max_bytes` is the stage budget, used as the soft cap. Unless an
/// operator sets it, it covers half a context window of the stage's attention
/// KV and one recurrent snapshot per lane, so a long conversation's snapshot
/// can still exceed it. The hard limit defaults to a multiple of it so the
/// working set stays bounded. One indivisible snapshot may remain above that
/// limit. `configured_exact_max_bytes` wins over the legacy environment
/// override when provided; zero means unbounded on either surface.
pub(super) fn exact_state_byte_limits(
    configured_max_bytes: u64,
    configured_exact_max_bytes: Option<u64>,
    override_max_bytes: Option<&str>,
    working_set_entries: usize,
) -> ExactStateByteLimits {
    let explicit = configured_exact_max_bytes
        .or_else(|| override_max_bytes.and_then(|value| value.trim().parse::<u64>().ok()));
    ExactStateByteLimits {
        soft_bytes: configured_max_bytes,
        hard_bytes: explicit
            .unwrap_or_else(|| configured_max_bytes.saturating_mul(EXACT_STATE_HARD_CAP_MULTIPLE)),
        hard_is_explicit: explicit.is_some(),
        working_set_entries,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn limits(soft_bytes: u64, hard_bytes: u64) -> ExactStateByteLimits {
        ExactStateByteLimits {
            soft_bytes,
            hard_bytes,
            hard_is_explicit: false,
            working_set_entries: EXACT_STATE_MIN_RETAINED_ENTRIES,
        }
    }
    fn explicit(soft_bytes: u64, hard_bytes: u64) -> ExactStateByteLimits {
        ExactStateByteLimits {
            hard_is_explicit: true,
            ..limits(soft_bytes, hard_bytes)
        }
    }
    /// The limits for a stage with the smallest working set.
    fn resolve(
        configured_max_bytes: u64,
        configured_exact_max_bytes: Option<u64>,
        override_max_bytes: Option<&str>,
    ) -> ExactStateByteLimits {
        exact_state_byte_limits(
            configured_max_bytes,
            configured_exact_max_bytes,
            override_max_bytes,
            EXACT_STATE_MIN_RETAINED_ENTRIES,
        )
    }

    #[test]
    fn the_working_set_holds_one_conversation_per_lane_within_the_entry_limit() {
        assert_eq!(
            exact_state_working_set(1, 64),
            EXACT_STATE_MIN_RETAINED_ENTRIES
        );
        assert_eq!(exact_state_working_set(16, 64), 16);
        assert_eq!(exact_state_working_set(16, 8), 8);
        assert_eq!(exact_state_working_set(1, 2), 2);
    }

    #[test]
    fn exact_state_byte_limits_scale_the_ceiling_off_the_stage_budget() {
        assert_eq!(
            resolve(1_024, None, None),
            limits(1_024, 1_024 * EXACT_STATE_HARD_CAP_MULTIPLE)
        );
        // An unset stage budget stays unbounded, as it was before.
        assert_eq!(resolve(0, None, None), limits(0, 0));
    }

    #[test]
    fn exact_state_byte_limits_honour_the_operator_override() {
        assert_eq!(resolve(1_024, None, Some(" 4096 ")), explicit(1_024, 4_096));
        assert_eq!(resolve(1_024, None, Some("0")), explicit(1_024, 0));
        // A malformed override must not silently disable the ceiling.
        assert_eq!(
            resolve(1_024, None, Some("not-a-number")),
            limits(1_024, 1_024 * EXACT_STATE_HARD_CAP_MULTIPLE)
        );
    }

    #[test]
    fn configured_limit_overrides_the_legacy_environment_including_unbounded() {
        assert_eq!(
            resolve(1024, Some(2048), Some("4096")),
            explicit(1024, 2048)
        );
        assert_eq!(resolve(1024, Some(0), Some("4096")), explicit(1024, 0));
    }
}
