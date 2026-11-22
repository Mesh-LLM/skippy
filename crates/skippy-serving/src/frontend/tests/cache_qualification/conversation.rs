use super::{
    EXPECTED_CODE, Family,
    evidence::{Candidate, Report, Status, compare, validate_l1_replay, validate_miss},
    messages, user_content,
};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Case {
    FirstFollowUp,
    SecondFollowUp,
    DivergentTail,
}

impl Case {
    pub(super) const ALL: [Self; 3] = [
        Self::FirstFollowUp,
        Self::SecondFollowUp,
        Self::DivergentTail,
    ];

    pub(super) fn messages(self) -> Value {
        if self == Self::DivergentTail {
            return json!([{"role": "user", "content": user_content(
                "The Harbor access code is 1618. Return the Harbor access code as a JSON object with the integer field code."
            )}]);
        }
        let Value::Array(mut history) = messages() else {
            unreachable!("qualification seed is a message array");
        };
        history.extend([
            json!({"role": "assistant", "content": format!("{{\"code\":{EXPECTED_CODE}}}")}),
            json!({"role": "user", "content": "The Harbor access code has changed to 3141. Return the new code as a JSON object with the integer field code."}),
        ]);
        if self == Self::SecondFollowUp {
            history.extend([
                json!({"role": "assistant", "content": "{\"code\":3141}"}),
                json!({"role": "user", "content": "The Harbor access code has changed to 2718. Return the new code as a JSON object with the integer field code."}),
            ]);
        }
        Value::Array(history)
    }

    fn code(self) -> i64 {
        match self {
            Self::FirstFollowUp => 3141,
            Self::SecondFollowUp => 2718,
            Self::DivergentTail => 1618,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::FirstFollowUp => "first follow-up",
            Self::SecondFollowUp => "second follow-up",
            Self::DivergentTail => "divergent tail",
        }
    }
}

#[derive(Deserialize, Serialize)]
pub(super) struct Observation {
    pub(super) cold: Candidate,
    pub(super) warm: Candidate,
}

#[derive(Serialize)]
pub(super) struct ResultRow {
    pub(super) status: Status,
    minimum_matched_tokens: Option<u32>,
    maximum_matched_tokens: Option<u32>,
    pub(super) errors: Vec<String>,
}

pub(super) fn validate(report: &Report) -> BTreeMap<Case, ResultRow> {
    Case::ALL
        .into_iter()
        .map(|case| (case, validate_case(report, case)))
        .collect()
}

fn validate_case(report: &Report, case: Case) -> ResultRow {
    let mut row = ResultRow {
        status: Status::Fail,
        minimum_matched_tokens: None,
        maximum_matched_tokens: None,
        errors: Vec::new(),
    };
    let observations = case_observations(report, case);
    let (observed, source) = match observations {
        Ok(observations) => observations,
        Err(error) => {
            row.errors.push(format!("{}: {error:#}", case.label()));
            return row;
        }
    };
    let family = report.fixture.family;
    let accounting = validate_accounting(case, family, observed, source, &mut row);
    for (check, outcome) in [
        (
            "known answer and prompt identity",
            compare(
                &observed.cold.generation,
                &observed.warm.generation,
                case.code(),
            ),
        ),
        ("token accounting", accounting),
    ] {
        if let Err(error) = outcome {
            row.errors
                .push(format!("{} {check}: {error:#}", case.label()));
        }
    }
    if row.errors.is_empty() {
        row.status = Status::Pass;
    }
    row
}

fn case_observations(report: &Report, case: Case) -> Result<(&Observation, &Candidate)> {
    let observed = report
        .conversations
        .get(&case)
        .context("missing Skippy conversation results")?;
    let source = match case {
        Case::FirstFollowUp | Case::DivergentTail => report.seed.as_ref(),
        Case::SecondFollowUp => report
            .conversations
            .get(&Case::FirstFollowUp)
            .map(|case| &case.warm),
    }
    .context("missing previously seeded conversation")?;
    Ok((observed, source))
}

fn validate_accounting(
    case: Case,
    family: Family,
    observation: &Observation,
    source: &Candidate,
    row: &mut ResultRow,
) -> Result<()> {
    let prompt = observation.warm.generation.prompt_ids.as_slice();
    let source_prompt = source.generation.prompt_ids.as_slice();
    let prompt_tokens = u32::try_from(prompt.len())?;
    ensure!(
        observation.cold.cache.status == "disabled" && observation.cold.cache.cached_tokens == 0,
        "comparison baseline did not run with caching disabled"
    );
    match case {
        Case::DivergentTail => ensure!(
            !prompt.starts_with(source_prompt) && common_prefix(prompt, source_prompt) >= 64,
            "divergent chat must share an admissible prefix and change the tail"
        ),
        _ => ensure!(
            prompt.len() > source_prompt.len(),
            "follow-up did not grow the prompt"
        ),
    }
    let (minimum, maximum) = expected_match(family, source, prompt)?;
    row.minimum_matched_tokens = Some(minimum);
    row.maximum_matched_tokens = Some(maximum);
    if maximum == 0 {
        ensure!(
            case == Case::DivergentTail,
            "follow-up did not preserve a reusable checkpoint"
        );
        validate_miss(prompt_tokens, &observation.warm)
    } else {
        let restored = observation.warm.cache.matched_tokens;
        ensure!(
            restored >= minimum && restored <= maximum,
            "restored prefix {restored} lies outside the retained {minimum}..={maximum} tokens"
        );
        validate_l1_replay(family, prompt_tokens, restored, &observation.warm)
    }
}

fn expected_match(family: Family, source: &Candidate, prompt: &[i32]) -> Result<(u32, u32)> {
    let source_prompt = source.generation.prompt_ids.as_slice();
    let maximum = family.prefill_tokens(u32::try_from(prompt.len())?) as usize;
    let prompt = &prompt[..maximum];
    match family {
        Family::DenseAttention | Family::AttentionMoe => {
            let previous_prefill = source_prompt.len().saturating_sub(1);
            let minimum = common_prefix(prompt, &source_prompt[..previous_prefill]);
            // The native checkpoint also retains prompt + decoded generated
            // tokens; the newest sampled token has not been decoded yet.
            let mut retained = source_prompt.to_vec();
            let generated = &source.generation.generated_ids;
            retained.extend_from_slice(&generated[..generated.len().saturating_sub(1)]);
            let maximum = common_prefix(prompt, &retained);
            Ok((u32::try_from(minimum)?, u32::try_from(maximum)?))
        }
        Family::HybridDense | Family::HybridMoe => {
            let checkpoint = source
                .chat_checkpoint_tokens
                .context("missing recurrent chat checkpoint")?;
            let prefix = source_prompt
                .get(..checkpoint as usize)
                .context("invalid recurrent chat checkpoint")?;
            let matched = if prompt.starts_with(prefix) {
                checkpoint
            } else {
                0
            };
            Ok((matched, matched))
        }
    }
}

fn common_prefix(first: &[i32], second: &[i32]) -> usize {
    first
        .iter()
        .zip(second)
        .take_while(|(left, right)| left == right)
        .count()
}

#[test]
fn conversation_keeps_the_seed_for_follow_ups_and_changes_the_divergent_tail() {
    let seed = messages();
    let original = &seed[0];
    assert_eq!(&Case::FirstFollowUp.messages()[0], original);
    assert_eq!(&Case::SecondFollowUp.messages()[0], original);
    assert_ne!(&Case::DivergentTail.messages()[0], original);
    assert_eq!(Case::SecondFollowUp.messages().as_array().unwrap().len(), 5);
}

#[test]
fn attention_accounting_includes_decoded_generated_tokens() {
    let source: Candidate = serde_json::from_value(json!({
        "generation": {"prompt_sha256":"prompt", "prompt_ids":[1,2,3,4],
            "generated_ids":[5,6,7], "content":"{\"code\":1729}", "top_logprobs":[]},
        "chat_checkpoint_tokens":null,
        "cache":{"status":"miss", "hit_kind":null,"restore_source":null,"payload_kind":null,
            "prompt_tokens":4,"cached_tokens":0,"matched_tokens":0,"suffix_tokens":4}
    }))
    .unwrap();
    assert_eq!(
        expected_match(Family::AttentionMoe, &source, &[1, 2, 3, 4, 5, 6, 7, 8]).unwrap(),
        (3, 6)
    );
    assert_eq!(
        expected_match(Family::DenseAttention, &source, &[1, 2, 9, 8]).unwrap(),
        (2, 2)
    );
}
