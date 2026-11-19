//! Project only configuration consumed by native inference jobs.
use crate::command::DynResult;
use serde_json::Value;
use std::collections::BTreeMap;

pub(super) fn json(text: &str) -> DynResult<String> {
    Ok(serde_json::to_string(&serde_json::from_str::<Value>(
        text,
    )?)?)
}

pub(super) fn templates(text: &str) -> DynResult<String> {
    let value: Value = serde_json::from_str(text)?;
    let templates = value["templates"]
        .as_array()
        .ok_or("invalid native templates")?;
    let mut selected = BTreeMap::new();
    for template in templates {
        let key = template["key"]
            .as_str()
            .ok_or("native template missing key")?;
        if !matches!(key, "inference-pr-standalone" | "inference-pr-split") {
            continue;
        }
        let mut fields = serde_json::Map::new();
        for name in [
            "build",
            "command",
            "env",
            "target",
            "roles",
            "rendezvous",
            "budget",
            "retain",
        ] {
            if let Some(value) = template.get(name) {
                fields.insert(name.to_owned(), value.clone());
            }
        }
        if let Some(inputs) = template.get("inputs").and_then(Value::as_array) {
            let inputs = inputs
                .iter()
                .map(|input| {
                    let mut input = input.clone();
                    if let Some(fields) = input.as_object_mut() {
                        fields.remove("label");
                        fields.remove("description");
                        if let Some(choices) =
                            fields.get_mut("choices").and_then(Value::as_array_mut)
                        {
                            for choice in choices {
                                if let Some(choice) = choice.as_object_mut() {
                                    choice.remove("label");
                                }
                            }
                        }
                    }
                    input
                })
                .collect::<Vec<_>>();
            fields.insert("inputs".into(), Value::Array(inputs));
        }
        if selected.insert(key, fields).is_some() {
            return Err("duplicate inference template".into());
        }
    }
    if selected.len() != 2 {
        return Err("missing PR inference template".into());
    }
    Ok(serde_json::to_string(&(value.get("version"), selected))?)
}

pub(super) fn cargo_config(text: &str) -> DynResult<String> {
    let config: toml::Value = toml::from_str(text)?;
    let mut selected = toml::Table::new();
    if let Some(table) = config.as_table() {
        for (key, value) in table {
            if key == "target" {
                if let Some(targets) = value.as_table() {
                    let linux: toml::Table = targets
                        .iter()
                        .filter(|(target, _)| super::rust::possible_target(target).unwrap_or(true))
                        .map(|(k, v)| (k.clone(), v.clone()))
                        .collect();
                    selected.insert(key.clone(), toml::Value::Table(linux));
                }
            } else if key == "alias" {
                if let Some(value) = value.get("xtool") {
                    selected.insert("xtool".into(), value.clone());
                }
            } else {
                selected.insert(key.clone(), value.clone());
            }
        }
    }
    Ok(toml::Value::Table(selected).to_string())
}
