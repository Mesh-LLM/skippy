//! Transaction boundaries and sampler-history completion for every exact tier.
use super::*;

impl KvStageIntegration {
    /// Exact prefix import materializes KV cells before suffix admission.
    /// Make room in the same runtime operation as the import. Protect a
    /// matching resident prefix so it can still serve a failed durable fill.
    pub(in crate::kv_integration) fn admit_native_prefix_import(
        &self,
        runtime: &mut RuntimeState,
        session_id: &str,
        identity: &PrefillKvIdentity,
        token_count: u64,
    ) -> Result<bool> {
        if self.payload != StagePrefixCachePayload::ResidentKv
            || runtime.session_token_count(session_id).unwrap_or_default() != 0
        {
            // In-place imports own an existing range and can replace its cells.
            return Ok(true);
        }
        let capacity = u64::from(runtime.kv_pool_tokens());
        if token_count > capacity {
            return Ok(false);
        }
        runtime.ensure_session_active(session_id)?;
        let free_cells = capacity.saturating_sub(runtime.memory_used_cells(session_id)?);
        if free_cells >= token_count {
            return Ok(true);
        }
        let protected = self.probe_resident_prefix(identity).map(|hit| hit.seq_id);
        Ok(self
            .admit_resident_capacity(runtime, session_id, token_count, 0, 0, protected)?
            .admitted)
    }

    /// Synchronously locate and restore an exact prefix. Scheduled callers use
    /// prepared metadata so this lookup does not hold the shared runtime lock.
    pub fn restore_exact_state(
        &self,
        runtime: &mut RuntimeState,
        session_id: &str,
        identities: &[PrefillKvIdentity],
    ) -> Result<Option<ExactStateRestore>> {
        self.restore_exact_state_with_cold_cost(runtime, session_id, identities, None)
    }

    /// Restore an exact prefix, using the estimated cold cost for L3 admission.
    pub fn restore_exact_state_with_cold_cost(
        &self,
        runtime: &mut RuntimeState,
        session_id: &str,
        identities: &[PrefillKvIdentity],
        cold_prefill_cost: Option<f64>,
    ) -> Result<Option<ExactStateRestore>> {
        let prepared = self.prepare_exact_state_restore(identities, false);
        self.restore_prepared_exact_state(runtime, session_id, &prepared, cold_prefill_cost)
    }

    /// Recheck resident preference and import prepared durable metadata within
    /// the existing rollback transaction; never repeat a completed disk lookup.
    pub(crate) fn restore_prepared_exact_state(
        &self,
        runtime: &mut RuntimeState,
        session_id: &str,
        prepared: &PreparedExactStateRestore,
        cold_prefill_cost: Option<f64>,
    ) -> Result<Option<ExactStateRestore>> {
        runtime.restore_transaction(session_id, |runtime| {
            self.restore_exact_state_inner(runtime, session_id, prepared, cold_prefill_cost)
        })
    }
}

pub(super) fn finish_restore(
    runtime: &mut RuntimeState,
    session_id: &str,
    identity: &PrefillKvIdentity,
    restored: ExactStateRestore,
) -> Result<Option<ExactStateRestore>> {
    // A longest-prefix lookup may restore fewer tokens than the candidate.
    // Keep this inside the restore transaction: a history failure must retire
    // the imported state rather than allow a partially restored cache hit.
    let tokens = identity
        .token_ids
        .get(..restored.token_count)
        .context("restored state extends beyond its prefix token identity")?;
    runtime.restore_token_history(session_id, tokens)?;
    Ok(Some(restored))
}
