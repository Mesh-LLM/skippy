use super::*;
use crate::execution;
use serde_json::Value;
use std::{fs, io::Write};
fn github(path: &str) -> DynResult<Value> {
    let token = std::env::var("GH_TOKEN")?;
    let output = execution::capture("gh", &["api".into(), path.into()], &[("GH_TOKEN", token)])?;
    Ok(serde_json::from_str(&output)?)
}

pub fn run() -> DynResult<()> {
    let event: Value = serde_json::from_slice(&fs::read(std::env::var("GITHUB_EVENT_PATH")?)?)?;
    let name = std::env::var("GITHUB_EVENT_NAME")?;
    let (sha, profile) = if matches!(name.as_str(), "workflow_run" | "pull_request_target") {
        let sha = if name == "workflow_run" {
            let run = &event["workflow_run"];
            if run["conclusion"] != "success" {
                return Err("upstream CI did not pass".into());
            }
            run["head_sha"].as_str().ok_or("missing workflow SHA")?
        } else {
            event["pull_request"]["head"]["sha"]
                .as_str()
                .ok_or("missing PR SHA")?
        };
        (sha.to_owned(), "pr".to_owned())
    } else {
        let sha = std::env::var("GITHUB_SHA")?;
        let profile = if name == "schedule" {
            "nightly".to_owned()
        } else {
            std::env::var("QUALIFICATION_PROFILE").unwrap_or_else(|_| "release".into())
        };
        (sha, profile)
    };
    if matches!(name.as_str(), "workflow_run" | "pull_request_target") {
        admit_pr(&sha)?;
    }
    let ladder: Ladder =
        serde_json::from_str(include_str!("../../../../ci/inference-ladder.json"))?;
    plan(&ladder, &profile, &sha)?;
    let mut file = fs::OpenOptions::new()
        .append(true)
        .open(std::env::var("GITHUB_OUTPUT")?)?;
    writeln!(file, "source={sha}\nprofile={profile}")?;
    Ok(())
}
fn admit_pr(sha: &str) -> DynResult<()> {
    let prs = github(&format!(
        "repos/general-inference/skippy/commits/{sha}/pulls"
    ))?;
    let pr = prs
        .as_array()
        .and_then(|p| {
            p.iter()
                .find(|p| p["head"]["sha"] == sha && p["state"] == "open")
        })
        .ok_or("no current open PR for tested SHA")?;
    let number = pr["number"].as_u64().ok_or("missing PR number")?;
    if pr["head"]["repo"]["full_name"] != "general-inference/skippy" {
        return Err("fork PR needs isolated execution".into());
    }
    let author = pr["user"]["login"].as_str().ok_or("missing PR author")?;
    let permission = github(&format!(
        "repos/general-inference/skippy/collaborators/{author}/permission"
    ))?;
    if !matches!(
        permission["permission"].as_str(),
        Some("admin" | "write" | "maintain")
    ) {
        return Err("PR author is not a trusted collaborator".into());
    }
    let current = github(&format!("repos/general-inference/skippy/pulls/{number}"))?;
    if current["head"]["sha"] != sha {
        return Err("PR head changed during inference admission".into());
    }
    Ok(())
}
