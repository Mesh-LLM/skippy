//! Deterministic, unpublished llama.cpp update candidates.
pub(crate) mod evidence;
mod qualification;
mod regeneration;
pub(crate) mod resolution;
pub(crate) mod source;
use crate::command::DynResult;
use std::io::Write;

pub(crate) fn run(args: &[String]) -> DynResult<()> {
    match args {
        [verb, rest @ ..] if verb == "resolve" => resolve(rest),
        [verb, sha] if verb == "prepare" => source::prepare(sha),
        [verb, bundle, sha] if verb == "restore" => source::restore(bundle, sha),
        [verb, option, root] if verb == "prepared-source" && option == "--root" => {println!("{}",source::prepared_head(std::path::Path::new(root))?);Ok(())},
        [verb, rest @ ..] if verb == "certify" => certify(rest),
        [verb, rest @ ..] if verb == "publish" => crate::release::publish_upgrade(rest),
        _ => Err("upgrade {resolve [--upstream SHA]|prepare SHA|restore BUNDLE SHA|certify --backend BACKEND --suite supported}".into()),
    }
}

fn resolve(args: &[String]) -> DynResult<()> {
    let mut upstream = None;
    let mut force = false;
    if !args.len().is_multiple_of(2) {
        return Err("resolve options require values".into());
    }
    for [key, value] in args.as_chunks::<2>().0 {
        match key.as_str() {
            "--upstream" if upstream.is_none() => upstream = Some(value.clone()),
            "--force-certify" => force = value.parse()?,
            _ => return Err("unknown or duplicate resolve option".into()),
        }
    }
    let sha = match upstream.filter(|value| !value.is_empty()) {
        Some(value) => value,
        None => resolution::latest()?,
    };
    source::validate_sha(&sha)?;
    let current = std::fs::read_to_string("llama_cpp/upstream.txt")?;
    let existing = resolution::open_upgrade(&sha)?;
    if let Some(url) = &existing {
        output("existing_pr", url)?;
    }
    output("upstream_sha", &sha)?;
    output(
        "changed",
        if current.trim() == sha {
            "false"
        } else {
            "true"
        },
    )?;
    output(
        "certify",
        if existing.is_none() && (current.trim() != sha || force) {
            "true"
        } else {
            "false"
        },
    )?;
    Ok(())
}

fn output(key: &str, value: &str) -> DynResult<()> {
    if key.contains(['\r', '\n', '=']) || value.contains(['\r', '\n']) {
        return Err("invalid workflow output".into());
    }
    println!("{key}={value}");
    if let Some(path) = std::env::var_os("GITHUB_OUTPUT") {
        writeln!(
            std::fs::OpenOptions::new().append(true).open(path)?,
            "{key}={value}"
        )?;
    }
    Ok(())
}

fn certify(args: &[String]) -> DynResult<()> {
    qualification::run(args)
}
