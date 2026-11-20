//! Typed observations from one runtime load.

use std::{
    panic::{AssertUnwindSafe, catch_unwind},
    sync::{Arc, mpsc},
    thread,
    time::Duration,
};

use skippy_runtime::{
    ModelOpenEventQueue, NativeEventRecord, RuntimeEventFailureCode, RuntimeEventKind,
    RuntimeEventProgressUnit, next_operation_id,
};

/// Select model-open observation independently of how observations are consumed.
#[derive(Clone, Default)]
#[non_exhaustive]
pub enum ModelLoadObservations {
    #[default]
    Disabled,
    /// Select the event-enabled loading path without delivering observations.
    Enabled,
    /// Deliver observations serially on a dedicated Skippy thread for this load.
    ///
    /// Native producers use a bounded nonblocking queue. The thread drains and
    /// joins before the runtime load returns. Keep callbacks short, and avoid
    /// waiting on that load. A callback panic stops delivery.
    Observe(Arc<dyn Fn(ModelLoadObservation) + Send + Sync>),
}

#[derive(Clone, Debug)]
#[non_exhaustive]
pub enum ModelLoadObservation {
    Native(ModelLoadRecord),
    Summary(ModelLoadSummary),
}

#[derive(Clone, Debug)]
#[non_exhaustive]
pub struct ModelLoadRecord {
    pub operation_id: u64,
    /// Native sequence for correlation. Other native activity can share its sequence space.
    pub sequence: u64,
    pub timestamp_mono_ns: u64,
    pub kind: ModelLoadRecordKind,
    pub detail: String,
    pub detail_truncated: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum ModelLoadRecordKind {
    Started,
    Progress {
        current: u64,
        total: u64,
        unit: ModelLoadProgressUnit,
    },
    DeviceSelected,
    PhaseChanged,
    MemoryAllocated,
    TensorsOffloaded,
    TokenizerReady,
    AuxiliaryComponentReady,
    Finished,
    HandledFailure(ModelLoadFailure),
    Other,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum ModelLoadProgressUnit {
    Unspecified,
    Bytes,
    Items,
    Tensors,
    Steps,
    Unknown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum ModelLoadFailure {
    Unspecified,
    InvalidArgument,
    Io,
    Model,
    Runtime,
    Backend,
    Cancelled,
    Internal,
    Unknown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum TerminalObservation {
    Missing,
    Consistent,
    Contradictory,
}

#[derive(Clone, Debug)]
#[non_exhaustive]
pub struct ModelLoadSummary {
    pub operation_id: u64,
    /// The result of opening the runtime, before serving backend construction.
    pub runtime_opened: bool,
    pub terminal: TerminalObservation,
    pub dropped: u64,
    pub rejected: u64,
}

impl ModelLoadObservations {
    pub(super) fn run<T>(
        self,
        load: impl FnOnce(bool, Option<Arc<ModelOpenEventQueue>>) -> anyhow::Result<T>,
    ) -> anyhow::Result<T> {
        let observer = match self {
            Self::Disabled => return load(false, None),
            Self::Enabled => return load(true, None),
            Self::Observe(observer) => observer,
        };
        let queue = ModelOpenEventQueue::new(next_operation_id());
        thread::scope(|scope| {
            let (finished, completion) = mpsc::channel();
            let drain_queue = Arc::clone(&queue);
            let worker = thread::Builder::new()
                .name("skippy-model-observation".into())
                .spawn_scoped(scope, move || drain(&drain_queue, completion, observer));
            match worker {
                Ok(worker) => {
                    let result = load(true, Some(queue));
                    let _ = finished.send(result.is_ok());
                    let _ = worker.join();
                    result
                }
                Err(error) => {
                    tracing::warn!(%error, "could not start model-load observer");
                    load(true, None)
                }
            }
        })
    }
}

fn drain(
    queue: &ModelOpenEventQueue,
    completion: mpsc::Receiver<bool>,
    observer: Arc<dyn Fn(ModelLoadObservation) + Send + Sync>,
) {
    let mut records = Vec::with_capacity(64);
    let mut terminals = Terminals::default();
    let operation_id = queue.operation_id().0;
    loop {
        let result = completion.recv_timeout(Duration::from_millis(10));
        while queue.drain(&mut records, 64) > 0 {
            for record in records.drain(..) {
                let record = typed_record(operation_id, &record);
                terminals.observe(record.kind);
                if !notify(observer.as_ref(), ModelLoadObservation::Native(record)) {
                    return;
                }
            }
        }
        match result {
            Ok(runtime_opened) => {
                notify(
                    observer.as_ref(),
                    ModelLoadObservation::Summary(ModelLoadSummary {
                        operation_id,
                        runtime_opened,
                        terminal: terminals.status(runtime_opened),
                        dropped: queue.dropped(),
                        rejected: queue.rejected(),
                    }),
                );
                return;
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => return,
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
    }
}

fn notify(observer: &dyn Fn(ModelLoadObservation), observation: ModelLoadObservation) -> bool {
    catch_unwind(AssertUnwindSafe(|| observer(observation))).is_ok()
}

#[derive(Default)]
struct Terminals {
    finished: bool,
    failed: bool,
}

impl Terminals {
    fn observe(&mut self, kind: ModelLoadRecordKind) {
        match kind {
            ModelLoadRecordKind::Finished => self.finished = true,
            ModelLoadRecordKind::HandledFailure(_) => self.failed = true,
            _ => {}
        }
    }

    fn status(&self, runtime_opened: bool) -> TerminalObservation {
        if (runtime_opened && self.failed) || (!runtime_opened && self.finished) {
            TerminalObservation::Contradictory
        } else if self.finished || self.failed {
            TerminalObservation::Consistent
        } else {
            TerminalObservation::Missing
        }
    }
}

fn typed_record(operation_id: u64, record: &NativeEventRecord) -> ModelLoadRecord {
    let event = record.to_event();
    let kind = match event.kind {
        RuntimeEventKind::ModelOpenStarted => ModelLoadRecordKind::Started,
        RuntimeEventKind::ModelOpenProgress => ModelLoadRecordKind::Progress {
            current: event.progress_current,
            total: event.progress_total,
            unit: event.progress_unit.into(),
        },
        RuntimeEventKind::BackendDeviceSelected => ModelLoadRecordKind::DeviceSelected,
        RuntimeEventKind::ModelLoadPhaseChanged => ModelLoadRecordKind::PhaseChanged,
        RuntimeEventKind::ModelLoadMemoryAllocated => ModelLoadRecordKind::MemoryAllocated,
        RuntimeEventKind::ModelLoadTensorsOffloaded => ModelLoadRecordKind::TensorsOffloaded,
        RuntimeEventKind::ModelLoadTokenizerReady => ModelLoadRecordKind::TokenizerReady,
        RuntimeEventKind::ModelLoadAuxComponentReady => {
            ModelLoadRecordKind::AuxiliaryComponentReady
        }
        RuntimeEventKind::ModelOpenFinished => ModelLoadRecordKind::Finished,
        RuntimeEventKind::ModelOpenFailedHandled => {
            ModelLoadRecordKind::HandledFailure(event.failure_code.into())
        }
        _ => ModelLoadRecordKind::Other,
    };
    ModelLoadRecord {
        operation_id,
        sequence: event.sequence,
        timestamp_mono_ns: event.timestamp_mono_ns,
        kind,
        detail: String::from_utf8_lossy(&event.detail_bytes).into_owned(),
        detail_truncated: record.detail_truncated,
    }
}

impl From<RuntimeEventProgressUnit> for ModelLoadProgressUnit {
    fn from(unit: RuntimeEventProgressUnit) -> Self {
        match unit {
            RuntimeEventProgressUnit::None => Self::Unspecified,
            RuntimeEventProgressUnit::Bytes => Self::Bytes,
            RuntimeEventProgressUnit::Items => Self::Items,
            RuntimeEventProgressUnit::Tensors => Self::Tensors,
            RuntimeEventProgressUnit::Steps => Self::Steps,
            RuntimeEventProgressUnit::Unknown(_) => Self::Unknown,
        }
    }
}

impl From<RuntimeEventFailureCode> for ModelLoadFailure {
    fn from(failure: RuntimeEventFailureCode) -> Self {
        match failure {
            RuntimeEventFailureCode::None => Self::Unspecified,
            RuntimeEventFailureCode::InvalidArgument => Self::InvalidArgument,
            RuntimeEventFailureCode::IoError => Self::Io,
            RuntimeEventFailureCode::ModelError => Self::Model,
            RuntimeEventFailureCode::RuntimeError => Self::Runtime,
            RuntimeEventFailureCode::BackendError => Self::Backend,
            RuntimeEventFailureCode::Cancelled => Self::Cancelled,
            RuntimeEventFailureCode::InternalError => Self::Internal,
            RuntimeEventFailureCode::Unknown(_) => Self::Unknown,
        }
    }
}

#[cfg(test)]
mod tests;
