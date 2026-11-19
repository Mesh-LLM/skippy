use super::*;
use crate::execution;
use serde_json::{Value, json};
use std::{fs, io::Write};

const REPO: &str = "repos/general-inference/skippy";
const MARKER: &str = "<!-- skippy-inference-results:v1 -->";

fn github(args: &[String]) -> DynResult<Value> {
    let mut command = vec!["api".into()];
    command.extend_from_slice(args);
    let output = execution::capture("gh", &command, &[("GH_TOKEN", std::env::var("GH_TOKEN")?)])?;
    Ok(serde_json::from_str(&output)?)
}

fn get(path: &str) -> DynResult<Value> {
    github(&[path.into()])
}

pub fn run(directory: &Path) -> DynResult<()> {
    let event: Value = serde_json::from_slice(&fs::read(std::env::var("GITHUB_EVENT_PATH")?)?)?;
    if std::env::var("GITHUB_EVENT_NAME")? != "pull_request_target" {
        return Err("results comments require a pull_request_target event".into());
    }
    let number = event["number"]
        .as_u64()
        .filter(|n| *n > 0)
        .ok_or("missing PR number")?;
    let sha = std::env::var("SOURCE_SHA")?;
    let ladder: Ladder =
        serde_json::from_str(include_str!("../../../../ci/inference-ladder.json"))?;
    let plan = plan(&ladder, "pr", &sha)?;
    let run = std::env::var("GITHUB_RUN_ID")?.parse::<u64>()?;
    let attempt = std::env::var("GITHUB_RUN_ATTEMPT")?.parse::<u64>()?;
    let jobs = get(&format!(
        "{REPO}/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100"
    ))?;
    let (passed, description) = super::summary::verdict(
        &std::env::var("PLAN").unwrap_or_default(),
        &std::env::var("REQUIRED").unwrap_or_default(),
        &std::env::var("RESULT").unwrap_or_default(),
    );
    let body = render(&plan, &jobs, directory, run, attempt, passed, description)?;
    if let Ok(path) = std::env::var("GITHUB_STEP_SUMMARY") {
        fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)?
            .write_all(body.as_bytes())?;
    }
    let comments = github(&[
        format!("{REPO}/issues/{number}/comments?per_page=100"),
        "--paginate".into(),
        "--slurp".into(),
    ])?;
    let existing = owned_comment(&comments)?;
    // A superseded attempt must not replace results for a newer commit or rerun.
    let pr = get(&format!("{REPO}/pulls/{number}"))?;
    if pr["state"] != "open"
        || pr["head"]["sha"] != sha
        || existing.is_some_and(|c| newer(&c["body"], run, attempt))
    {
        println!("Skipping results comment for superseded qualification");
        return Ok(());
    }
    let (method, endpoint) = if let Some(comment) = existing {
        let id = comment["id"].as_u64().ok_or("invalid comment ID")?;
        ("PATCH", format!("{REPO}/issues/comments/{id}"))
    } else {
        ("POST", format!("{REPO}/issues/{number}/comments"))
    };
    let mut payload = tempfile::NamedTempFile::new()?;
    serde_json::to_writer(&mut payload, &json!({"body":body}))?;
    github(&[
        endpoint,
        "--method".into(),
        method.into(),
        "--input".into(),
        payload.path().to_string_lossy().into(),
    ])?;
    Ok(())
}

fn owned_comment(pages: &Value) -> DynResult<Option<&Value>> {
    let pages = pages.as_array().ok_or("invalid comment pages")?;
    let mut found = None;
    for page in pages {
        for comment in page.as_array().ok_or("invalid comment page")? {
            if comment["user"]["login"] == "github-actions[bot]"
                && comment["user"]["type"] == "Bot"
                && comment["body"]
                    .as_str()
                    .is_some_and(|body| body.starts_with(MARKER))
            {
                found = Some(comment);
            }
        }
    }
    Ok(found)
}

fn newer(body: &Value, run: u64, attempt: u64) -> bool {
    let Some(line) = body.as_str().and_then(|b| b.lines().nth(1)) else {
        return false;
    };
    let Some(generation) = line
        .strip_prefix("<!-- run:")
        .and_then(|s| s.strip_suffix(" -->"))
    else {
        return false;
    };
    let Some((old_run, old_attempt)) = generation.split_once(':') else {
        return false;
    };
    match (old_run.parse::<u64>(), old_attempt.parse::<u64>()) {
        (Ok(old_run), Ok(old_attempt)) => (old_run, old_attempt) > (run, attempt),
        _ => false,
    }
}

fn render(
    plan: &Plan<'_>,
    jobs: &Value,
    directory: &Path,
    run: u64,
    attempt: u64,
    passed: bool,
    description: &str,
) -> DynResult<String> {
    let url = format!(
        "https://github.com/general-inference/skippy/actions/runs/{run}/attempts/{attempt}"
    );
    let noop = description.starts_with("No inference inputs changed");
    let rows = jobs["jobs"].as_array();
    let complete = rows.is_some_and(|jobs| {
        plan.rows.iter().all(|row| {
            jobs.iter().any(|job| {
                job["name"] == format!("Qualify · {}", row.id) && job["conclusion"] == "success"
            })
        })
    });
    let outcome = if passed && noop {
        "Not required"
    } else if passed && complete {
        "Passed"
    } else {
        "Failed or incomplete"
    };
    let mut body = format!(
        "{MARKER}\n<!-- run:{run}:{attempt} -->\n### [General Inference](https://generalinference.xyz) Lab Qualification: {outcome}\n\nCommit: `{}` · PR smoke · [Workflow and timings]({url}) · [Saved evidence](https://github.com/general-inference/skippy/actions/runs/{run}#artifacts)\n\n{description}.\n",
        plan.source_sha
    );
    if noop {
        body.push_str("\nModel tests were not required and did not run.\n");
        return Ok(body);
    }
    let jobs = jobs["jobs"]
        .as_array()
        .ok_or("invalid workflow jobs response")?;
    body.push_str(
        "\n| Configuration | Outcome | Prefill tok/s | Decode tok/s | Actions | Lab record |\n| --- | --- | ---: | ---: | --- | --- |\n",
    );
    for row in &plan.rows {
        let name = format!("Qualify · {}", row.id);
        let job = jobs.iter().find(|j| j["name"] == name);
        let outcome = match job.and_then(|j| j["conclusion"].as_str()) {
            Some("success") => "Passed",
            Some("failure" | "timed_out" | "action_required" | "startup_failure") => "Failed",
            Some("cancelled") => "Cancelled",
            Some("skipped") => "Skipped (not qualified)",
            _ => "Missing or incomplete",
        };
        let action = job.and_then(|j| j["id"].as_u64()).map_or_else(|| "—".into(), |id| format!("[Logs](https://github.com/general-inference/skippy/actions/runs/{run}/job/{id})"));
        let (prefill, decode) = if outcome == "Passed" {
            super::throughput::read(directory, row, plan.source_sha)?
        } else {
            ("Unavailable".into(), "Unavailable".into())
        };
        body.push_str(&format!(
            "| `{}` | {outcome} | {prefill} | {decode} | {action} | {} |\n",
            row.id,
            jy_record(directory, row, plan.source_sha)?
        ));
    }
    body.push_str("\nTPS describes the first response of each of three short scored smoke cases at concurrency 1; deterministic repeats are excluded. Each rate is total tokens / total server-reported phase time, not an average of request rates. Prefill excludes cached prompt tokens. These short samples include startup/warmup effects and are not sustained benchmarks or controlled speedup comparisons. Missing or invalid timings show as Unavailable.\n");
    body.push_str("\nSmoke checks cover GPU startup, deterministic endpoint repeats and bounded streaming. They do not establish an AgentX performance score. The General Inference lab retains native results and logs; GitHub evidence copies expire after 30 days.\n");
    Ok(body)
}

fn jy_record(directory: &Path, row: &Row, sha: &str) -> DynResult<String> {
    let path = directory.join(format!("{}-job.json", row.id));
    if !path.is_file() {
        return Ok("Unavailable".into());
    }
    let job: Value = serde_json::from_slice(&fs::read(path)?)?;
    if job["repo_rev"] != sha {
        return Err("comment evidence source SHA mismatch".into());
    }
    let id = job["id"]
        .as_str()
        .filter(|id| {
            id.starts_with("job_") && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
        })
        .ok_or("invalid JY job ID")?;
    Ok(format!("[{id}](https://pilot.jianyang.cloud/jobs/{id})"))
}

#[cfg(test)]
#[path = "comment_tests.rs"]
mod tests;
