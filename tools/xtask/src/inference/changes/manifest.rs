use super::rust;
use crate::command::DynResult;
use toml::Value;

pub(super) fn tables<'a>(manifest: &'a Value, prefix: &str) -> Vec<(String, &'a toml::Table)> {
    let mut result = Vec::new();
    for name in ["dependencies", "build-dependencies"] {
        if let Some(table) = manifest.get(name).and_then(Value::as_table) {
            result.push((format!("{prefix}{name}"), table));
        }
    }
    if let Some(targets) = manifest.get("target").and_then(Value::as_table) {
        for (name, target) in targets {
            if rust::possible_target(name).unwrap_or(true) {
                result.extend(tables(target, &format!("target.{name}.")));
            }
        }
    }
    result
}

pub(super) fn projection(
    manifest: &Value,
    workspace: &Value,
    directory: &str,
) -> DynResult<toml::Table> {
    let mut projection = toml::Table::new();
    if let Some(package) = manifest.get("package").and_then(Value::as_table) {
        let mut fields = toml::Table::new();
        for key in [
            "name",
            "version",
            "edition",
            "rust-version",
            "build",
            "links",
        ] {
            if let Some(value) = package.get(key) {
                let inherited = value.get("workspace").and_then(Value::as_bool) == Some(true);
                let value = if inherited {
                    workspace
                        .get("workspace")
                        .and_then(|w| w.get("package"))
                        .and_then(|p| p.get(key))
                        .ok_or("missing inherited package field")?
                } else {
                    value
                };
                fields.insert(key.to_owned(), value.clone());
            }
        }
        projection.insert("package".into(), Value::Table(fields));
    }
    for key in ["lib", "features"] {
        if let Some(value) = manifest.get(key) {
            projection.insert(key.into(), value.clone());
        }
    }
    if directory == "crates/skippy-cli"
        && let Some(binaries) = manifest.get("bin")
    {
        projection.insert("bin".into(), binaries.clone());
    }
    Ok(projection)
}

pub(super) fn dependency(
    declaration: &Value,
    workspace: &Value,
    alias: &str,
) -> DynResult<(Value, bool)> {
    let inherited = declaration.get("workspace").and_then(Value::as_bool) == Some(true);
    let mut resolved = if inherited {
        workspace
            .get("workspace")
            .and_then(|w| w.get("dependencies"))
            .and_then(|d| d.get(alias))
            .ok_or("missing workspace dependency")?
            .clone()
    } else {
        declaration.clone()
    };
    if let Value::String(version) = resolved {
        resolved = Value::Table(toml::Table::from_iter([(
            "version".into(),
            Value::String(version),
        )]));
    }
    if inherited {
        let table = resolved
            .as_table_mut()
            .ok_or("invalid inherited dependency")?;
        for (key, value) in declaration
            .as_table()
            .ok_or("invalid dependency override")?
        {
            if key != "workspace" {
                if key == "features" {
                    let mut features = table
                        .get(key)
                        .and_then(Value::as_array)
                        .cloned()
                        .unwrap_or_default();
                    features.extend(
                        value
                            .as_array()
                            .ok_or("invalid dependency features")?
                            .clone(),
                    );
                    table.insert(key.clone(), Value::Array(features));
                } else {
                    table.insert(key.clone(), value.clone());
                }
            }
        }
    }
    Ok((resolved, inherited))
}
