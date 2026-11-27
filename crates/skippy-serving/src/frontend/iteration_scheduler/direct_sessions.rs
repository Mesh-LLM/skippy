//! The sessions that hand the worker direct iterations one step at a time:
//! prompts in their chunked prefill and requests generating tokens. Each
//! counts itself in with a guard, so the worker waits for these rather than
//! for every session the runtime holds, and a prompt can size its chunk by
//! the generated tokens that will share its step.

use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

#[derive(Debug, Default)]
pub(crate) struct DirectSessions {
    prefilling: AtomicUsize,
    generating: AtomicUsize,
}

impl DirectSessions {
    /// Sessions whose next direct iteration is on its way.
    pub(super) fn expected(&self) -> usize {
        self.prefilling
            .load(Ordering::Relaxed)
            .saturating_add(self.generating.load(Ordering::Relaxed))
    }

    /// Sessions generating tokens.
    pub(super) fn generating(&self) -> usize {
        self.generating.load(Ordering::Relaxed)
    }

    fn counter(&self, role: DirectRole) -> &AtomicUsize {
        match role {
            DirectRole::Prefilling => &self.prefilling,
            DirectRole::Generating => &self.generating,
        }
    }
}

/// What a session hands the worker while its guard lives.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum DirectRole {
    /// Chunks of its prompt.
    Prefilling,
    /// One generated token a step.
    Generating,
}

/// One session's place among the direct ones; it leaves on drop.
#[derive(Debug)]
pub(crate) struct DirectSession {
    sessions: Arc<DirectSessions>,
    role: DirectRole,
}

impl DirectSession {
    pub(super) fn enter(sessions: &Arc<DirectSessions>, role: DirectRole) -> Self {
        sessions.counter(role).fetch_add(1, Ordering::Relaxed);
        Self {
            sessions: Arc::clone(sessions),
            role,
        }
    }
}

impl Drop for DirectSession {
    fn drop(&mut self) {
        self.sessions
            .counter(self.role)
            .fetch_sub(1, Ordering::Relaxed);
    }
}

#[cfg(test)]
mod tests {
    use super::{DirectRole, DirectSession, DirectSessions};
    use std::sync::Arc;

    #[test]
    fn sessions_count_while_their_guards_live() {
        let sessions = Arc::new(DirectSessions::default());
        let prompt = DirectSession::enter(&sessions, DirectRole::Prefilling);
        let first = DirectSession::enter(&sessions, DirectRole::Generating);
        let second = DirectSession::enter(&sessions, DirectRole::Generating);
        assert_eq!((sessions.expected(), sessions.generating()), (3, 2));
        drop(prompt);
        drop(first);
        assert_eq!((sessions.expected(), sessions.generating()), (1, 1));
        drop(second);
        assert_eq!(sessions.expected(), 0);
    }
}
