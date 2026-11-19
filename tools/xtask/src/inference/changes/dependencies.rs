//! The libraries used by the native PR workload, excluding development targets.
use super::{
    manifest, rust,
    source::{Source, relative},
};
use crate::command::DynResult;
use std::collections::{BTreeMap, BTreeSet};
use toml::Value;

// These are used by candidate-side execution/build helpers. The remaining
// xtask dependencies serve the protected controller or unrelated automation.
pub(super) const WORKER_DEPENDENCIES: &[&str] = &[
    "process-supervision",
    "serde",
    "serde_json",
    "sha2",
    "hex",
    "tempfile",
    "libc",
];
pub(super) const WORKER_FILES: &[&str] = &[
    "tools/xtask/src/inference/execute.rs",
    "tools/xtask/src/inference/artifacts.rs",
    "tools/xtask/src/inference/build.rs",
    "tools/xtask/src/inference/gpu.rs",
    "tools/xtask/src/inference/provenance.rs",
    "tools/xtask/src/inference/smoke.rs",
    "tools/xtask/src/execution.rs",
    "tools/xtask/src/artifact.rs",
    "tools/xtask/src/command.rs",
];

#[derive(Default)]
pub(super) struct Graph {
    pub manifests: BTreeMap<String, Value>,
    pub sources: BTreeSet<String>,
    pub directories: BTreeSet<String>,
    pub assets: BTreeSet<String>,
    dependencies: BTreeSet<(String, String)>,
}

pub(super) fn document(source: &mut impl Source, revision: &str, path: &str) -> DynResult<Value> {
    let text = source
        .read(revision, path)?
        .ok_or_else(|| format!("missing manifest: {path}"))?;
    toml::from_str(&text).map_err(Into::into)
}

pub(super) fn graph(source: &mut impl Source, revision: &str) -> DynResult<Graph> {
    let workspace = document(source, revision, "Cargo.toml")?;
    let mut result = Graph::default();
    collect(
        source,
        revision,
        "crates/skippy-cli/Cargo.toml",
        &workspace,
        &mut result,
    )?;
    collect(
        source,
        revision,
        "tools/xtask/Cargo.toml",
        &workspace,
        &mut result,
    )?;
    result.sources.extend(rust::modules(
        source,
        revision,
        &WORKER_FILES
            .iter()
            .map(|path| (*path).to_owned())
            .collect::<Vec<_>>(),
    )?);
    result.assets = rust::assets(source, revision, &result.sources)?;
    Ok(result)
}

fn collect(
    source: &mut impl Source,
    revision: &str,
    path: &str,
    workspace: &Value,
    graph: &mut Graph,
) -> DynResult<()> {
    if graph.manifests.contains_key(path) {
        return Ok(());
    }
    let manifest = document(source, revision, path)?;
    let directory = path
        .strip_suffix("/Cargo.toml")
        .ok_or("invalid crate manifest path")?;
    let worker = directory == "tools/xtask";
    let mut projection = manifest::projection(&manifest, workspace, directory)?;
    // Mark before descending through dependencies, so cycles cannot recurse.
    graph
        .manifests
        .insert(path.to_owned(), Value::Boolean(false));
    for (kind, dependencies) in manifest::tables(&manifest, "") {
        for (alias, declaration) in dependencies {
            if worker && !WORKER_DEPENDENCIES.contains(&alias.as_str()) {
                continue;
            }
            let (resolved, inherited) = manifest::dependency(declaration, workspace, alias)?;
            let key = format!("{kind}:{alias}");
            projection.insert(key, resolved.clone());
            if let Some(dependency) = resolved.get("path").and_then(Value::as_str) {
                let dependency = relative(if inherited { "" } else { directory }, dependency)?;
                collect(
                    source,
                    revision,
                    &format!("{dependency}/Cargo.toml"),
                    workspace,
                    graph,
                )?;
            } else {
                let name = resolved
                    .get("package")
                    .and_then(Value::as_str)
                    .unwrap_or(alias);
                let requirement = resolved
                    .as_str()
                    .or_else(|| resolved.get("version").and_then(Value::as_str))
                    .unwrap_or("*");
                graph
                    .dependencies
                    .insert((name.to_owned(), requirement.to_owned()));
            }
        }
    }
    graph
        .manifests
        .insert(path.to_owned(), Value::Table(projection));
    if worker {
        return Ok(());
    }
    graph.directories.insert(directory.to_owned());
    let mut roots = Vec::new();
    if directory == "crates/skippy-cli" {
        let binary = manifest
            .get("bin")
            .and_then(Value::as_array)
            .and_then(|bins| {
                bins.iter()
                    .find(|bin| bin.get("name").and_then(Value::as_str) == Some("skippy"))
            })
            .and_then(|bin| bin.get("path"))
            .and_then(Value::as_str)
            .unwrap_or("src/main.rs");
        roots.push(relative(directory, binary)?);
    } else {
        let lib = manifest
            .get("lib")
            .and_then(|lib| lib.get("path"))
            .and_then(Value::as_str)
            .unwrap_or("src/lib.rs");
        roots.push(relative(directory, lib)?);
    }
    let build = manifest.get("package").and_then(|p| p.get("build"));
    if build != Some(&Value::Boolean(false)) {
        let build = relative(
            directory,
            build.and_then(Value::as_str).unwrap_or("build.rs"),
        )?;
        if source.read(revision, &build)?.is_some() {
            roots.push(build);
        }
    }
    graph
        .sources
        .extend(rust::modules(source, revision, &roots)?);
    Ok(())
}

pub(super) fn lock_projection(
    source: &mut impl Source,
    revision: &str,
    graph: &Graph,
) -> DynResult<Value> {
    let lock = document(source, revision, "Cargo.lock")?;
    let packages = lock
        .get("package")
        .and_then(Value::as_array)
        .ok_or("invalid Cargo lockfile")?;
    let mut pending = Vec::new();
    for (name, requirement) in &graph.dependencies {
        let requirement = semver::VersionReq::parse(requirement)?;
        let selected = packages
            .iter()
            .filter(|package| {
                package.get("name").and_then(Value::as_str) == Some(name)
                    && package
                        .get("version")
                        .and_then(Value::as_str)
                        .and_then(|v| semver::Version::parse(v).ok())
                        .is_some_and(|v| requirement.matches(&v))
            })
            .collect::<Vec<_>>();
        if selected.len() != 1 {
            return Err(format!("unresolved or ambiguous production dependency: {name}").into());
        }
        pending.extend(selected);
    }
    let mut selected = BTreeMap::new();
    while let Some(package) = pending.pop() {
        let name = package
            .get("name")
            .and_then(Value::as_str)
            .ok_or("missing lock package name")?;
        let version = package
            .get("version")
            .and_then(Value::as_str)
            .ok_or("missing lock package version")?;
        let key = format!(
            "{name} {version} {}",
            package.get("source").and_then(Value::as_str).unwrap_or("")
        );
        if selected.insert(key, package.clone()).is_some() {
            continue;
        }
        if let Some(dependencies) = package.get("dependencies").and_then(Value::as_array) {
            for dependency in dependencies {
                let parts = dependency
                    .as_str()
                    .ok_or("invalid lock dependency")?
                    .split_whitespace()
                    .collect::<Vec<_>>();
                let matches = packages
                    .iter()
                    .filter(|p| {
                        p.get("name").and_then(Value::as_str) == parts.first().copied()
                            && (parts.len() < 2
                                || p.get("version").and_then(Value::as_str)
                                    == parts.get(1).copied())
                    })
                    .collect::<Vec<_>>();
                if matches.len() != 1 {
                    return Err("ambiguous production lock dependency".into());
                }
                pending.extend(matches);
            }
        }
    }
    Ok(Value::Array(selected.into_values().collect()))
}
