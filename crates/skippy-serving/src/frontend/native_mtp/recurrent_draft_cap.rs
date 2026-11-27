use skippy_runtime::ModelStateKind;

use crate::frontend::SpeculativeDecodeConfig;

const RECURRENT_NATIVE_MTP_MAX_DRAFT_TOKENS: usize = 2;

/// Keep recurrent speculation bounded at two drafts; deeper windows need
/// separate qualification. Rollback planning includes the verification anchor,
/// and windows beyond native retention use checkpoint/replay fallback.
pub(in crate::frontend) fn native_mtp_draft_cap(
    requested: usize,
    state_kind: Option<ModelStateKind>,
) -> usize {
    match state_kind {
        Some(ModelStateKind::Recurrent | ModelStateKind::Hybrid) => {
            requested.min(RECURRENT_NATIVE_MTP_MAX_DRAFT_TOKENS)
        }
        _ => requested,
    }
}

pub(in crate::frontend) fn apply_native_mtp_draft_cap(
    speculative: &mut SpeculativeDecodeConfig,
    state_kind: Option<ModelStateKind>,
) {
    let requested = speculative.native_mtp.max_draft_tokens;
    let capped = native_mtp_draft_cap(requested, state_kind);
    if capped == requested {
        return;
    }
    speculative.native_mtp.max_draft_tokens = capped;
    speculative.native_mtp.min_draft_tokens = speculative.native_mtp.min_draft_tokens.min(capped);
    if speculative.native_mtp.enabled {
        let _ = skippy_events::diagnostics::emit(
            skippy_events::diagnostics::ServingDiagnostic::Warning {
                message: format!(
                    "native MTP draft tokens capped from {requested} to {capped}: this model has \
                     recurrent state; native MTP is currently limited to two draft tokens \
                     for hybrid and recurrent models"
                ),
                context: Some(format!("model_state_kind={state_kind:?}")),
            },
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hybrid_and_recurrent_models_cap_native_mtp_to_two_drafts() {
        for kind in [ModelStateKind::Hybrid, ModelStateKind::Recurrent] {
            for (requested, expected) in [(0, 0), (1, 1), (2, 2), (3, 2), (usize::MAX, 2)] {
                assert_eq!(
                    native_mtp_draft_cap(requested, Some(kind)),
                    expected,
                    "{kind:?}"
                );
            }
        }
    }

    #[test]
    fn dense_models_keep_the_requested_native_mtp_depth() {
        assert_eq!(native_mtp_draft_cap(3, Some(ModelStateKind::Dense)), 3);
        assert_eq!(native_mtp_draft_cap(1, Some(ModelStateKind::Dense)), 1);
        assert_eq!(native_mtp_draft_cap(3, None), 3);
    }

    #[test]
    fn capping_a_hybrid_plan_keeps_min_within_max() {
        let mut speculative = SpeculativeDecodeConfig::default();
        speculative.native_mtp.enabled = true;
        speculative.native_mtp.max_draft_tokens = 4;
        speculative.native_mtp.min_draft_tokens = 3;

        apply_native_mtp_draft_cap(&mut speculative, Some(ModelStateKind::Hybrid));

        assert_eq!(speculative.native_mtp.max_draft_tokens, 2);
        assert_eq!(speculative.native_mtp.min_draft_tokens, 2);
        speculative.validate().expect("capped plan stays valid");
        assert_eq!(speculative.recurrent_rollback_tokens(None, None), 5);
        speculative.verify_window.max_tokens = 2;
        assert_eq!(speculative.recurrent_rollback_tokens(None, None), 3);
    }
}
