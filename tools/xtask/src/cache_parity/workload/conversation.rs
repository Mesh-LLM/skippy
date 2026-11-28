use super::Workload;
use serde_json::{Value, json};
use std::fmt::Write;

pub(super) struct Conversation {
    pub(super) seed: Vec<Value>,
    pub(super) replay: Vec<Value>,
}

pub(super) fn build(
    scenario: Workload,
    lane: &str,
    burst: usize,
    burst_count: usize,
    expected: &str,
) -> Conversation {
    let prefix = vec![
        message(
            "system",
            format!(
                "{lane} is the workspace for this review. Follow the final request and preserve exact acceptance text."
            ),
        ),
        message("user", repository(lane, expected)),
        message(
            "assistant",
            "I located the routing table and the acceptance constant. I'll compare the configuration with the test output.",
        ),
    ];
    let instruction = "Return the value of ACCEPTANCE from the source as your entire reply. Do not add quotes, punctuation, or explanation.";
    match scenario {
        Workload::ExactReplay => {
            let mut messages = prefix;
            messages.push(message(
                "user",
                format!("Review run {burst} finished. {instruction}"),
            ));
            Conversation {
                seed: messages.clone(),
                replay: messages,
            }
        }
        Workload::DivergentTail => {
            let mut seed = prefix.clone();
            let mut replay = prefix;
            seed.push(message(
                "user",
                format!("Review run {burst} confirmed the request timeout cases. {instruction}"),
            ));
            replay.push(message(
                "user",
                format!(
                    "Review run {burst} confirmed the retry and cancellation cases. {instruction}"
                ),
            ));
            Conversation { seed, replay }
        }
        Workload::GrowingConversation => {
            let mut seed = prefix;
            let turns = 1 + burst * 5 / burst_count;
            for (step, check) in ["format", "unit", "timeouts", "retries", "integration"]
                .iter()
                .take(turns)
                .enumerate()
            {
                seed.push(message("user", format!("The CI tool ran check {step} ({check}) in {lane}. Exit status is zero. The output shows the configured routes match the source table, cancelled requests leave no in-flight work, and retry counters reset after success. Record this result and identify the next validation step.")));
                seed.push(message("assistant", format!("The {check} check passed. I'll review the next test result without changing the routing configuration.")));
            }
            seed.push(message(
                "user",
                "Read the acceptance constant and confirm it is still present.",
            ));
            let mut replay = seed.clone();
            replay.push(message(
                "assistant",
                "The acceptance constant is still present in the source.",
            ));
            replay.push(message(
                "user",
                format!("The review is now complete. {instruction}"),
            ));
            Conversation { seed, replay }
        }
    }
}

pub(super) fn eviction(lane: &str, burst: usize) -> Vec<Value> {
    vec![
        message("system", format!("Migration ledger for the {lane} archival service. Use the numbered audit log below.")),
        message("user", format!("Audit run {burst} completed. The audit log records independent ledger operations.\n{}\nReply only with ARCHIVE AUDIT COMPLETE.",
            (0..24).map(|id| format!("partition {id:03}: archived entries verified, retention window checked, no pending updates.\n")).collect::<String>())),
    ]
}

fn repository(lane: &str, expected: &str) -> String {
    let mut text = format!(
        "Review changes to the {lane} routing module. Check the source and compare it with CI before replying.\n\n```rust\nstruct Route {{ path: &'static str, timeout_ms: u32, retry: bool }}\nconst ROUTES: &[Route] = &[\n"
    );
    for index in 0..28 {
        let group = ["events", "sessions", "metadata", "exports"][index % 4];
        let timeout = 150 + index * 25;
        let retry = index % 4 != 2;
        writeln!(
            text,
            "    Route {{ path: \"/{group}/v{}\", timeout_ms: {timeout}, retry: {retry} }},",
            index + 1
        )
        .unwrap();
    }
    write!(text, "];\nconst ACCEPTANCE: &str = \"{expected}\";\n```\n\nThe retry policy must preserve cancellation and report a timeout only after the configured deadline. The acceptance string is used by the CI harness.").unwrap();
    text
}

fn message(role: &str, content: impl Into<String>) -> Value {
    json!({"role":role,"content":content.into()})
}
