use super::*;
use anyhow::Context;
use std::{collections::VecDeque, num::NonZeroU64};

#[derive(Clone, Copy)]
pub(in crate::kv_integration) struct DurableRecordTarget<'a> {
    pub(in crate::kv_integration) l3: Option<&'a L3Tier>,
    pub(in crate::kv_integration) cachegen_enabled: bool,
    #[cfg(test)]
    pub(in crate::kv_integration) before_l3_spill: Option<&'a dyn Fn()>,
}

/// Exact-state payload handed from the serving worker to durable storage.
#[derive(Debug)]
pub(in crate::kv_integration) struct PendingDurableSpill {
    pub(in crate::kv_integration) page_id: String,
    pub(in crate::kv_integration) payload: skippy_cache::ExactStatePayload,
    pub(in crate::kv_integration) extra: super::super::ExactStateExtra,
    pub(in crate::kv_integration) namespace: String,
    pub(in crate::kv_integration) token_ids: Vec<i32>,
    pub(in crate::kv_integration) native_raw: bool,
    pub(in crate::kv_integration) l3_cost: Option<skippy_cache::policy::CostSample>,
    pub(in crate::kv_integration) extension: Option<NativeSpillExtension>,
}

#[derive(Debug)]
pub(in crate::kv_integration) struct NativeSpillExtension {
    pub(in crate::kv_integration) base: skippy_cache::L3Location,
    pub(in crate::kv_integration) reused_rows: NonZeroU64,
    pub(in crate::kv_integration) geometry: PayloadGeometry,
}

#[derive(Debug)]
struct NativeCheckpoint {
    namespace: String,
    tokens: Vec<i32>,
    location: skippy_cache::L3Location,
}

pub(in crate::kv_integration) struct DurableSpillReservation {
    bytes: u64,
    held_bytes: Arc<std::sync::atomic::AtomicU64>,
}

impl Drop for DurableSpillReservation {
    fn drop(&mut self) {
        self.held_bytes
            .fetch_sub(self.bytes, std::sync::atomic::Ordering::Release);
    }
}

struct QueuedDurableSpill {
    pending: PendingDurableSpill,
    _reservation: DurableSpillReservation,
    _native_claim: Option<super::super::resident_offload::NativeOffloadClaim>,
}

#[derive(Debug, Clone)]
pub(in crate::kv_integration) struct DurableSpillSender {
    tx: std::sync::mpsc::SyncSender<QueuedDurableSpill>,
    held_bytes: Arc<std::sync::atomic::AtomicU64>,
    byte_cap: u64,
    recent_checkpoints: Arc<Mutex<VecDeque<NativeCheckpoint>>>,
}

impl DurableSpillSender {
    fn new(tx: std::sync::mpsc::SyncSender<QueuedDurableSpill>, byte_cap: u64) -> Self {
        Self {
            tx,
            held_bytes: Arc::new(std::sync::atomic::AtomicU64::new(0)),
            byte_cap,
            recent_checkpoints: Arc::default(),
        }
    }

    pub(in crate::kv_integration) fn native_base(
        &self,
        namespace: &str,
        tokens: &[i32],
    ) -> Option<skippy_cache::L3Location> {
        self.recent_checkpoints
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .iter()
            .filter(|entry| {
                entry.namespace == namespace
                    && entry.tokens.len() < tokens.len()
                    && tokens.starts_with(&entry.tokens)
            })
            .max_by_key(|entry| entry.tokens.len())
            .map(|entry| entry.location.clone())
    }

    pub(in crate::kv_integration) fn remember_loaded(
        &self,
        namespace: &str,
        tokens: &[i32],
        location: &skippy_cache::L3Location,
    ) {
        if location.native_kv_passthrough
            && let Ok(len) = usize::try_from(location.token_count)
            && let Some(prefix) = tokens.get(..len)
        {
            Self::remember_native_checkpoint(
                &self.recent_checkpoints,
                NativeCheckpoint {
                    namespace: namespace.to_owned(),
                    tokens: prefix.to_vec(),
                    location: location.clone(),
                },
            );
        }
    }

    fn remember_native_checkpoint(
        recent: &Mutex<VecDeque<NativeCheckpoint>>,
        checkpoint: NativeCheckpoint,
    ) {
        const RECENT_NATIVE_CHECKPOINTS: usize = 16;
        let mut recent = recent
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        recent.retain(|entry| entry.location.manifest_key != checkpoint.location.manifest_key);
        if recent.len() >= RECENT_NATIVE_CHECKPOINTS {
            recent.pop_front();
        }
        recent.push_back(checkpoint);
    }

    /// Reserve bounded host bytes before materializing a native cold prefix.
    /// Unlike the legacy recorder, native offload never admits an oversized exception.
    pub(in crate::kv_integration) fn try_reserve(
        &self,
        bytes: u64,
    ) -> Option<DurableSpillReservation> {
        let mut held = self.held_bytes.load(std::sync::atomic::Ordering::Acquire);
        loop {
            if bytes > self.byte_cap.saturating_sub(held) {
                return None;
            }
            match self.held_bytes.compare_exchange_weak(
                held,
                held + bytes,
                std::sync::atomic::Ordering::AcqRel,
                std::sync::atomic::Ordering::Acquire,
            ) {
                Ok(_) => {
                    return Some(DurableSpillReservation {
                        bytes,
                        held_bytes: Arc::clone(&self.held_bytes),
                    });
                }
                Err(actual) => held = actual,
            }
        }
    }

    pub(in crate::kv_integration) fn send_reserved(
        &self,
        pending: PendingDurableSpill,
        reservation: DurableSpillReservation,
        claim: super::super::resident_offload::NativeOffloadClaim,
    ) -> bool {
        if pending.payload.byte_len() > reservation.bytes {
            return false;
        }
        self.tx
            .try_send(QueuedDurableSpill {
                pending,
                _reservation: reservation,
                _native_claim: Some(claim),
            })
            .is_ok()
    }

    /// Queue one spill without ever blocking the serving worker.
    ///
    /// The reservation remains live while the spill is queued and while the
    /// durable worker is writing it. A single oversized spill is admitted only
    /// when no other spill bytes are outstanding, matching record admission.
    pub(in crate::kv_integration) fn try_send(&self, pending: PendingDurableSpill) -> bool {
        let bytes = pending.payload.byte_len();
        let held = self
            .held_bytes
            .fetch_add(bytes, std::sync::atomic::Ordering::AcqRel);
        if !super::super::record_fits_queue(held, bytes, self.byte_cap) {
            self.held_bytes
                .fetch_sub(bytes, std::sync::atomic::Ordering::Release);
            return false;
        }
        let queued = QueuedDurableSpill {
            pending,
            _native_claim: None,
            _reservation: DurableSpillReservation {
                bytes,
                held_bytes: Arc::clone(&self.held_bytes),
            },
        };
        self.tx.try_send(queued).is_ok()
    }
}

type DurableSpillWorker = (
    Option<DurableSpillSender>,
    Option<std::thread::JoinHandle<()>>,
);

/// Starts the optional bounded worker that isolates durable I/O from L1 publication.
pub(in crate::kv_integration) fn start_durable_spill_worker(
    l3: Option<Arc<L3Tier>>,
    cachegen_enabled: bool,
    byte_cap: u64,
    stage_id: &str,
    #[cfg(test)] received: Arc<std::sync::atomic::AtomicUsize>,
    #[cfg(test)] pause: Arc<std::sync::atomic::AtomicBool>,
) -> Result<DurableSpillWorker> {
    let Some(l3) = l3 else {
        return Ok((None, None));
    };
    let (tx, rx) = std::sync::mpsc::sync_channel::<QueuedDurableSpill>(EXACT_STATE_RECORD_CAPACITY);
    let sender = DurableSpillSender::new(tx, byte_cap);
    let recent_checkpoints = Arc::clone(&sender.recent_checkpoints);
    let profile_spill = std::env::var_os("SKIPPY_PROFILE_DURABLE_SPILL").is_some();
    let task = std::thread::Builder::new()
        .name(format!("skippy-l3-spill-{stage_id}"))
        .spawn(move || {
            while let Ok(queued) = rx.recv() {
                let QueuedDurableSpill {
                    pending,
                    _reservation,
                    _native_claim,
                } = queued;
                #[cfg(test)]
                received.fetch_add(1, std::sync::atomic::Ordering::AcqRel);
                #[cfg(test)]
                while pause.load(std::sync::atomic::Ordering::Acquire) {
                    std::thread::sleep(std::time::Duration::from_millis(2));
                }
                let prefix = pending
                    .native_raw
                    .then(|| (pending.namespace.clone(), pending.token_ids.clone()));
                let reused_base = pending
                    .extension
                    .as_ref()
                    .map(|extension| extension.base.manifest_key.clone());
                let start = profile_spill.then(std::time::Instant::now);
                spill_exact_record_to_l3(&l3, cachegen_enabled, pending);
                if let Some(start) = start {
                    let elapsed_ms = start.elapsed().as_secs_f64() * 1_000.0;
                    let _ = skippy_events::diagnostics::emit(
                        skippy_events::diagnostics::ServingDiagnostic::Info {
                            message: "Skippy diagnostic durable KV spill".to_string(),
                            context: Some(
                                serde_json::json!({ "elapsed_ms": elapsed_ms }).to_string(),
                            ),
                        },
                    );
                }
                if let Some((namespace, tokens)) = prefix
                    && let Ok(Some(location)) = l3.locate_longest(&namespace, &tokens, 1)
                    && location.token_count == tokens.len() as u64
                    && location.native_kv_passthrough
                {
                    DurableSpillSender::remember_native_checkpoint(
                        &recent_checkpoints,
                        NativeCheckpoint {
                            namespace,
                            tokens,
                            location,
                        },
                    );
                } else if let Some(base) = reused_base {
                    recent_checkpoints
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner)
                        .retain(|entry| entry.location.manifest_key != base);
                }
            }
        })?;
    Ok((Some(sender), Some(task)))
}

/// Persists one exact-state payload without affecting its already-published L1 entry.
pub(in crate::kv_integration) fn spill_exact_record_to_l3(
    l3: &L3Tier,
    cachegen_enabled: bool,
    pending: PendingDurableSpill,
) {
    let PendingDurableSpill {
        page_id,
        payload,
        extra,
        namespace,
        token_ids,
        l3_cost,
        native_raw,
        extension,
    } = pending;
    // Native eviction already owns the runtime and resident index. Resolve
    // committed disk entries here on the durable worker, never under either
    // scheduler lock. The in-flight claim remains held across this lookup.
    if native_raw
        && l3
            .locate_longest(&namespace, &token_ids, 1)
            .is_ok_and(|entry| {
                entry.is_some_and(|entry| entry.token_count == token_ids.len() as u64)
            })
    {
        return;
    }
    let kv_desc_json = extra
        .kv_desc
        .as_ref()
        .and_then(|desc| serde_json::to_string(desc).ok());
    let geometry = extra
        .kv_desc
        .as_ref()
        .and_then(|desc| kv_page_geometry(desc, payload.byte_len()));
    let cachegen_spill = if cachegen_enabled && !native_raw {
        match (extra.kv_desc.as_ref(), payload.kv_bytes().ok().flatten()) {
            (Some(desc), Some(kv)) if !kv.is_empty() && cachegen_descriptor_is_qualified(desc) => {
                match skippy_runtime::encode_cachegen_kv_page(desc, kv.as_ref()) {
                    Ok(archive) if archive.bytes.len() < kv.len() => {
                        let calibration_digest = skippy_cache::segment_digest(&archive.bytes);
                        Some(l3.spill_cachegen_with_cost(
                            &namespace,
                            &token_ids,
                            &payload,
                            kv_desc_json.clone().unwrap_or_default(),
                            skippy_cache::CacheGenKvPayload {
                                archive: archive.bytes,
                                decoded_len: desc.payload_bytes,
                                calibration_digest,
                            },
                            l3_cost,
                        ))
                    }
                    Ok(_) => None,
                    Err(error) => {
                        static WARNED_CACHEGEN: std::sync::atomic::AtomicBool =
                            std::sync::atomic::AtomicBool::new(false);
                        if !WARNED_CACHEGEN.swap(true, std::sync::atomic::Ordering::AcqRel) {
                            let _ = skippy_events::diagnostics::emit(
                                skippy_events::diagnostics::ServingDiagnostic::Warning {
                                    message: "CacheGen encode declined; storing native KV page"
                                        .to_string(),
                                    context: Some(format!("page_id={} reason={error:#}", page_id)),
                                },
                            );
                        }
                        None
                    }
                }
            }
            _ => None,
        }
    } else {
        None
    };
    let spill = if let Some(extension) = extension {
        spill_native_extension(
            l3,
            &namespace,
            &token_ids,
            &payload,
            kv_desc_json,
            &extension,
        )
    } else {
        cachegen_spill.unwrap_or_else(|| {
            l3.spill_with_cost(
                &namespace,
                &token_ids,
                &payload,
                kv_desc_json,
                geometry.as_ref(),
                l3_cost,
            )
        })
    };
    emit_l3_state_transitions(l3);
    if let Err(error) = spill {
        static WARNED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
        if !WARNED.swap(true, std::sync::atomic::Ordering::AcqRel) {
            let _ = skippy_events::diagnostics::emit(
                skippy_events::diagnostics::ServingDiagnostic::Warning {
                    message: "Skippy L3 disk cache write refused; see kv-cache status".to_string(),
                    context: Some(format!("page_id={page_id} reason={error:#}")),
                },
            );
        }
    }
}

fn spill_native_extension(
    l3: &L3Tier,
    namespace: &str,
    tokens: &[i32],
    payload: &skippy_cache::ExactStatePayload,
    descriptor: Option<String>,
    extension: &NativeSpillExtension,
) -> Result<Option<String>> {
    let descriptor = descriptor.context("native extension has no full checkpoint descriptor")?;
    let kv = payload
        .kv_bytes()?
        .context("native extension has no KV suffix")?;
    let recurrent = payload.recurrent_state_bytes()?;
    l3.spill_native_extension(
        namespace,
        tokens,
        skippy_cache::NativeKvExtension {
            base: &extension.base,
            reused_rows: extension.reused_rows,
            geometry: &extension.geometry,
            kv_desc_json: descriptor,
            kv_tail: &kv,
            recurrent: &recurrent,
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pending(bytes: usize) -> PendingDurableSpill {
        PendingDurableSpill {
            page_id: format!("spill-{bytes}"),
            payload: skippy_cache::ExactStatePayload::full_state(vec![7; bytes]),
            extra: super::super::super::ExactStateExtra::default(),
            namespace: "model".to_string(),
            token_ids: vec![1, 2],
            l3_cost: None,
            native_raw: false,
            extension: None,
        }
    }

    #[test]
    fn spill_byte_reservation_covers_queued_and_active_payloads() {
        let (tx, rx) = std::sync::mpsc::sync_channel(2);
        let sender = DurableSpillSender::new(tx, 10);

        assert!(sender.try_send(pending(8)));
        assert!(!sender.try_send(pending(8)));
        assert_eq!(
            sender.held_bytes.load(std::sync::atomic::Ordering::Acquire),
            8
        );

        let active = rx.recv().unwrap();
        assert_eq!(
            sender.held_bytes.load(std::sync::atomic::Ordering::Acquire),
            8
        );
        drop(active);
        assert_eq!(
            sender.held_bytes.load(std::sync::atomic::Ordering::Acquire),
            0
        );

        assert!(sender.try_send(pending(8)));
    }
}

#[cfg(test)]
mod native_reservation_tests {
    use super::*;

    #[test]
    fn native_export_reservation_is_strict_and_released_without_materialization() {
        let (tx, _rx) = std::sync::mpsc::sync_channel(2);
        let sender = DurableSpillSender::new(tx, 10);
        let held = sender.try_reserve(8).unwrap();
        assert!(sender.try_reserve(3).is_none());
        assert!(sender.try_reserve(11).is_none());
        drop(held);
        assert!(sender.try_reserve(10).is_some());
        assert_eq!(
            sender.held_bytes.load(std::sync::atomic::Ordering::Acquire),
            0
        );
    }
}

#[cfg(test)]
#[path = "tests/native_spill_failures.rs"]
mod native_spill_failures;
