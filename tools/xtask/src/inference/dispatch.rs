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
        "--fail-with-body".into(),
        "--silent".into(),
        "--show-error".into(),
        "--max-time".into(),
        "60".into(),
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
    serde_json::from_str(&execution::capture("curl", &args, &[])?).map_err(Into::into)
}

pub fn cancel(ledger: &Path) -> DynResult<()> {
    let ids: Vec<String> = serde_json::from_slice(&fs::read(ledger)?)?;
    let mut errors = Vec::new();
    for id in ids {
        if !id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') {
            return Err("invalid job id".into());
        }
        let job = api("GET", &format!("/v1/jobs/{id}"), None)?;
        if matches!(
            job["status"].as_str(),
            Some("succeeded" | "failed" | "cancelled" | "expired")
        ) {
            continue;
        }
        let path = if let Some(formation) = job["formation_id"].as_str() {
            if !formation
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'_')
            {
                return Err("invalid formation id".into());
            }
            format!("/v1/formations/{formation}/cancel")
        } else {
            format!("/v1/jobs/{id}/cancel")
        };
        if let Err(e) = api("POST", &path, None) {
            errors.push(e.to_string());
        }
    }
    if !errors.is_empty() {
        return Err(errors.join("\n").into());
    }
    Ok(())
}

pub fn run(profile: &str, sha: &str, output: &Path) -> DynResult<()> {
    let ladder: Ladder =
        serde_json::from_str(include_str!("../../../../ci/inference-ladder.json"))?;
    let plan = plan(&ladder, profile, sha)?;
    fs::create_dir_all(output)?;
    write_json_file(&output.join("plan.json"), &plan)?;
    let ledger = output.join("jobs.json");
    let mut ids = Vec::<String>::new();
    write_json_file(&ledger, &ids)?;
    let result = (|| {
        for row in &plan.rows {
            let launch = api(
                "POST",
                &format!("/v1/repos/skippy/templates/inference-{}/launch", row.mode),
                Some(
                    &json!({"ref":sha,"inputs":{"profile":profile,"model":row.model.split('/').nth(1),"model_key": row.id.strip_suffix(&format!("-{}", row.mode)).ok_or("invalid row id")?},"idempotency_key":format!("inference-{}-{profile}-{}-{}",std::env::var("GITHUB_RUN_ID").unwrap_or_else(|_|"local".into()),std::env::var("GITHUB_RUN_ATTEMPT").unwrap_or_else(|_|"1".into()),row.id)}),
                ),
            )?;
            let id = launch["job_id"]
                .as_str()
                .ok_or("JY launch returned no job_id")?
                .to_owned();
            ids.push(id.clone());
            write_json_file(&ledger, &ids)?;
            write_json_file(&output.join(format!("{}-launch.json", row.id)), &launch)?;
            let started = Instant::now();
            loop {
                let job = api("GET", &format!("/v1/jobs/{id}"), None)?;
                write_json_file(&output.join(format!("{}-job.json", row.id)), &job)?;
                match job["status"].as_str().ok_or("job status missing")? {
                    "succeeded" => break,
                    "failed" | "cancelled" | "expired" => {
                        return Err(format!("{} failed: {job}", row.id).into());
                    }
                    _ if started.elapsed() > Duration::from_secs(21600) => {
                        return Err("JY qualification timed out".into());
                    }
                    _ => thread::sleep(Duration::from_secs(15)),
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
