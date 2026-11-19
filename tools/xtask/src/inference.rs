//! Source-bound qualification planning. A plan is never hardware evidence.
mod admission;
mod artifacts;
mod build;
mod changes;
mod comment;
mod corpus;
mod dispatch;
mod execute;
mod gpu;
mod login;
mod numerical;
mod performance;
mod placement;
mod provenance;
mod retention;
mod smoke;
mod summary;
mod tests;
mod throughput;
use crate::command::{DynResult, write_json_file};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, path::Path};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Ladder {
    schema_version: u32,
    models: Vec<Model>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Model {
    id: String,
    model: String,
    architecture: String,
    pr: bool,
    split: bool,
}

#[derive(Serialize)]
struct Plan<'a> {
    schema_version: u32,
    status: &'static str,
    profile: &'a str,
    source_sha: &'a str,
    hardware: &'static str,
    execution: &'static str,
    performance_claim: &'static str,
    rows: Vec<Row>,
}

#[derive(Serialize)]
struct Row {
    id: String,
    model: String,
    architecture: String,
    mode: &'static str,
    machines: u8,
    checks: [&'static str; 3],
}

pub fn run(args: &[String]) -> DynResult<()> {
    match args
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["build"] => build::run(),
        ["admit"] => admission::run(),
        ["inputs", base, head, output] => changes::inspect(base, head, Path::new(output)),
        ["summary"] => summary::run(),
        ["comment", directory] => comment::run(Path::new(directory)),
        ["control-preflight"] => {
            let result = dispatch::api("GET", "/v1/repos/skippy", None)?;
            println!("JY repository access verified: {}", result["slug"]);
            Ok(())
        }
        ["dispatch", profile, sha, output] => dispatch::run(profile, sha, Path::new(output), None),
        ["dispatch", profile, sha, output, row] => {
            dispatch::run(profile, sha, Path::new(output), Some(row))
        }
        ["matrix", profile, sha] => {
            use std::io::Write;
            let ladder: Ladder =
                serde_json::from_str(include_str!("../../../ci/inference-ladder.json"))?;
            let plan = plan(&ladder, profile, sha)?;
            let value = serde_json::json!({"include":plan.rows});
            let mut file = std::fs::OpenOptions::new()
                .append(true)
                .open(std::env::var("GITHUB_OUTPUT")?)?;
            writeln!(file, "matrix={value}")?;
            Ok(())
        }
        ["cancel", ledger] => dispatch::cancel(Path::new(ledger)),
        ["execute", profile, key, mode] => execute::run(profile, key, mode),
        ["plan", profile, sha, output] => {
            let ladder: Ladder =
                serde_json::from_str(include_str!("../../../ci/inference-ladder.json"))?;
            let plan = plan(&ladder, profile, sha)?;
            write_json_file(Path::new(output), &plan)?;
            println!(
                "Wrote {} planned configurations; no inference was executed",
                plan.rows.len()
            );
            Ok(())
        }
        _ => Err("usage: inference plan {pr|nightly|release} SOURCE_SHA OUTPUT_JSON".into()),
    }
}

fn plan<'a>(ladder: &Ladder, profile: &'a str, sha: &'a str) -> DynResult<Plan<'a>> {
    if !matches!(profile, "pr" | "nightly" | "release") {
        return Err("unknown inference profile".into());
    }
    if sha.len() != 40 || !sha.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("source must be an immutable 40-digit commit SHA".into());
    }
    if ladder.schema_version != 1 || ladder.models.is_empty() {
        return Err("unsupported or empty inference ladder".into());
    }
    let mut ids = BTreeSet::new();
    let mut models = BTreeSet::new();
    for model in &ladder.models {
        if model.id.is_empty()
            || !model
                .id
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
            || !ids.insert(&model.id)
            || !models.insert(&model.model)
            || !model.model.starts_with("Qwen/")
            || !matches!(
                model.architecture.as_str(),
                "hybrid-dense" | "full-attention-moe" | "hybrid-moe" | "qwen4-preview"
            )
        {
            return Err(format!("invalid or duplicate inference model: {}", model.id).into());
        }
    }
    let mut rows = Vec::new();
    for model in ladder
        .models
        .iter()
        .filter(|model| profile != "pr" || model.pr)
    {
        for (mode, machines) in [("standalone", 1), ("split", 2)] {
            if mode == "split" && !model.split {
                continue;
            }
            rows.push(Row {
                id: format!("{}-{mode}", model.id),
                model: model.model.clone(),
                architecture: model.architecture.clone(),
                mode,
                machines,
                checks: if profile == "pr" {
                    ["gpu-startup", "scored-quality", "streaming-smoke"]
                } else {
                    ["correctness", "scored-quality", "agentx-performance"]
                },
            });
        }
    }
    if rows.is_empty() {
        return Err("profile selects no inference models".into());
    }
    Ok(Plan {
        schema_version: 1,
        status: "planned-not-executed",
        profile,
        source_sha: sha,
        hardware: "nvidia-gb10-dgx-spark",
        execution: "jy-native-job-or-atomic-formation-outside-actions-container",
        performance_claim: if profile == "pr" {
            "smoke-only"
        } else {
            "extended-qualification-requires-pinned-harness-and-repeats"
        },
        rows,
    })
}
