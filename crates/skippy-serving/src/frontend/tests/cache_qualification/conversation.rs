use super::{
    Family,
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
                "Continue this descending sequence, starting immediately with 4: 8, 7, 6, 5,"
            )}]);
        }
        let Value::Array(mut history) = messages() else {
            unreachable!("qualification seed is a message array");
        };
        history.extend([
            json!({"role": "assistant", "content": "5, 6, 7, 8"}),
            json!({"role": "user", "content": "Continue from the last number. Start immediately with 9 and list the next numbers separated by commas."}),
        ]);
        if self == Self::SecondFollowUp {
            history.extend([
                json!({"role": "assistant", "content": "9, 10, 11, 12"}),
                json!({"role": "user", "content": "Continue the same sequence. Start immediately with 13 and list the next numbers separated by commas."}),
            ]);
        }
        Value::Array(history)
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
    expected_matched_tokens: Option<u32>,
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
        expected_matched_tokens: None,
        errors: Vec::new(),
    };
    let observations = case_observations(report, case);
    let (reference, observed, source) = match observations {
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
            "cold Skippy against pinned llama.cpp",
            compare(reference, &observed.cold.generation),
        ),
        (
            "warm Skippy against cold Skippy",
            compare(&observed.cold.generation, &observed.warm.generation),
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

fn case_observations(
    report: &Report,
    case: Case,
) -> Result<(&super::evidence::Generation, &Observation, &Candidate)> {
    let reference = report
        .reference
        .as_ref()
        .and_then(|reference| reference.conversations.get(&case))
        .context("missing llama.cpp conversation result")?;
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
    Ok((reference, observed, source))
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
    let matched = expected_match(family, source, prompt)?;
    row.expected_matched_tokens = Some(matched);
    if matched == 0 {
        ensure!(
            case == Case::DivergentTail,
            "follow-up did not preserve a reusable checkpoint"
        );
        validate_miss(prompt_tokens, &observation.warm, family)
    } else {
        validate_l1_replay(family, prompt_tokens, matched, &observation.warm)
    }
}

fn expected_match(family: Family, source: &Candidate, prompt: &[i32]) -> Result<u32> {
    let source_prompt = source.generation.prompt_ids.as_slice();
    let maximum = family.prefill_tokens(u32::try_from(prompt.len())?) as usize;
    let prompt = &prompt[..maximum];
    match family {
        Family::DenseAttention | Family::AttentionMoe => {
            let previous_prefill = source_prompt.len().saturating_sub(1);
            Ok(u32::try_from(common_prefix(
                prompt,
                &source_prompt[..previous_prefill],
            ))?)
        }
        Family::HybridDense | Family::HybridMoe => {
            let checkpoint = source
                .chat_checkpoint_tokens
                .context("missing recurrent chat checkpoint")?;
            let prefix = source_prompt
                .get(..checkpoint as usize)
                .context("invalid recurrent chat checkpoint")?;
            Ok(if prompt.starts_with(prefix) {
                checkpoint
            } else {
                0
            })
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
