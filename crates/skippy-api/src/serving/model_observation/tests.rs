use super::*;
use skippy_ffi::{SkippyRuntimeEventV1, Status};
use skippy_runtime::{OperationId, RuntimeEventCategory, RuntimeEventEmitterKind};
use std::{mem::size_of, ptr, sync::Mutex};

fn event(kind: RuntimeEventKind, sequence: u64) -> SkippyRuntimeEventV1 {
    SkippyRuntimeEventV1 {
        abi_version: 1,
        struct_size: size_of::<SkippyRuntimeEventV1>() as u32,
        category: RuntimeEventCategory::ModelOpen.raw(),
        kind: kind.raw(),
        emitter: RuntimeEventEmitterKind::OpenThread.raw(),
        reserved0: 0,
        sequence,
        timestamp_mono_ns: sequence * 10,
        model_id: 1,
        stage_id: 0,
        session_id: 0,
        progress_current: 1,
        progress_total: 2,
        progress_unit: RuntimeEventProgressUnit::Steps.raw(),
        failure_code: RuntimeEventFailureCode::ModelError.raw(),
        status: Status::Ok as i32,
        reserved1: 0,
        detail_ptr: ptr::null(),
        detail_len: 0,
        numeric_summary_0: 0,
        numeric_summary_1: 0,
        numeric_summary_2: 0,
        numeric_summary_3: 0,
    }
}

#[test]
fn loss_and_unknown_records_are_reported_without_synthesizing_a_terminal() {
    let queue = ModelOpenEventQueue::with_capacity(OperationId(8), 2);
    // SAFETY: records remain alive for each synchronous copy.
    unsafe {
        queue.deliver_for_test(&event(RuntimeEventKind::ModelOpenProgress, 4));
        queue.deliver_for_test(&event(RuntimeEventKind::Unknown(u32::MAX), 6));
        queue.deliver_for_test(&event(RuntimeEventKind::ModelOpenFinished, 7));
        queue.deliver_for_test(ptr::null());
    }
    let (finish, complete) = mpsc::channel();
    finish.send(true).unwrap();
    let observations = Arc::new(Mutex::new(Vec::new()));
    let received = observations.clone();
    drain(
        &queue,
        complete,
        Arc::new(move |event| received.lock().unwrap().push(event)),
    );
    let events = observations.lock().unwrap();
    assert!(matches!(
        &events[0],
        ModelLoadObservation::Native(ModelLoadRecord {
            operation_id: 8,
            sequence: 4,
            kind: ModelLoadRecordKind::Progress {
                current: 1,
                total: 2,
                unit: ModelLoadProgressUnit::Steps
            },
            ..
        })
    ));
    assert!(matches!(
        &events[1],
        ModelLoadObservation::Native(ModelLoadRecord {
            sequence: 6,
            kind: ModelLoadRecordKind::Other,
            ..
        })
    ));
    assert!(matches!(
        &events[2],
        ModelLoadObservation::Summary(ModelLoadSummary {
            runtime_opened: true,
            terminal: TerminalObservation::Missing,
            dropped: 1,
            rejected: 1,
            ..
        })
    ));
}

#[test]
fn terminal_observations_never_replace_the_result_and_failure_drains_before_return() {
    use RuntimeEventKind::{ModelOpenFailedHandled, ModelOpenFinished};
    for (success, kinds, expected) in [
        (
            true,
            vec![ModelOpenFinished],
            TerminalObservation::Consistent,
        ),
        (
            false,
            vec![ModelOpenFailedHandled],
            TerminalObservation::Consistent,
        ),
        (
            true,
            vec![ModelOpenFinished, ModelOpenFailedHandled],
            TerminalObservation::Contradictory,
        ),
        (
            false,
            vec![ModelOpenFinished],
            TerminalObservation::Contradictory,
        ),
    ] {
        let calling_thread = thread::current().id();
        let events = Arc::new(Mutex::new(Vec::new()));
        let received = events.clone();
        let observations = ModelLoadObservations::Observe(Arc::new(move |event| {
            received
                .lock()
                .unwrap()
                .push((thread::current().id(), event));
        }));
        let mut weak = None;
        let result = observations.run(|enabled, queue| {
            assert!(enabled);
            let queue = queue.unwrap();
            weak = Some(Arc::downgrade(&queue));
            for (sequence, kind) in kinds.iter().copied().enumerate() {
                // SAFETY: the test record remains alive for the callback's synchronous copy.
                unsafe { queue.deliver_for_test(&event(kind, sequence as u64)) };
            }
            if success {
                Ok(5)
            } else {
                anyhow::bail!("actual runtime error")
            }
        });
        assert!(weak.unwrap().upgrade().is_none());
        assert_eq!(result.as_ref().is_ok_and(|&value| value == 5), success);
        let events = events.lock().unwrap();
        assert_eq!(events.len(), kinds.len() + 1);
        assert!(events.iter().all(|(thread, _)| *thread != calling_thread));
        if kinds.contains(&ModelOpenFailedHandled) {
            assert!(events.iter().any(|(_, event)| matches!(
                event,
                ModelLoadObservation::Native(ModelLoadRecord {
                    kind: ModelLoadRecordKind::HandledFailure(ModelLoadFailure::Model),
                    ..
                })
            )));
        }
        let ModelLoadObservation::Summary(summary) = &events.last().unwrap().1 else {
            panic!("missing summary");
        };
        assert_eq!(summary.terminal, expected);
        assert_eq!(summary.runtime_opened, success);
    }
}

#[test]
fn no_observer_preserves_event_path_selection() {
    for (mode, expected) in [
        (ModelLoadObservations::Disabled, false),
        (ModelLoadObservations::Enabled, true),
    ] {
        mode.run(|enabled, queue| {
            assert_eq!(enabled, expected);
            assert!(queue.is_none());
            Ok(())
        })
        .unwrap();
    }
}

#[test]
fn a_panicking_observer_does_not_change_the_load_result() {
    let value = ModelLoadObservations::Observe(Arc::new(|_| panic!("observer panic")))
        .run(|_, _| Ok(42))
        .unwrap();
    assert_eq!(value, 42);
}
