//! Short-lived submitter sessions, signed with a repository automation SSH key.
use super::*;
use crate::execution;
use serde_json::{Value, json};
use std::{
    fs,
    sync::Mutex,
    time::{Duration, Instant},
};
static SESSION: Mutex<Option<(Instant, String)>> = Mutex::new(None);
fn post(path: &str, body: &Value) -> DynResult<Value> {
    let file = tempfile::NamedTempFile::new()?;
    write_json_file(file.path(), body)?;
    let result = execution::capture(
        "curl",
        &[
            "-fsS".into(),
            "--max-time".into(),
            "30".into(),
            "-H".into(),
            "Content-Type: application/json".into(),
            "--data-binary".into(),
            format!("@{}", file.path().display()),
            format!("https://pilot.jianyang.cloud/v1/auth/{path}"),
        ],
        &[],
    )?;
    Ok(serde_json::from_str(&result)?)
}
pub fn token() -> DynResult<String> {
    if let Ok(token) = std::env::var("JIANYANG_API_KEY") {
        return Ok(token);
    }
    let mut cached = SESSION.lock().map_err(|_| "session cache poisoned")?;
    if let Some((at, token)) = cached.as_ref()
        && at.elapsed() < Duration::from_secs(1800)
    {
        return Ok(token.clone());
    }
    let private = std::env::var("JIANYANG_INFERENCE_SSH_KEY")?;
    let dir = tempfile::tempdir()?;
    let key = dir.path().join("key");
    fs::write(&key, private)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&key, fs::Permissions::from_mode(0o600))?;
    }
    let public = execution::capture(
        "ssh-keygen",
        &["-y".into(), "-f".into(), key.display().to_string()],
        &[],
    )?;
    let public_path = dir.path().join("key.pub");
    fs::write(&public_path, public)?;
    let fingerprint = execution::capture(
        "ssh-keygen",
        &["-lf".into(), public_path.display().to_string()],
        &[],
    )?
    .split_whitespace()
    .nth(1)
    .ok_or("SSH fingerprint missing")?
    .to_owned();
    let challenge = post("challenge", &json!({}))?;
    if challenge["namespace"] != "jianyang" {
        return Err("unexpected SSH signature namespace".into());
    }
    let nonce = dir.path().join("nonce");
    fs::write(
        &nonce,
        challenge["nonce"]
            .as_str()
            .ok_or("challenge nonce missing")?,
    )?;
    execution::capture(
        "ssh-keygen",
        &[
            "-Y".into(),
            "sign".into(),
            "-f".into(),
            key.display().to_string(),
            "-n".into(),
            "jianyang".into(),
            nonce.display().to_string(),
        ],
        &[],
    )?;
    let signature = fs::read_to_string(dir.path().join("nonce.sig"))?;
    let session = post(
        "login",
        &json!({"nonce":challenge["nonce"],"fingerprint":fingerprint,"signature":signature}),
    )?;
    if session["role"] != "submitter" {
        return Err("inference automation requires a submitter key, never an admin key".into());
    }
    let token = session["api_key"]
        .as_str()
        .ok_or("session key missing")?
        .to_owned();
    *cached = Some((Instant::now(), token.clone()));
    Ok(token)
}
