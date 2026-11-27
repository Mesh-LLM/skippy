//! What a direct iteration does. The chunks of one prompt carry the place the
//! prompt took when it began, so a batch policy can still order two prompts
//! while one of them is between chunks and has nothing queued.

use skippy_runtime::IterationBatchPhase;
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT_PROMPT: AtomicU64 = AtomicU64::new(0);

/// A prompt's place in the order prompts began in. Taken once per prompt;
/// no two are equal.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct PromptStart(u64);

impl PromptStart {
    /// A place after every one taken before it.
    pub(crate) fn next() -> Self {
        Self(NEXT_PROMPT.fetch_add(1, Ordering::Relaxed))
    }

    #[cfg(test)]
    pub(super) fn nth(place: u64) -> Self {
        Self(place)
    }
}

/// A chunk of a prompt, or a generated token.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum IterationWork {
    Prompt(PromptStart),
    Generation,
}

impl IterationWork {
    /// The work of an iteration that is all of it: a prefill is a whole
    /// prompt, beginning now.
    pub(crate) fn unchunked(phase: IterationBatchPhase) -> Self {
        match phase {
            IterationBatchPhase::Prefill => Self::Prompt(PromptStart::next()),
            IterationBatchPhase::Decode => Self::Generation,
        }
    }

    pub(crate) const fn phase(self) -> IterationBatchPhase {
        match self {
            Self::Prompt(_) => IterationBatchPhase::Prefill,
            Self::Generation => IterationBatchPhase::Decode,
        }
    }

    pub(crate) const fn prompt_start(self) -> Option<PromptStart> {
        match self {
            Self::Prompt(start) => Some(start),
            Self::Generation => None,
        }
    }
}
