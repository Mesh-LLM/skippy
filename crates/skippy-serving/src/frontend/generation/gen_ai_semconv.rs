//! OpenTelemetry GenAI semantic-convention attributes for a finished
//! generation, so GenAI-aware trace backends can type its span and read its
//! model and token use. Counts and identifiers only: prompts and completions
//! stay out of telemetry.

use std::collections::BTreeMap;

use serde_json::{Value, json};
use skippy_metrics::semconv;

use super::InferenceGenerationIds;
use crate::generation_stats::RequestShape;

/// No well-known `gen_ai.provider.name` covers a self-hosted server.
const PROVIDER_NAME: &str = "skippy";

/// The `gen_ai.operation.name` values the generation path serves.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(in crate::frontend) enum GenAiOperation {
    Chat,
    TextCompletion,
}

impl GenAiOperation {
    pub(in crate::frontend) fn of(shape: RequestShape) -> Self {
        if shape.chat {
            Self::Chat
        } else {
            Self::TextCompletion
        }
    }

    const fn as_str(self) -> &'static str {
        match self {
            Self::Chat => "chat",
            Self::TextCompletion => "text_completion",
        }
    }
}

/// Token counts as the response's `usage` reports them.
pub(in crate::frontend) struct GenAiUsage {
    pub(in crate::frontend) input_tokens: u32,
    pub(in crate::frontend) output_tokens: u32,
    pub(in crate::frontend) cache_read_input_tokens: u32,
}

/// Attributes for the one span per generation that carries its usage. Trace
/// backends total usage across spans, so a second span carrying these would
/// count the request twice.
pub(in crate::frontend) fn gen_ai_attrs(
    operation: GenAiOperation,
    model_id: &str,
    usage: &GenAiUsage,
    ids: &InferenceGenerationIds,
) -> BTreeMap<String, Value> {
    let mut attrs = BTreeMap::from([
        (
            semconv::GEN_AI_OPERATION_NAME.to_string(),
            json!(operation.as_str()),
        ),
        (
            semconv::GEN_AI_PROVIDER_NAME.to_string(),
            json!(PROVIDER_NAME),
        ),
        (semconv::GEN_AI_RESPONSE_MODEL.to_string(), json!(model_id)),
        (
            semconv::GEN_AI_USAGE_INPUT_TOKENS.to_string(),
            json!(usage.input_tokens),
        ),
        (
            semconv::GEN_AI_USAGE_OUTPUT_TOKENS.to_string(),
            json!(usage.output_tokens),
        ),
        (
            semconv::GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS.to_string(),
            json!(usage.cache_read_input_tokens),
        ),
    ]);
    // Only a trusted session binds requests to one native KV session, so only
    // it groups them, by the hashed id `skippy.session_id` already carries
    // rather than the client's own string.
    if ids.agent_session_trusted {
        attrs.insert(
            semconv::SESSION_ID.to_string(),
            json!(ids.session_id_string()),
        );
    }
    attrs
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::frontend::generation::InferenceCacheHints;

    fn ids(trusted: bool) -> InferenceGenerationIds {
        InferenceGenerationIds::new_with_trust(
            InferenceCacheHints::default(),
            Some("conversation-7"),
            trusted,
            None,
        )
    }

    fn usage() -> GenAiUsage {
        GenAiUsage {
            input_tokens: 120,
            output_tokens: 30,
            cache_read_input_tokens: 96,
        }
    }

    #[test]
    fn a_generation_reports_its_operation_model_and_usage() {
        let shape = RequestShape {
            chat: true,
            ..RequestShape::default()
        };
        let attrs = gen_ai_attrs(
            GenAiOperation::of(shape),
            "org/model:Q4",
            &usage(),
            &ids(false),
        );

        assert_eq!(attrs[semconv::GEN_AI_OPERATION_NAME], json!("chat"));
        assert_eq!(attrs[semconv::GEN_AI_RESPONSE_MODEL], json!("org/model:Q4"));
        assert_eq!(attrs[semconv::GEN_AI_USAGE_INPUT_TOKENS], json!(120));
        assert_eq!(attrs[semconv::GEN_AI_USAGE_OUTPUT_TOKENS], json!(30));
        assert_eq!(
            attrs[semconv::GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS],
            json!(96)
        );
        assert_eq!(
            GenAiOperation::of(RequestShape::default()).as_str(),
            "text_completion"
        );
    }

    #[test]
    fn only_a_trusted_session_groups_requests() {
        let untrusted = gen_ai_attrs(GenAiOperation::Chat, "m", &usage(), &ids(false));
        assert!(!untrusted.contains_key(semconv::SESSION_ID));

        let trusted_ids = ids(true);
        let trusted = gen_ai_attrs(GenAiOperation::Chat, "m", &usage(), &trusted_ids);
        assert_eq!(
            trusted[semconv::SESSION_ID],
            json!(trusted_ids.session_id_string())
        );
        assert_ne!(trusted[semconv::SESSION_ID], json!("conversation-7"));
    }
}
