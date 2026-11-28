//! The parts an exact checkpoint of one stage carries.

use crate::types::MemoryCacheCapabilities;

/// The parts an exact checkpoint of one stage carries, read from the loaded
/// stage rather than its model family: a hybrid model's stage that holds no
/// attention layers is checkpointed by its recurrent state alone.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CheckpointShape {
    /// A KV page alone.
    Attention,
    /// The recurrent state alone.
    Recurrent,
    /// A KV page and the recurrent state that ends where it does.
    AttentionAndRecurrent,
}

impl CheckpointShape {
    pub(crate) fn from_native_parts(parts: u32) -> Option<Self> {
        Self::from_parts(
            parts & skippy_ffi::CHECKPOINT_PART_KV_PAGE != 0,
            parts & skippy_ffi::CHECKPOINT_PART_RECURRENT != 0,
        )
    }

    /// The shape the resident cache keeps for a stage: its attention cells
    /// where it holds them, and a checkpoint where it holds recurrent state.
    pub fn of_resident(capabilities: MemoryCacheCapabilities) -> Option<Self> {
        if !capabilities.resident {
            return None;
        }
        Self::from_parts(
            capabilities.resident_attention,
            capabilities.resident_checkpoint,
        )
    }

    fn from_parts(page: bool, recurrent: bool) -> Option<Self> {
        match (page, recurrent) {
            (true, false) => Some(Self::Attention),
            (false, true) => Some(Self::Recurrent),
            (true, true) => Some(Self::AttentionAndRecurrent),
            (false, false) => None,
        }
    }

    pub fn has_page(self) -> bool {
        !matches!(self, Self::Recurrent)
    }

    pub fn has_recurrent(self) -> bool {
        !matches!(self, Self::Attention)
    }

    /// Whether exported components hold every part of the shape: a described
    /// KV page where the stage keeps attention, and recurrent bytes where it
    /// keeps recurrent state.
    pub fn is_covered_by(self, kv_bytes: u64, recurrent_bytes: u64, has_descriptor: bool) -> bool {
        (!self.has_page() || (kv_bytes > 0 && has_descriptor))
            && (!self.has_recurrent() || recurrent_bytes > 0)
    }

    /// The cache payload kind that stores the checkpoint.
    pub fn payload_kind(self) -> skippy_cache::ExactStatePayloadKind {
        match self {
            Self::Recurrent => skippy_cache::ExactStatePayloadKind::RecurrentOnly,
            Self::Attention | Self::AttentionAndRecurrent => {
                skippy_cache::ExactStatePayloadKind::KvRecurrent
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use skippy_cache::ExactStatePayloadKind;

    fn resident(attention: bool, checkpoint: bool) -> MemoryCacheCapabilities {
        MemoryCacheCapabilities {
            resident: true,
            resident_attention: attention,
            resident_checkpoint: checkpoint,
            ..MemoryCacheCapabilities::default()
        }
    }

    #[test]
    fn the_native_parts_name_the_shape() {
        let page = skippy_ffi::CHECKPOINT_PART_KV_PAGE;
        let recurrent = skippy_ffi::CHECKPOINT_PART_RECURRENT;
        assert_eq!(CheckpointShape::from_native_parts(0), None);
        assert_eq!(
            CheckpointShape::from_native_parts(page),
            Some(CheckpointShape::Attention)
        );
        assert_eq!(
            CheckpointShape::from_native_parts(recurrent),
            Some(CheckpointShape::Recurrent)
        );
        assert_eq!(
            CheckpointShape::from_native_parts(page | recurrent),
            Some(CheckpointShape::AttentionAndRecurrent)
        );
    }

    #[test]
    fn a_stage_without_resident_memory_has_no_resident_shape() {
        let capabilities = MemoryCacheCapabilities {
            resident: false,
            ..resident(true, true)
        };
        assert_eq!(CheckpointShape::of_resident(capabilities), None);
        assert_eq!(CheckpointShape::of_resident(resident(false, false)), None);
    }

    #[test]
    fn a_hybrid_checkpoint_needs_its_page_and_its_recurrent_state() {
        let shape = CheckpointShape::of_resident(resident(true, true)).unwrap();
        assert_eq!(shape, CheckpointShape::AttentionAndRecurrent);
        assert!(shape.is_covered_by(1, 1, true));
        assert!(!shape.is_covered_by(0, 1, true));
        assert!(!shape.is_covered_by(1, 1, false));
        assert!(!shape.is_covered_by(1, 0, true));
        assert_eq!(shape.payload_kind(), ExactStatePayloadKind::KvRecurrent);
    }

    #[test]
    fn a_stage_without_attention_layers_is_checkpointed_by_recurrent_state_alone() {
        let shape = CheckpointShape::of_resident(resident(false, true)).unwrap();
        assert_eq!(shape, CheckpointShape::Recurrent);
        assert!(shape.is_covered_by(0, 1, false));
        assert!(!shape.is_covered_by(0, 0, false));
        assert_eq!(shape.payload_kind(), ExactStatePayloadKind::RecurrentOnly);
    }

    #[test]
    fn an_attention_checkpoint_needs_only_a_described_page() {
        let shape = CheckpointShape::of_resident(resident(true, false)).unwrap();
        assert_eq!(shape, CheckpointShape::Attention);
        assert!(shape.is_covered_by(1, 0, true));
        assert!(!shape.is_covered_by(1, 0, false));
    }
}
