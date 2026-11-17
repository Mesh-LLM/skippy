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
        let number = pr["number"].as_u64().ok_or("missing PR number")?;
        let files = github(&format!(
            "repos/general-inference/skippy/pulls/{number}/files?per_page=100"
        ))?;
        // Fail broad rather than miss dependencies or a paginated file list.
        let relevant = files
            .as_array()
            .ok_or("invalid files response")?
            .iter()
            .any(|f| f["filename"].as_str().is_some_and(relevant));
        if !relevant && files.as_array().is_some_and(|f| f.len() < 100) {
            return Err("PR does not touch inference inputs".into());
        }
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
    let ladder: Ladder =
        serde_json::from_str(include_str!("../../../../ci/inference-ladder.json"))?;
    plan(&ladder, &profile, &sha)?;
    let mut file = fs::OpenOptions::new()
        .append(true)
        .open(std::env::var("GITHUB_OUTPUT")?)?;
    writeln!(file, "source={sha}\nprofile={profile}")?;
    Ok(())
}
fn relevant(path: &str) -> bool {
    [
        "crates/",
        "llama_cpp/",
        "tools/",
        "scripts/",
        "ci/",
        ".gi/",
        ".github/",
        "Cargo.",
        "Justfile",
        "rust-toolchain",
        ".cargo/",
    ]
    .iter()
    .any(|p| path.starts_with(p))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn triggers_cover_dependencies_and_native_inputs() {
        for p in [
            "Cargo.lock",
            "crates/skippy-serving/src/lib.rs",
            "llama_cpp/patches/a.patch",
            ".gi/templates.yaml",
            "Justfile",
        ] {
            assert!(relevant(p));
        }
        assert!(!relevant("docs/typo.md"));
    }
}
