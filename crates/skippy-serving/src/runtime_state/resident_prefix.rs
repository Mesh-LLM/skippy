//! Cold exports read immutable cache owners, never a borrowed execution lane.
use super::*;

pub(super) type IdlePrefixCapture = (bool, Box<dyn FnOnce(&mut RuntimeState) + Send>);

impl RuntimeState {
    pub(crate) fn offer_idle_prefix_capture(
        &mut self,
        preferred: bool,
        capture: impl FnOnce(&mut Self) + Send + 'static,
    ) {
        // Keep one candidate without retaining another native owner. Prefer
        // a reusable boundary over a later request-specific checkpoint.
        if preferred
            || !self
                .idle_prefix_capture
                .as_ref()
                .is_some_and(|(preferred, _)| *preferred)
        {
            self.idle_prefix_capture = Some((preferred, Box::new(capture)));
        }
    }

    pub(crate) fn can_run_idle_prefix_capture(&self) -> bool {
        self.idle_prefix_capture.is_some() && self.is_idle_for_prefix_capture()
    }

    pub(crate) fn run_idle_prefix_capture(&mut self) {
        if self.is_idle_for_prefix_capture()
            && let Some((_, capture)) = self.idle_prefix_capture.take()
        {
            capture(self);
        }
    }

    pub(crate) fn is_idle_for_prefix_capture(&self) -> bool {
        self.sessions.is_empty() && self.parked_session_ends.is_empty()
    }
}

impl RuntimeState {
    pub(crate) fn resident_kv_page_desc(
        &mut self,
        session: &str,
        sequence: i32,
        tokens: std::ops::Range<u64>,
    ) -> Result<skippy_runtime::RuntimeKvPageDesc> {
        let layers = (
            i32::try_from(self.model_layer_start())?,
            i32::try_from(self.model_layer_end())?,
        );
        self.session(session)?
            .resident_kv_range_desc(sequence, layers, tokens)
    }

    pub(crate) fn export_resident_kv_page(
        &mut self,
        session: &str,
        sequence: i32,
        tokens: std::ops::Range<u64>,
        bytes: usize,
    ) -> Result<skippy_runtime::RuntimeKvPage> {
        let layers = (
            i32::try_from(self.model_layer_start())?,
            i32::try_from(self.model_layer_end())?,
        );
        self.session(session)?
            .export_resident_kv_range(sequence, layers, tokens, bytes)
    }

    pub(crate) fn resident_recurrent_state_bytes(
        &mut self,
        session: &str,
        sequence: i32,
    ) -> Result<usize> {
        self.session(session)?
            .resident_recurrent_state_bytes(sequence)
    }

    pub(crate) fn export_resident_recurrent_state(
        &mut self,
        session: &str,
        sequence: i32,
        bytes: usize,
    ) -> Result<Vec<u8>> {
        self.session(session)?
            .export_resident_recurrent_state(sequence, bytes)
    }
}
