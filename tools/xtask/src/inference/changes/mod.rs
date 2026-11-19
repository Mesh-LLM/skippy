//! Explainable admission based on immutable production inputs, never candidate execution.
mod config;
mod dependencies;
mod manifest;
mod policy;
mod recipes;
mod rust;
mod source;
mod syntax;
#[cfg(test)]
mod tests;
mod workspace;

use crate::command::DynResult;
use serde_json::Value;
pub(super) use source::GitSource;
use source::Source;
use std::collections::BTreeSet;

pub(super) fn inspect(base: &str, head: &str, output: &std::path::Path) -> DynResult<()> {
    let mut source = GitSource::local(base, head)?;
    let files = source.changes(base, head)?;
    let decision = assess(&files, &mut source, base, head)?;
    let report = serde_json::json!({"base":base,"head":head,"required":!decision.reasons.is_empty(),"decision":decision});
    crate::command::write_json_file(output, &report)?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}

#[derive(Default, serde::Serialize)]
pub(super) struct Decision {
    pub reasons: Vec<String>,
    pub ignored: Vec<String>,
}

fn paths(pages: &Value) -> DynResult<BTreeSet<String>> {
    let pages = pages
        .as_array()
        .filter(|pages| !pages.is_empty())
        .ok_or("invalid files pages")?;
    let mut paths = BTreeSet::new();
    let mut count = 0;
    for page in pages {
        let files = page.as_array().ok_or("invalid files response")?;
        count += files.len();
        for file in files {
            paths.insert(
                file["filename"]
                    .as_str()
                    .ok_or("missing changed-file path")?
                    .to_owned(),
            );
            if let Some(previous) = file.get("previous_filename") {
                paths.insert(
                    previous
                        .as_str()
                        .ok_or("invalid previous file path")?
                        .to_owned(),
                );
            }
        }
    }
    if count >= 3000 {
        return Err("PR file list may be truncated; cannot determine inference inputs".into());
    }
    for path in &paths {
        if source::relative("", path)? != *path {
            return Err("non-canonical changed-file path".into());
        }
    }
    Ok(paths)
}

pub(super) fn assess(
    pages: &Value,
    source: &mut impl Source,
    base: &str,
    head: &str,
) -> DynResult<Decision> {
    let paths = paths(pages)?;
    let before = dependencies::graph(source, base)?;
    let after = dependencies::graph(source, head)?;
    let mut decision = Decision::default();
    // Manifest edits can change the production dependency graph even if the
    // changed manifest belonged to a previously unrelated crate.
    if before.manifests != after.manifests {
        decision
            .reasons
            .push("production Cargo dependency/build configuration changed".into());
    }
    for path in paths {
        if path == "Cargo.toml" {
            if workspace::projection(source, base, &before)?
                != workspace::projection(source, head, &after)?
            {
                decision.reasons.push(
                    "Cargo.toml: native build profile or production dependency override changed"
                        .into(),
                );
            } else {
                decision
                    .ignored
                    .push("Cargo.toml: native profiles and overrides unchanged".into());
            }
            continue;
        }
        if path == "Cargo.lock" {
            if dependencies::lock_projection(source, base, &before)?
                != dependencies::lock_projection(source, head, &after)?
            {
                decision
                    .reasons
                    .push("Cargo.lock: production dependency resolution changed".into());
            } else {
                decision
                    .ignored
                    .push(format!("{path}: only unrelated dependencies changed"));
            }
            continue;
        }
        let kind = policy::input(&path, &before).or_else(|| policy::input(&path, &after));
        let Some(kind) = kind else {
            decision
                .ignored
                .push(format!("{path}: outside native PR inference inputs"));
            continue;
        };
        if matches!(kind, policy::Input::Bytes) {
            decision.reasons.push(format!(
                "{path}: native source, runtime data or build input changed"
            ));
            continue;
        }
        let mut project = |revision| -> DynResult<Option<String>> {
            source
                .read(revision, &path)?
                .map(|text| policy::projection(kind, &text))
                .transpose()
        };
        if project(base)? != project(head)? {
            decision
                .reasons
                .push(format!("{path}: production inference content changed"));
        } else {
            decision.ignored.push(format!(
                "{path}: production content unchanged (tests, comments or unrelated configuration)"
            ));
        }
    }
    Ok(decision)
}
