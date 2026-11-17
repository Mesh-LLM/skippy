//! Standalone repository boundaries, checked without compiling product crates.
use crate::command::DynResult;
use std::{
    fs,
    path::{Component, Path, PathBuf},
};

pub fn run() -> DynResult<()> {
    let root = std::env::current_dir()?.canonicalize()?;
    let mut files = Vec::new();
    collect(&root, &mut files)?;
    let mut failures = Vec::new();
    for path in files {
        let relative = path
            .strip_prefix(&root)?
            .to_string_lossy()
            .replace('\\', "/");
        if path.file_name().is_some_and(|name| name == "Cargo.toml") {
            manifests(&root, &path, &relative, &mut failures)?;
        }
        if path.extension().is_some_and(|extension| extension == "md") {
            links(&root, &path, &relative, &mut failures)?;
        }
        if active_source(&relative) {
            active_boundaries(&path, &relative, &mut failures)?;
        }
    }
    if failures.is_empty() {
        println!("standalone repository contracts passed");
        Ok(())
    } else {
        Err(format!(
            "standalone repository contract violations:\n{}",
            failures.join("\n")
        )
        .into())
    }
}
fn collect(directory: &Path, files: &mut Vec<PathBuf>) -> DynResult<()> {
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let path = entry.path();
        let name = entry.file_name();
        if [
            ".git",
            "target",
            ".deps",
            ".scratch",
            "node_modules",
            ".venv",
            "build",
            "dist",
        ]
        .iter()
        .any(|skip| name == *skip)
        {
            continue;
        }
        if entry.file_type()?.is_symlink() {
            continue;
        }
        if path.is_dir() {
            collect(&path, files)?;
        } else {
            files.push(path);
        }
    }
    Ok(())
}
fn manifests(
    root: &Path,
    path: &Path,
    relative: &str,
    failures: &mut Vec<String>,
) -> DynResult<()> {
    let manifest: toml::Value = toml::from_str(&fs::read_to_string(path)?)?;
    if let Some(package) = manifest.get("package") {
        if package
            .get("name")
            .and_then(toml::Value::as_str)
            .is_some_and(forbidden_package)
        {
            failures.push(format!("{relative}: forbidden downstream package"));
        }
        if relative.starts_with("crates/") {
            if package.get("publish").and_then(toml::Value::as_bool) != Some(false) {
                failures.push(format!(
                    "{relative}: production crates require publish = false"
                ));
            }
            if package
                .get("description")
                .and_then(toml::Value::as_str)
                .is_none_or(|text| text.trim().is_empty())
            {
                failures.push(format!("{relative}: missing crate description"));
            }
            if !path.with_file_name("README.md").is_file() {
                failures.push(format!("{relative}: missing crate README"));
            }
        }
    }
    dependencies(
        root,
        path.parent().ok_or("manifest has no parent")?,
        &manifest,
        relative,
        failures,
    )
}
fn forbidden_package(name: &str) -> bool {
    name.starts_with("mesh-") || name.starts_with("mesh_llm")
}
fn dependencies(
    root: &Path,
    directory: &Path,
    value: &toml::Value,
    label: &str,
    failures: &mut Vec<String>,
) -> DynResult<()> {
    if let Some(table) = value.as_table() {
        for (key, child) in table {
            if matches!(
                key.as_str(),
                "dependencies" | "dev-dependencies" | "build-dependencies" | "patch" | "replace"
            ) {
                dependency_entries(root, directory, child, label, failures)?;
            } else {
                dependencies(root, directory, child, label, failures)?;
            }
        }
    }
    Ok(())
}
fn dependency_entries(
    root: &Path,
    directory: &Path,
    value: &toml::Value,
    label: &str,
    failures: &mut Vec<String>,
) -> DynResult<()> {
    if let Some(table) = value.as_table() {
        for (name, entry) in table {
            if forbidden_package(name)
                || entry
                    .get("package")
                    .and_then(toml::Value::as_str)
                    .is_some_and(forbidden_package)
            {
                failures.push(format!("{label}: forbidden dependency {name}"));
            }
            if let Some(path) = entry.get("path").and_then(toml::Value::as_str) {
                match directory.join(path).canonicalize() {
                    Ok(path) if path.starts_with(root) => {}
                    _ => failures.push(format!(
                        "{label}: missing or escaping path dependency {name}: {path}"
                    )),
                }
            }
            if let Some(url) = entry.get("git").and_then(toml::Value::as_str)
                && url.to_ascii_lowercase().contains("mesh-llm/")
            {
                failures.push(format!("{label}: downstream Git dependency {name}"));
            }
            dependency_entries(root, directory, entry, label, failures)?;
        }
    }
    Ok(())
}
fn active_source(path: &str) -> bool {
    path != "tools/xtask/src/ci/contracts.rs"
        && !path.starts_with("llama_cpp/")
        && (path.starts_with("crates/")
            || path.starts_with("tools/")
            || path.starts_with("scripts/")
            || path.starts_with(".github/")
            || path.starts_with("just/")
            || path == "Justfile")
        && ["rs", "sh", "ps1", "yml", "yaml", "just", "cmd"]
            .iter()
            .any(|extension| path.ends_with(&format!(".{extension}")) || path == "Justfile")
}
fn active_boundaries(path: &Path, label: &str, failures: &mut Vec<String>) -> DynResult<()> {
    let source = fs::read_to_string(path)?;
    for (index, line) in source.lines().enumerate() {
        let lower = line.to_ascii_lowercase();
        let comment = line.trim_start();
        if comment.starts_with("//") || comment.starts_with('#') {
            continue;
        }
        if misplaced_worker_transport(label, line) {
            failures.push(format!(
                "{label}:{}: historical worker transport identifier outside its compatibility owner",
                index + 1
            ));
        }
        if [
            "github.com/mesh-llm/mesh-llm",
            "git@github.com:mesh-llm/",
            "mesh/crates/",
            "mesh/scripts/",
            "mesh/website/",
            "mesh_llm::",
        ]
        .iter()
        .any(|pattern| lower.contains(pattern))
        {
            failures.push(format!(
                "{label}:{}: active downstream source/build reference",
                index + 1
            ));
        }
    }
    Ok(())
}
fn misplaced_worker_transport(path: &str, line: &str) -> bool {
    path != "crates/skippy-model-package/src/jobs/delivery.rs"
        && !path.starts_with("crates/skippy-model-package/src/jobs/delivery/")
        && [
            "MESH_HF_JOB_INPUT",
            "MESH_HF_JOB_REQUEST",
            "MESH_HF_PUBLICATION_TOKEN",
        ]
        .iter()
        .any(|identifier| line.contains(identifier))
}
fn links(root: &Path, file: &Path, label: &str, failures: &mut Vec<String>) -> DynResult<()> {
    let source = fs::read_to_string(file)?;
    let mut fence = false;
    for (index, line) in source.lines().enumerate() {
        let line = line.trim();
        if line.starts_with("```") || line.starts_with("~~~") {
            fence = !fence;
            continue;
        }
        if fence {
            continue;
        }
        let mut remainder = line;
        while let Some((_, tail)) = remainder.split_once("](") {
            let Some((target, rest)) = tail.split_once(')') else {
                break;
            };
            check_link(root, file, label, index + 1, target, failures);
            remainder = rest;
        }
        if line.starts_with('[')
            && !line.starts_with("[^")
            && let Some((_, target)) = line.split_once("]:")
        {
            check_link(root, file, label, index + 1, target, failures);
        }
    }
    Ok(())
}
fn check_link(
    root: &Path,
    file: &Path,
    label: &str,
    line: usize,
    raw: &str,
    failures: &mut Vec<String>,
) {
    let raw = raw.trim();
    let target = if let Some(raw) = raw.strip_prefix('<') {
        raw.split('>').next().unwrap_or(raw)
    } else {
        raw.split_whitespace().next().unwrap_or("")
    };
    let target = target.split(['#', '?']).next().unwrap_or("");
    if target.is_empty()
        || target.contains("://")
        || target.starts_with("mailto:")
        || target.starts_with("data:")
    {
        return;
    }
    let decoded = target.replace("%20", " ");
    let path = if let Some(relative) = decoded.strip_prefix('/') {
        root.join(relative)
    } else {
        file.parent().unwrap_or(root).join(&decoded)
    };
    let normalized = normalize(&path);
    if !normalized.starts_with(root) || !normalized.exists() {
        failures.push(format!("{label}:{line}: broken local link {target}"));
    }
}
fn normalize(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for component in path.components() {
        match component {
            Component::ParentDir => {
                result.pop();
            }
            Component::CurDir => {}
            value => result.push(value.as_os_str()),
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn historical_worker_transport_is_confined_to_delivery() {
        for identifier in [
            "MESH_HF_JOB_INPUT",
            "MESH_HF_JOB_REQUEST",
            "MESH_HF_PUBLICATION_TOKEN",
        ] {
            for path in [
                "crates/skippy-model-package/src/jobs/delivery.rs",
                "crates/skippy-model-package/src/jobs/delivery/request_transport.rs",
                "crates/skippy-model-package/src/jobs/delivery/generic_cli/tests.rs",
            ] {
                assert!(!misplaced_worker_transport(path, identifier));
            }
            for path in [
                "crates/skippy-model-package/src/jobs/delivery_new.rs",
                "crates/skippy-model-package/src/jobs/delivery_new/worker.rs",
                "scripts/publish.sh",
                ".github/workflows/release.yml",
            ] {
                assert!(misplaced_worker_transport(path, identifier));
            }
        }
        assert!(!misplaced_worker_transport(
            "scripts/worker.sh",
            "SKIPPY_JOB_INPUT"
        ));
    }
    #[test]
    fn dependency_cannot_escape_checkout() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("repo");
        fs::create_dir(&root).unwrap();
        let value: toml::Value =
            toml::from_str("[dependencies]\nforeign={path='..'}\nmesh-runtime='1'").unwrap();
        let mut failures = Vec::new();
        dependencies(&root, &root, &value, "Cargo.toml", &mut failures).unwrap();
        assert_eq!(failures.len(), 2);
    }
    #[test]
    fn markdown_links_resolve_from_owning_file() {
        let temp = tempfile::tempdir().unwrap();
        let file = temp.path().join("README.md");
        fs::write(temp.path().join("guide.md"), "ok").unwrap();
        let mut failures = Vec::new();
        check_link(
            temp.path(),
            &file,
            "README.md",
            1,
            "guide.md#section",
            &mut failures,
        );
        check_link(
            temp.path(),
            &file,
            "README.md",
            2,
            "missing.md",
            &mut failures,
        );
        check_link(
            temp.path(),
            &file,
            "README.md",
            3,
            "../outside.md",
            &mut failures,
        );
        assert_eq!(failures.len(), 2);
    }

    #[test]
    fn footnotes_are_prose_but_their_inline_links_are_checked() {
        let temp = tempfile::tempdir().unwrap();
        let file = temp.path().join("README.md");
        fs::write(
            &file,
            "[^note]: Follow-up prose with [a link](missing.md).\n[guide]: missing-guide.md\n",
        )
        .unwrap();
        let mut failures = Vec::new();
        links(temp.path(), &file, "README.md", &mut failures).unwrap();
        assert_eq!(failures.len(), 2);
        assert!(failures[0].ends_with("missing.md"));
        assert!(failures[1].ends_with("missing-guide.md"));
    }
}
