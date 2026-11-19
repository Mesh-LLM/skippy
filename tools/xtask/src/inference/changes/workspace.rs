//! Compare only profiles and overrides used by the native development build.
use super::{
    dependencies::{self, Graph},
    source::Source,
};
use crate::command::DynResult;
use std::collections::BTreeSet;
use toml::Value;

pub(super) fn projection(
    source: &mut impl Source,
    revision: &str,
    graph: &Graph,
) -> DynResult<String> {
    let value = dependencies::document(source, revision, "Cargo.toml")?;
    let mut names = graph
        .manifests
        .values()
        .filter_map(|m| m.get("package")?.get("name")?.as_str())
        .map(str::to_owned)
        .collect::<BTreeSet<_>>();
    if let Value::Array(packages) = dependencies::lock_projection(source, revision, graph)? {
        names.extend(
            packages
                .iter()
                .filter_map(|p| p.get("name")?.as_str())
                .map(str::to_owned),
        );
    }
    let mut result = toml::Table::new();
    if let Some(profile) = value
        .get("profile")
        .and_then(|p| p.get("dev"))
        .and_then(Value::as_table)
    {
        let mut profile = profile.clone();
        if let Some(Value::Table(packages)) = profile.get_mut("package") {
            packages.retain(|name, _| name == "*" || names.contains(name));
            if packages.is_empty() {
                profile.remove("package");
            }
        }
        if !profile.is_empty() {
            result.insert("dev".into(), Value::Table(profile));
        }
    }
    for key in ["patch", "replace"] {
        if let Some(overrides) = value.get(key).and_then(Value::as_table) {
            let mut overrides = overrides.clone();
            if key == "patch" {
                for (_, group) in overrides.iter_mut() {
                    if let Some(group) = group.as_table_mut() {
                        group.retain(|alias, declaration| {
                            names.contains(
                                declaration
                                    .get("package")
                                    .and_then(Value::as_str)
                                    .unwrap_or(alias),
                            )
                        });
                    }
                }
                overrides.retain(|_, group| !group.as_table().is_some_and(toml::Table::is_empty));
            } else {
                overrides.retain(|name, _| names.contains(name.split(':').next().unwrap_or(name)));
            }
            if !overrides.is_empty() {
                result.insert(key.into(), Value::Table(overrides));
            }
        }
    }
    if let Some(resolver) = value.get("workspace").and_then(|w| w.get("resolver")) {
        result.insert("resolver".into(), resolver.clone());
    }
    Ok(Value::Table(result).to_string())
}
