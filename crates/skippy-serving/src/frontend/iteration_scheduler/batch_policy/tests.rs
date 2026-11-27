use super::*;
use crate::frontend::iteration_scheduler::iteration_work::PromptStart;
use std::num::NonZeroU32;

fn queued(session: &'static str, tokens: usize, work: IterationWork) -> QueuedRequest<'static> {
    QueuedRequest {
        session,
        tokens,
        work,
    }
}

/// A chunk of the prompt that began `place`th.
fn prompt(place: u64) -> IterationWork {
    IterationWork::Prompt(PromptStart::nth(place))
}

fn view(
    queued: Vec<QueuedRequest<'static>>,
    lanes: usize,
    token_budget: usize,
) -> BatchView<'static> {
    BatchView {
        queued,
        active_sessions: 0,
        limits: BatchLimits {
            lanes,
            token_budget,
            prefill_prompts: PrefillPrompts::All,
            mixing: BatchMixing::Mixed,
        },
    }
}

fn at_most(prompts: u32) -> PrefillPrompts {
    PrefillPrompts::AtMost(NonZeroU32::new(prompts).unwrap())
}

fn default_tuning() -> BatchTuning {
    BatchTuning::resolve(BatchPlatform::Other, &BatchConfig::default(), None)
}

const DECODE: IterationWork = IterationWork::Generation;

#[test]
fn device_names_map_to_platforms() {
    assert_eq!(
        BatchPlatform::from_device_name("MTL0"),
        BatchPlatform::Metal
    );
    assert_eq!(
        BatchPlatform::from_device_name("CUDA1"),
        BatchPlatform::Cuda
    );
    assert_eq!(
        BatchPlatform::from_device_name("Vulkan0"),
        BatchPlatform::Vulkan
    );
    assert_eq!(BatchPlatform::from_device_name("CPU"), BatchPlatform::Cpu);
    assert_eq!(
        BatchPlatform::from_device_name("SYCL0"),
        BatchPlatform::Other
    );
}

/// Every backend starts from the values the scheduler has always used.
#[test]
fn unset_tuning_is_the_scheduler_default_on_every_platform() {
    for platform in [
        BatchPlatform::Metal,
        BatchPlatform::Cuda,
        BatchPlatform::Vulkan,
        BatchPlatform::Cpu,
        BatchPlatform::Other,
    ] {
        let tuning = BatchTuning::resolve(platform, &BatchConfig::default(), None);
        assert_eq!(tuning.decode_window, Duration::from_millis(2));
        assert_eq!(tuning.prefill_window_per_token, Duration::from_micros(15));
        assert_eq!(tuning.prefill_window_max, Duration::from_millis(8));
        assert_eq!(tuning.prefill_prompts, PrefillPrompts::All);
        assert_eq!(tuning.prefill_wait, PrefillWait::Wave);
        assert_eq!(tuning.mixing, BatchMixing::Mixed);
    }
    let prompt_order = BatchConfig {
        policy: Some(BatchPolicyKind::PromptOrder),
        ..BatchConfig::default()
    };
    assert_eq!(
        BatchTuning::resolve(BatchPlatform::Other, &prompt_order, None).prefill_prompts,
        PrefillPrompts::ONE
    );
}

#[test]
fn configured_tuning_replaces_the_platform_values() {
    let config = BatchConfig {
        policy: Some(BatchPolicyKind::PromptOrder),
        decode_window_us: Some(500),
        prefill_window_per_token_us: Some(40),
        prefill_prompts: Some(at_most(2)),
        prefill_wait: Some(PrefillWait::Active),
        mixing: Some(BatchMixing::Separate),
    };
    let tuning = BatchTuning::resolve(BatchPlatform::Metal, &config, Some(20));
    assert_eq!(tuning.decode_window, Duration::from_micros(500));
    assert_eq!(tuning.prefill_window_per_token, Duration::from_micros(40));
    assert_eq!(tuning.prefill_window_max, Duration::from_millis(20));
    assert_eq!(tuning.prefill_prompts, at_most(2));
    assert_eq!(tuning.prefill_wait, PrefillWait::Active);
    assert_eq!(tuning.mixing, BatchMixing::Separate);
    assert_eq!(tuning.prefill_window(100), Duration::from_millis(4));
    assert_eq!(tuning.prefill_window(4096), Duration::from_millis(20));
    assert_eq!(tuning.prefill_window(1), Duration::from_micros(500));
}

#[test]
fn a_prompt_gathers_a_full_wave_and_generation_the_active_sessions() {
    let policy = ArrivalOrder {
        tuning: default_tuning(),
    };
    let mut with_prompt = view(
        vec![queued("a", 1, DECODE), queued("b", 512, prompt(0))],
        4,
        2048,
    );
    with_prompt.active_sessions = 2;
    assert_eq!(
        policy.gather(&with_prompt),
        Gather {
            target: 4,
            window: Duration::from_micros(7_680),
        }
    );
    let mut generating = view(vec![queued("a", 1, DECODE)], 4, 2048);
    generating.active_sessions = 3;
    assert_eq!(
        policy.gather(&generating),
        Gather {
            target: 3,
            window: Duration::from_millis(2),
        }
    );
    assert_eq!(policy.gather(&view(Vec::new(), 4, 2048)).target, 0);
    let active = ArrivalOrder {
        tuning: BatchTuning {
            prefill_wait: PrefillWait::Active,
            ..default_tuning()
        },
    };
    assert_eq!(
        active.gather(&with_prompt),
        Gather {
            target: 2,
            window: Duration::from_millis(2),
        }
    );
}

#[test]
fn arrival_order_keeps_every_rule() {
    let policy = ArrivalOrder {
        tuning: default_tuning(),
    };
    let view = view(
        vec![
            queued("a", 4, prompt(0)),
            queued("b", 9, prompt(1)),
            queued("a", 1, DECODE),
            queued("b", 1, DECODE),
            queued("c", 1, DECODE),
        ],
        3,
        8,
    );
    let selection = policy.select(&view);
    // b's prefill does not fit; its later decode must not overtake it
    assert_eq!(selection, Selection(vec![0, 4]));
    assert_eq!(check_selection(&view, &selection), Ok(()));
}

#[test]
fn a_selection_that_breaks_a_rule_is_named() {
    let view = view(
        vec![
            queued("a", 4, prompt(0)),
            queued("b", 4, prompt(1)),
            queued("a", 1, DECODE),
            queued("c", 6, prompt(2)),
        ],
        2,
        8,
    );
    let rejected = |positions: &[usize]| check_selection(&view, &Selection(positions.to_vec()));
    assert_eq!(rejected(&[]), Err(BatchRejection::Empty));
    assert_eq!(rejected(&[1, 0]), Err(BatchRejection::NotInQueueOrder));
    assert_eq!(rejected(&[0, 0]), Err(BatchRejection::NotInQueueOrder));
    assert_eq!(rejected(&[0, 9]), Err(BatchRejection::OutOfRange));
    assert_eq!(rejected(&[1]), Err(BatchRejection::SkipsQueueHead));
    assert_eq!(rejected(&[0, 1, 3]), Err(BatchRejection::TooManyRequests));
    assert_eq!(rejected(&[0, 3]), Err(BatchRejection::OverTokenBudget));
    assert_eq!(rejected(&[0, 2]), Err(BatchRejection::SessionTwice));
    assert_eq!(rejected(&[0, 1]), Ok(()));
}

#[test]
fn a_later_request_may_not_overtake_its_own_session() {
    let view = view(
        vec![
            queued("a", 1, DECODE),
            queued("b", 4, prompt(0)),
            queued("b", 1, DECODE),
        ],
        4,
        8,
    );
    assert_eq!(
        check_selection(&view, &Selection(vec![0, 2])),
        Err(BatchRejection::OvertakesSession)
    );
    assert_eq!(check_selection(&view, &Selection(vec![0, 1])), Ok(()));
}

#[test]
fn prompt_order_runs_the_oldest_prompts_and_every_generated_token() {
    let policy = PromptOrder {
        tuning: default_tuning(),
    };
    let mut view = view(
        vec![
            queued("b", 512, prompt(1)),
            queued("c", 1, DECODE),
            queued("a", 512, prompt(0)),
            queued("d", 512, prompt(2)),
            queued("e", 1, DECODE),
        ],
        4,
        2048,
    );
    view.limits.prefill_prompts = PrefillPrompts::ONE;
    let one = policy.select(&view);
    // a's next chunk was queued after b's, but a's prompt began first
    assert_eq!(one, Selection(vec![1, 2, 4]));
    assert_eq!(check_selection(&view, &one), Ok(()));
    view.limits.prefill_prompts = at_most(2);
    assert_eq!(policy.select(&view), Selection(vec![0, 1, 2, 4]));
}

#[test]
fn a_batch_prefills_no_more_prompts_than_its_limit() {
    let mut view = view(
        vec![
            queued("a", 4, prompt(0)),
            queued("b", 4, prompt(1)),
            queued("c", 1, DECODE),
        ],
        4,
        16,
    );
    view.limits.prefill_prompts = PrefillPrompts::ONE;
    let arrival = ArrivalOrder {
        tuning: default_tuning(),
    };
    assert_eq!(arrival.select(&view), Selection(vec![0, 2]));
    assert_eq!(
        check_selection(&view, &Selection(vec![0, 1, 2])),
        Err(BatchRejection::TooManyPrompts)
    );
}

#[test]
fn the_head_waits_only_while_older_prompts_take_its_room() {
    let mut prompts = view(
        vec![
            queued("b", 4, prompt(5)),
            queued("a", 4, prompt(0)),
            queued("c", 4, prompt(9)),
            queued("d", 1, DECODE),
        ],
        4,
        16,
    );
    prompts.limits.prefill_prompts = PrefillPrompts::ONE;
    let checked = |view: &BatchView<'_>, positions: &[usize]| {
        check_selection(view, &Selection(positions.to_vec()))
    };
    assert_eq!(checked(&prompts, &[1, 3]), Ok(()));
    assert_eq!(
        checked(&prompts, &[2, 3]),
        Err(BatchRejection::SkipsQueueHead)
    );
    assert_eq!(checked(&prompts, &[3]), Err(BatchRejection::SkipsQueueHead));
    // With room for every prompt, the head fits beside the older one
    prompts.limits.prefill_prompts = PrefillPrompts::All;
    assert_eq!(
        checked(&prompts, &[1, 3]),
        Err(BatchRejection::SkipsQueueHead)
    );
    let generating = view(
        vec![queued("d", 1, DECODE), queued("a", 4, prompt(0))],
        4,
        16,
    );
    assert_eq!(
        check_selection(&generating, &Selection(vec![1])),
        Err(BatchRejection::SkipsQueueHead)
    );
}

#[test]
fn separate_batches_take_the_kind_at_the_head() {
    let arrival = ArrivalOrder {
        tuning: default_tuning(),
    };
    let mut prompt_first = view(
        vec![
            queued("a", 4, prompt(0)),
            queued("b", 1, DECODE),
            queued("c", 1, DECODE),
        ],
        4,
        16,
    );
    prompt_first.limits.mixing = BatchMixing::Separate;
    assert_eq!(arrival.select(&prompt_first), Selection(vec![0]));
    assert_eq!(
        check_selection(&prompt_first, &Selection(vec![0, 1])),
        Err(BatchRejection::MixesPromptsAndTokens)
    );
    let mut tokens_first = view(
        vec![
            queued("b", 1, DECODE),
            queued("a", 4, prompt(0)),
            queued("c", 1, DECODE),
        ],
        4,
        16,
    );
    tokens_first.limits.mixing = BatchMixing::Separate;
    assert_eq!(arrival.select(&tokens_first), Selection(vec![0, 2]));
}
