use crate::command::DynResult;
use std::collections::{BTreeMap, BTreeSet};

pub(super) fn projection(text: &str) -> DynResult<String> {
    let selected = [
        "automation-run",
        "inference-build",
        "inference-build-uncached",
        "inference-execute",
        "llama-build",
        "llama-prepare",
    ];
    let (mut recipes, globals) = parse(text)?;
    for name in selected {
        if !recipes.contains_key(name) {
            return Err(format!("missing native Just recipe: {name}").into());
        }
    }
    let needed = closure(&recipes, &selected)?;
    recipes.retain(|name, _| needed.contains(name));
    let mut consumed = BTreeSet::new();
    let mut body = serde_json::to_string(&recipes)?;
    loop {
        let mut added = false;
        for (key, value) in &globals {
            let used = key.starts_with("export ")
                || key.starts_with("set ")
                || !value.contains(":=")
                || body
                    .split(|c: char| !c.is_alphanumeric() && c != '_')
                    .any(|word| word == key);
            if used && consumed.insert(value.clone()) {
                body.push_str(value);
                added = true;
            }
        }
        if !added {
            break;
        }
    }
    Ok(serde_json::to_string(&(recipes, consumed))?)
}

type Recipes = BTreeMap<String, Vec<String>>;
type Globals = BTreeMap<String, String>;

fn parse(text: &str) -> DynResult<(Recipes, Globals)> {
    let mut recipes = BTreeMap::<String, Vec<String>>::new();
    let mut globals = BTreeMap::new();
    let mut active = None;
    let mut attributes = Vec::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || (trimmed.starts_with('#') && !trimmed.starts_with("#!")) {
            continue;
        }
        if line.starts_with([' ', '\t']) {
            if let Some(name) = &active {
                recipes
                    .get_mut(name)
                    .ok_or("recipe disappeared")?
                    .push(trimmed.to_owned());
            }
            continue;
        }
        active = None;
        if trimmed.starts_with('[') {
            attributes.push(trimmed.to_owned());
            continue;
        }
        if let Some((key, _)) = trimmed.split_once(":=") {
            globals.insert(key.trim().to_owned(), trimmed.to_owned());
            continue;
        }
        if let Some((header, _)) = trimmed.split_once(':') {
            let name = header
                .split_whitespace()
                .next()
                .ok_or("empty Just recipe")?;
            if attributes
                .iter()
                .any(|attribute| matches!(attribute.as_str(), "[windows]" | "[macos]"))
            {
                attributes.clear();
                continue;
            }
            let mut lines = std::mem::take(&mut attributes);
            lines.push(trimmed.to_owned());
            recipes.insert(name.to_owned(), lines);
            active = Some(name.to_owned());
            attributes.clear();
        } else {
            // Imports, modules and global settings can affect recipe resolution.
            globals.insert(trimmed.to_owned(), trimmed.to_owned());
        }
    }
    Ok((recipes, globals))
}

fn closure(recipes: &Recipes, selected: &[&str]) -> DynResult<BTreeSet<String>> {
    let mut needed = selected
        .iter()
        .map(|name| (*name).to_owned())
        .collect::<BTreeSet<_>>();
    loop {
        let mut additions = BTreeSet::new();
        for name in &needed {
            let lines = recipes.get(name).ok_or("missing consumed Just recipe")?;
            // Follow named header dependencies and literal recipe calls. Unknown
            // dynamic calls must be made explicit in the reviewed input policy.
            for line in lines {
                let header = line
                    .split_once(':')
                    .filter(|(header, _)| header.split_whitespace().next() == Some(name.as_str()))
                    .map(|(_, dependencies)| dependencies);
                let tokens = line.split_whitespace().collect::<Vec<_>>();
                if let Some(header) = header {
                    for dependency in header.split_whitespace() {
                        let dependency = dependency.trim_matches(['(', ')']);
                        if recipes.contains_key(dependency) {
                            additions.insert(dependency.to_owned());
                        }
                    }
                }
                for call in tokens.windows(2) {
                    if matches!(call[0], "just" | "@just") {
                        if !recipes.contains_key(call[1]) {
                            return Err(
                                "unresolved native Just invocation; extend the input policy".into(),
                            );
                        }
                        additions.insert(call[1].to_owned());
                    }
                }
            }
        }
        let previous = needed.len();
        needed.extend(additions);
        if needed.len() == previous {
            break;
        }
    }
    Ok(needed)
}
