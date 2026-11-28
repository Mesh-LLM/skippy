//! Native radix prefixes own shared KV references and bounded recurrent host state.
use skippy_protocol::StageKvCacheConfig;
use std::num::NonZeroU64;

const DEFAULT_CHECKPOINT_BYTES: u64 = 1024 * 1024 * 1024;

pub(super) fn checkpoint_budget(cache: &StageKvCacheConfig) -> u64 {
    cache
        .l1_checkpoint_max_bytes
        .map_or(DEFAULT_CHECKPOINT_BYTES, NonZeroU64::get)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checkpoint_ram_budget_is_independent_of_l2_and_attention_estimates() {
        let cache = StageKvCacheConfig {
            max_bytes: 32,
            exact_max_bytes: Some(64),
            l2_max_bytes: 256,
            l1_checkpoint_max_bytes: None,
            mode: skippy_protocol::StageKvCacheMode::LookupRecord,
            max_entries: 64,
            codec: Default::default(),
            min_tokens: 64,
            shared_prefix_stride_tokens: 128,
            shared_prefix_record_limit: 2,
            prefill_admission: Default::default(),
            retention: Default::default(),
        };
        assert_eq!(checkpoint_budget(&cache), DEFAULT_CHECKPOINT_BYTES);
        assert_eq!(
            checkpoint_budget(&StageKvCacheConfig {
                l1_checkpoint_max_bytes: NonZeroU64::new(512),
                ..cache
            }),
            512
        );
    }
}
