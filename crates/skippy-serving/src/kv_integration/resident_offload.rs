//! Capture resident prefixes for optional persistence during scheduler idle time.
use super::*;
use crate::runtime_state::RuntimeState;
use config::durable_spill::{DurableSpillSender, NativeSpillExtension, PendingDurableSpill};
use std::num::NonZeroU64;

#[derive(Debug)]
pub(super) struct NativeOffloadClaim {
    claims: Arc<Mutex<BTreeSet<String>>>,
    key: String,
}

impl NativeOffloadClaim {
    pub(in crate::kv_integration) fn acquire(
        claims: &Arc<Mutex<BTreeSet<String>>>,
        namespace: &str,
        tokens: &[i32],
    ) -> Option<Self> {
        let key = format!(
            "native-offload:{}",
            skippy_cache::l3_prefix_key(namespace, tokens)
        );
        if !claims
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .insert(key.clone())
        {
            return None;
        }
        Some(Self {
            claims: Arc::clone(claims),
            key,
        })
    }
}

impl Drop for NativeOffloadClaim {
    fn drop(&mut self) {
        self.claims
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .remove(&self.key);
    }
}

impl KvStageIntegration {
    pub(super) fn rewarm_resident_prefix(
        &self,
        runtime: &mut RuntimeState,
        session_id: &str,
        identity: &PrefillKvIdentity,
        tokens: &[i32],
    ) -> Result<()> {
        runtime.restore_token_history(session_id, tokens)?;
        let mut prefix = identity.clone();
        prefix.token_ids = tokens.to_vec();
        prefix.identity.token_count = tokens.len() as u64;
        let _ = self.record_resident_prefix(runtime, session_id, &prefix, tokens);
        Ok(())
    }

    pub(super) fn native_payload_is_complete(
        &self,
        kind: skippy_cache::ExactStatePayloadKind,
    ) -> bool {
        if !self.records_resident_prefixes() {
            return kind != skippy_cache::ExactStatePayloadKind::RecurrentOnly;
        }
        self.loaded_memory_cache.is_some_and(|caps| {
            if caps.resident_attention {
                kind == skippy_cache::ExactStatePayloadKind::KvRecurrent
            } else {
                caps.resident_checkpoint
                    && kind == skippy_cache::ExactStatePayloadKind::RecurrentOnly
            }
        })
    }

    pub(super) fn native_components_are_complete(
        &self,
        kv_bytes: u64,
        recurrent_bytes: u64,
        has_descriptor: bool,
    ) -> bool {
        self.loaded_memory_cache.is_none_or(|caps| {
            !caps.resident
                || ((!caps.resident_attention || (kv_bytes > 0 && has_descriptor))
                    && (!caps.resident_checkpoint || recurrent_bytes > 0))
        })
    }

    pub(super) fn release_native_prefix(
        &self,
        runtime: &mut RuntimeState,
        session: &str,
        victim: &skippy_cache::RadixEvictionCandidate<RadixResidentEntry>,
    ) -> Result<()> {
        runtime.drop_resident_prefix_sequence(session, victim.value.seq_id)
    }

    pub(crate) fn offer_idle_prefix_capture(
        self: &Arc<Self>,
        runtime: &mut RuntimeState,
        identity: PrefillKvIdentity,
        preferred: bool,
    ) {
        let cache = Arc::downgrade(self);
        runtime.offer_idle_prefix_capture(preferred, move |runtime| {
            if let Some(cache) = cache.upgrade()
                && let Err(error) = cache.capture_resident_prefix_for_l3(runtime, &identity)
            {
                tracing::debug!(%error, "idle durable prefix capture skipped");
            }
        });
    }

    /// Read an exact, still-resident checkpoint while holding the runtime.
    /// The spill worker receives an owned payload, so persistence can outlive
    /// the native prefix and does not hold any KV cells.
    pub(crate) fn capture_resident_prefix_for_l3(
        &self,
        runtime: &mut RuntimeState,
        identity: &PrefillKvIdentity,
    ) -> Result<bool> {
        if self.l3.is_none() || !runtime.is_idle_for_prefix_capture() {
            return Ok(false);
        }
        let entry = self
            .radix
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .resident_exact(&identity.namespace, &identity.token_ids)
            .map(|entry| entry.value);
        let Some(entry) = entry else {
            return Ok(false);
        };
        // This lane provides access to the shared model. It owns no prompt
        // cells. Runtime serialization precludes concurrent native eviction
        // until the capture returns.
        const SESSION: &str = "native-prefix-durable-capture";
        runtime.ensure_session_active(SESSION)?;
        let result = self.offload_native_prefix(runtime, SESSION, identity, &entry);
        runtime.drop_session_timed(SESSION)?;
        result
    }

    fn offload_native_prefix(
        &self,
        runtime: &mut RuntimeState,
        session: &str,
        identity: &PrefillKvIdentity,
        entry: &RadixResidentEntry,
    ) -> Result<bool> {
        let sender = self
            .exact_state_record_worker
            .durable_sender
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone();
        let Some(sender) = sender else {
            return Ok(false);
        };
        let Some(capabilities) = self.loaded_memory_cache else {
            return Ok(false);
        };
        if self.l3.is_none() {
            return Ok(false);
        }
        if sender.has_native_checkpoint(&identity.namespace, &identity.token_ids) {
            return Ok(false);
        }
        let Some(claim) = NativeOffloadClaim::acquire(
            &self.inflight_records,
            &identity.namespace,
            &identity.token_ids,
        ) else {
            return Ok(false);
        };
        let sequence = entry.seq_id;
        let tokens = entry.token_count;
        let desc = if capabilities.resident_attention {
            Some(runtime.resident_kv_page_desc(session, sequence, 0..tokens)?)
        } else {
            None
        };
        let recurrent_bytes = if capabilities.resident_checkpoint {
            runtime.resident_recurrent_state_bytes(session, sequence)?
        } else {
            0
        };
        let incremental = desc
            .as_ref()
            .and_then(|full| incremental_capture(&sender, identity, full, recurrent_bytes))
            .and_then(|extension| {
                let start = extension.reused_rows.get();
                let tail = runtime
                    .resident_kv_page_desc(session, sequence, start..tokens)
                    .ok()?;
                let tail_geometry = config::kv_page_geometry(&tail, tail.payload_bytes)?;
                if tail.token_start != start
                    || tail.token_count != tokens - start
                    || tail_geometry.blocks != extension.geometry.blocks
                    || tail_geometry.window_rows != extension.geometry.window_rows
                {
                    return None;
                }
                Some((extension, tail.payload_bytes))
            });
        let (start, kv_bytes) = incremental.as_ref().map_or_else(
            || (0, desc.as_ref().map_or(0, |desc| desc.payload_bytes)),
            |(extension, bytes)| (extension.reused_rows.get(), *bytes),
        );
        let bytes = kv_bytes
            .checked_add(recurrent_bytes as u64)
            .ok_or_else(|| anyhow::anyhow!("native offload size overflow"))?;
        // An idle capture is advisory. Bound the bytes of a single capture
        // independently from the disk queue, which can hold multiple copies.
        const MAX_IDLE_CAPTURE_BYTES: u64 = 1024 * 1024 * 1024;
        if bytes == 0 || bytes > MAX_IDLE_CAPTURE_BYTES {
            return Ok(false);
        }
        let Some(reservation) = sender.try_reserve(bytes) else {
            return Ok(false);
        };
        let kv = if kv_bytes > 0 {
            let page = runtime.export_resident_kv_page(
                session,
                sequence,
                start..tokens,
                usize::try_from(kv_bytes)?,
            )?;
            anyhow::ensure!(
                page.desc.token_start == start
                    && page.desc.token_count == tokens - start
                    && page.payload.len() as u64 == kv_bytes,
                "resident export no longer matches its sized token range"
            );
            page.payload
        } else {
            Vec::new()
        };
        let recurrent = if recurrent_bytes > 0 {
            runtime.export_resident_recurrent_state(session, sequence, recurrent_bytes)?
        } else {
            Vec::new()
        };
        let payload = if capabilities.resident_attention {
            ExactStatePayload::kv_recurrent(kv, recurrent)
        } else {
            anyhow::ensure!(
                capabilities.resident_checkpoint && !recurrent.is_empty(),
                "resident prefix has no complete export component"
            );
            ExactStatePayload::recurrent_only(recurrent)
        };
        Ok(sender.send_reserved(
            PendingDurableSpill {
                page_id: entry.page_id.clone(),
                payload,
                extra: ExactStateExtra { kv_desc: desc },
                namespace: identity.namespace.clone(),
                token_ids: identity.token_ids.to_vec(),
                l3_cost: None,
                native_raw: true,
                extension: incremental.map(|(extension, _)| extension),
            },
            reservation,
            claim,
        ))
    }
}

fn incremental_capture(
    sender: &DurableSpillSender,
    identity: &PrefillKvIdentity,
    full: &skippy_runtime::RuntimeKvPageDesc,
    recurrent_bytes: usize,
) -> Option<NativeSpillExtension> {
    let base = sender.native_base(&identity.namespace, &identity.token_ids)?;
    let base_desc = serde_json::from_str(base.kv_desc_json.as_deref()?).ok()?;
    let base_geometry = config::kv_page_geometry(&base_desc, base.kv_bytes)?;
    let total = full.payload_bytes.checked_add(recurrent_bytes as u64)?;
    let geometry = config::kv_page_geometry(full, total)?;
    if base_geometry.blocks != geometry.blocks
        || base_geometry.window_rows != geometry.window_rows
        || base_geometry.rows != base.token_count
        || full.token_start != 0
    {
        return None;
    }
    let reused_rows =
        NonZeroU64::new(base.token_count / geometry.window_rows * geometry.window_rows)?;
    Some(NativeSpillExtension {
        base,
        reused_rows,
        geometry,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_offload_claim_is_token_keyed_and_released_on_every_drop() {
        let claims = Arc::new(Mutex::new(BTreeSet::new()));
        let claim = NativeOffloadClaim::acquire(&claims, "model", &[1, 2]).unwrap();
        assert!(NativeOffloadClaim::acquire(&claims, "model", &[1, 2]).is_none());
        assert!(NativeOffloadClaim::acquire(&claims, "model", &[1, 3]).is_some());
        assert!(NativeOffloadClaim::acquire(&claims, "other", &[1, 2]).is_some());
        drop(claim);
        assert!(NativeOffloadClaim::acquire(&claims, "model", &[1, 2]).is_some());
        assert!(claims.lock().unwrap().is_empty());
    }
}
