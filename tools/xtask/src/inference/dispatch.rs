//! Protected controller. No candidate source is evaluated by this process.
use super::*;
use crate::execution;
use serde_json::{Value, json};
use std::{
    fs, thread,
    time::{Duration, Instant},
};

pub fn api(method: &str, path: &str, body: Option<&Value>) -> DynResult<Value> {
    if !path.starts_with("/v1/") || path.contains(['\r', '\n']) {
        return Err("invalid API path".into());
    }
    let token = super::login::token()?;
    if token.contains(['\r', '\n']) {
        return Err("invalid API credential".into());
    }
    let headers = tempfile::NamedTempFile::new()?;
    fs::write(
        headers.path(),
        format!("Authorization: Bearer {token}\nContent-Type: application/json\n"),
    )?;
    let mut args = vec![
        "--silent".into(),
        "--show-error".into(),
        "--max-time".into(),
        "60".into(),
        "--write-out".into(),
        "\n%{http_code}".into(),
        "-X".into(),
        method.into(),
        "-H".into(),
        format!("@{}", headers.path().display()),
        format!("https://pilot.jianyang.cloud{path}"),
    ];
    let data = tempfile::NamedTempFile::new()?;
    if let Some(body) = body {
        write_json_file(data.path(), body)?;
        args.extend([
            "--data-binary".into(),
            format!("@{}", data.path().display()),
        ]);
    }
    let response = execution::capture("curl", &args, &[])?;
    let (body, status) = response.rsplit_once('\n').ok_or("missing HTTP status")?;
    if !(200..300).contains(&status.parse::<u16>()?) {
        return Err(format!("JY HTTP {status}: {body}").into());
    }
    serde_json::from_str(body).map_err(Into::into)
}

fn launch(path: &str, body: &Value, deadline: Instant) -> DynResult<Value> {
    let mut body = body.clone();
    let mut attempt = 0;
    loop {
        if path.ends_with("-split/launch") {
            let inventory = api("GET", "/v1/agents", None)?;
            match super::placement::select(
                &inventory,
                &std::env::var("RUNNER_NAME").unwrap_or_default(),
                attempt,
                body["ref"].as_str().ok_or("missing source SHA")?,
            ) {
                Ok((leader, worker)) => {
                    body["inputs"]["leader_node"] = leader.into();
                    body["inputs"]["worker_node"] = worker.into();
                }
                Err(error) if Instant::now() < deadline => {
                    println!("Waiting for Sparks: {error}");
                    thread::sleep(Duration::from_secs(15));
                    continue;
                }
                Err(error) => return Err(error),
            }
            attempt += 1;
        }
        match api("POST", path, Some(&body)) {
            Err(error)
                if error.to_string().contains("role_unplaceable") && Instant::now() < deadline =>
            {
                println!("Waiting for an atomic Spark pair: {error}");
                thread::sleep(Duration::from_secs(15));
            }
            result => return result,
        }
    }
}

fn terminal(value: &Value) -> bool {
    matches!(
        value["status"].as_str(),
        Some("succeeded" | "failed" | "cancelled" | "expired")
    )
}
fn valid_id(id: &str) -> DynResult<()> {
    if id.is_empty() || !id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') {
        return Err("invalid JY resource id".into());
    }
    Ok(())
}
fn cancel_path(id: &str) -> DynResult<Option<String>> {
    valid_id(id)?;
    let formation = if id.starts_with("frm_") {
        id.to_owned()
    } else {
        let job = api("GET", &format!("/v1/jobs/{id}"), None)?;
        if let Some(formation) = job["formation_id"].as_str() {
            formation.to_owned()
        } else {
            return Ok((!terminal(&job)).then(|| format!("/v1/jobs/{id}/cancel")));
        }
    };
    valid_id(&formation)?;
    let group = api("GET", &format!("/v1/formations/{formation}"), None)?;
    let finished = group["members"]
        .as_array()
        .is_some_and(|members| !members.is_empty() && members.iter().all(terminal));
    Ok((!finished).then(|| format!("/v1/formations/{formation}/cancel")))
}
pub fn cancel(ledger: &Path) -> DynResult<()> {
    let ids: Vec<String> = serde_json::from_slice(&fs::read(ledger)?)?;
    let mut errors = Vec::new();
    for id in ids {
        let result = cancel_path(&id).and_then(|path| match path {
            Some(path) => api("POST", &path, None).map(|_| ()),
            None => Ok(()),
        });
        if let Err(error) = result
            && !error.to_string().contains("already_terminal")
        {
            errors.push(error.to_string());
        }
    }
    if !errors.is_empty() {
        return Err(errors.join("\n").into());
    }
    Ok(())
}
fn leader(formation: &Value) -> DynResult<String> {
    let id = formation["members"]
        .as_array()
        .and_then(|members| members.iter().find(|member| member["role"] == "leader"))
        .and_then(|member| member["job_id"].as_str())
        .ok_or("JY formation leader missing")?;
    valid_id(id)?;
    Ok(id.to_owned())
}
fn launched_job(launch: &Value) -> DynResult<String> {
    let id = launch["job_id"]
        .as_str()
        .ok_or("JY launch returned no job_id")?;
    valid_id(id)?;
    if id.starts_with("frm_") {
        // Deduplicated formation launches return the formation handle, whereas
        // fresh launches return the leader job. Normalize before polling/cleanup.
        leader(&api("GET", &format!("/v1/formations/{id}"), None)?)
    } else {
        Ok(id.to_owned())
    }
}

pub fn run(profile: &str, sha: &str, output: &Path, row: Option<&str>) -> DynResult<()> {
    let ladder: Ladder =
        serde_json::from_str(include_str!("../../../../ci/inference-ladder.json"))?;
    let mut plan = plan(&ladder, profile, sha)?;
    if let Some(row) = row {
        plan.rows.retain(|candidate| candidate.id == row);
        if plan.rows.len() != 1 {
            return Err("configuration not in qualification plan".into());
        }
    }
    fs::create_dir_all(output)?;
    write_json_file(&output.join("plan.json"), &plan)?;
    let ledger = output.join("jobs.json");
    let mut ids = Vec::<String>::new();
    write_json_file(&ledger, &ids)?;
    // Capacity waits and all formation members share one budget. Leave ten
    // minutes before Actions' six-hour limit for cancellation and evidence.
    let deadline = Instant::now() + Duration::from_secs(if profile == "pr" { 2400 } else { 21000 });
    let result = (|| {
        for row in &plan.rows {
            let launch = launch(
                &format!(
                    "/v1/repos/skippy/templates/inference-{}{}/launch",
                    if profile == "pr" { "pr-" } else { "" },
                    row.mode
                ),
                &json!({"ref":sha,"inputs":{"profile":profile,"model":row.model.split('/').nth(1),"model_key": row.id.strip_suffix(&format!("-{}", row.mode)).ok_or("invalid row id")?},"idempotency_key":format!("inference-{}-{profile}-{}-{}-{sha}",std::env::var("GITHUB_RUN_ID").unwrap_or_else(|_|"local".into()),std::env::var("GITHUB_RUN_ATTEMPT").unwrap_or_else(|_|"1".into()),row.id)}),
                deadline,
            )?;
            let handle = launch["job_id"]
                .as_str()
                .ok_or("JY launch returned no handle")?;
            valid_id(handle)?;
            ids.push(handle.to_owned());
            write_json_file(&ledger, &ids)?;
            write_json_file(&output.join(format!("{}-launch.json", row.id)), &launch)?;
            let id = launched_job(&launch)?;
            loop {
                if Instant::now() >= deadline {
                    return Err("JY qualification timed out".into());
                }
                let job = api("GET", &format!("/v1/jobs/{id}"), None)?;
                write_json_file(&output.join(format!("{}-job.json", row.id)), &job)?;
                match job["status"].as_str().ok_or("job status missing")? {
                    "succeeded" => {
                        if job["repo_rev"] != sha {
                            return Err("JY result source SHA mismatch".into());
                        }
                        if let Some(formation) = job["formation_id"].as_str() {
                            let evidence =
                                api("GET", &format!("/v1/formations/{formation}"), None)?;
                            write_json_file(
                                &output.join(format!("{}-formation.json", row.id)),
                                &evidence,
                            )?;
                            match evidence["status"].as_str() {
                                Some("succeeded") => break,
                                Some("failed" | "cancelled" | "expired") => {
                                    return Err("split formation failed".into());
                                }
                                _ => {
                                    thread::sleep(Duration::from_secs(5));
                                    continue;
                                }
                            }
                        }
                        break;
                    }
                    "failed" | "cancelled" | "expired" => {
                        return Err(format!("{} failed: {job}", row.id).into());
                    }
                    _ => thread::sleep(Duration::from_secs(15)),
                }
            }
            if profile == "pr" {
                let job = serde_json::from_slice(&fs::read(
                    output.join(format!("{}-job.json", row.id)),
                )?)?;
                if let Err(error) = super::throughput::collect(&job, row, sha, output) {
                    eprintln!("{} smoke throughput unavailable: {error}", row.id);
                }
            }
        }
        Ok(())
    })();
    if result.is_err() {
        let _ = cancel(&ledger);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn deduplicated_formations_resolve_the_leader_not_the_first_member() {
        let formation = json!({"members":[{"role":"worker","job_id":"job_worker"},{"role":"leader","job_id":"job_leader"}]});
        assert_eq!(leader(&formation).unwrap(), "job_leader");
        assert!(leader(&json!({"members":[]})).is_err());
        assert!(!terminal(&json!({"status":"running"})));
        assert!(terminal(&json!({"status":"succeeded"})));
    }
}
