//! Bounded commands for CI artifact production. Children never inherit publisher tokens.
use crate::command::DynResult;
use process_supervision::{
    Cancellation, Completion, Limits, ProcessSpec, RawCaptureOptions, Readiness, Value,
};
use std::{
    ffi::OsString,
    path::{Path, PathBuf},
    time::Duration,
};

pub fn executable(name: &str) -> DynResult<PathBuf> {
    let direct = Path::new(name);
    if direct.components().count() > 1 && direct.is_file() {
        return Ok(direct.canonicalize()?);
    }
    for directory in std::env::split_paths(&std::env::var_os("PATH").ok_or("PATH is unset")?) {
        let candidate = directory.join(name);
        if candidate.is_file() {
            return Ok(candidate.canonicalize()?);
        }
        if cfg!(windows) {
            let candidate = directory.join(format!("{name}.exe"));
            if candidate.is_file() {
                return Ok(candidate.canonicalize()?);
            }
        }
    }
    Err(format!("required tool is unavailable on PATH: {name}").into())
}

pub fn specification(
    program: &str,
    args: &[String],
    env: &[(&str, String)],
) -> DynResult<ProcessSpec> {
    let mut environment = std::env::vars_os()
        .filter(|(key, _)| {
            let key = key.to_string_lossy();
            !key.contains("TOKEN")
                && !key.contains("SECRET")
                && !key.contains("PASSWORD")
                && !matches!(
                    key.as_ref(),
                    "GITHUB_ENV" | "GITHUB_OUTPUT" | "GITHUB_PATH" | "GITHUB_STEP_SUMMARY"
                )
        })
        .map(|(key, value)| (key, Value::Public(value)))
        .collect::<std::collections::BTreeMap<_, _>>();
    for (key, value) in env {
        let value = if key.contains("TOKEN") || key.contains("SECRET") || key.contains("PASSWORD") {
            Value::Secret(value.into())
        } else {
            Value::Public(value.into())
        };
        environment.insert(OsString::from(key), value);
    }
    Ok(ProcessSpec {
        executable: executable(program)?,
        arguments: args
            .iter()
            .map(|value| Value::Public(value.into()))
            .collect(),
        cwd: std::env::current_dir()?.canonicalize()?,
        environment,
    })
}

pub fn limits(seconds: u64) -> Limits {
    Limits {
        execution: Duration::from_secs(seconds),
        graceful_shutdown: Duration::from_secs(20),
        forced_shutdown: Duration::from_secs(10),
        retained_bytes_per_stream: 1024 * 1024,
        readiness: Readiness::None,
        completion: Completion::Exit,
    }
}

pub fn capture(program: &str, args: &[String], env: &[(&str, String)]) -> DynResult<String> {
    capture_for(program, args, env, 180)
}

pub fn capture_for(
    program: &str,
    args: &[String],
    env: &[(&str, String)],
    seconds: u64,
) -> DynResult<String> {
    let report = process_supervision::supervise_raw(
        &specification(program, args, env)?,
        &limits(seconds),
        &Cancellation::default(),
        RawCaptureOptions {
            stdout: std::num::NonZeroUsize::new(16 * 1024 * 1024),
            stderr: None,
        },
    )?;
    if !report.process.success() {
        return Err(format!(
            "{program} failed: {:?}: {}",
            report.process.outcome,
            String::from_utf8_lossy(&report.process.stderr.bytes_retained)
        )
        .into());
    }
    Ok(String::from_utf8(
        report
            .stdout
            .ok_or("missing captured stdout")?
            .as_bytes()
            .to_vec(),
    )?)
}

pub fn run(program: &str, args: &[String], env: &[(&str, String)], seconds: u64) -> DynResult<()> {
    let report = process_supervision::supervise_inherited(
        &specification(program, args, env)?,
        &limits(seconds),
        &Cancellation::default(),
    )?;
    if report.outcome == process_supervision::Outcome::Exited
        && report.status.is_some_and(|status| status.success())
        && report.cleanup.complete
    {
        Ok(())
    } else {
        Err(format!(
            "{program} failed: {:?}, {:?}",
            report.outcome, report.status
        )
        .into())
    }
}

pub fn strings(values: &[&str]) -> Vec<String> {
    values.iter().map(|value| (*value).to_owned()).collect()
}

pub fn output(program: &str, arguments: &[String]) -> DynResult<String> {
    capture(program, arguments, &[])
}
