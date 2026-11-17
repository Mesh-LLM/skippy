//! Tag admission and immutable release inventory. Publication remains workflow-owned.
mod upgrade_publication;
use crate::{
    ci::{artifacts, execution, matrix::Matrix},
    command::DynResult,
};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, fs, io::Write, path::Path};

pub(crate) fn publish_upgrade(args: &[String]) -> DynResult<()> {
    upgrade_publication::run(args)
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Release {
    schema_version: u32,
    tag: String,
    source: String,
    upstream_pin: String,
    assets: BTreeMap<String, String>,
    variants: Vec<String>,
}

pub fn run(args: &[String]) -> DynResult<()> {
    match args
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["validate", tag] => validate(tag),
        ["prepare", tag, directory] => prepare(tag, Path::new(directory)),
        ["verify", directory] => verify(Path::new(directory)),
        ["admit-draft", path] => admit_draft(Path::new(path)),
        ["select", tag, path] => select(tag, Path::new(path)),
        ["upload-plan", directory, path] => upload_plan(Path::new(directory), Path::new(path)),
        _ => Err(
            "usage: release {validate TAG|prepare TAG INPUT_DIR|verify DIR|admit-draft JSON}"
                .into(),
        ),
    }
}

fn version(tag: &str, manifest: &str) -> DynResult<semver::Version> {
    let raw = tag
        .strip_prefix('v')
        .ok_or("release tag must begin with v")?;
    let version = semver::Version::parse(raw)?;
    if raw != version.to_string() {
        return Err("release tag is not canonical SemVer".into());
    }
    let cargo: toml::Value = toml::from_str(manifest)?;
    if cargo["workspace"]["package"]["version"].as_str() != Some(raw) {
        return Err("tag version differs from committed workspace version".into());
    }
    Ok(version)
}

fn validate(tag: &str) -> DynResult<()> {
    let parsed = version(tag, &fs::read_to_string("Cargo.toml")?)?;
    let source = artifacts::source()?;
    let tagged = execution::capture(
        "git",
        &execution::strings(&["rev-parse", &format!("refs/tags/{tag}^{{commit}}")]),
        &[],
    )?;
    if tagged.trim() != source {
        return Err("checkout is not the exact tagged commit".into());
    }
    execution::run(
        "git",
        &execution::strings(&[
            "merge-base",
            "--is-ancestor",
            &source,
            "refs/remotes/origin/main",
        ]),
        &[],
        60,
    )?;
    if !execution::capture(
        "git",
        &execution::strings(&["status", "--porcelain", "--untracked-files=no"]),
        &[],
    )?
    .trim()
    .is_empty()
    {
        return Err("release checkout has tracked modifications".into());
    }
    github_output(
        "prerelease",
        if parsed.pre.is_empty() {
            "false"
        } else {
            "true"
        },
    )?;
    github_output("source", &source)
}

fn prepare(tag: &str, input: &Path) -> DynResult<()> {
    validate(tag)?;
    let matrix = Matrix::load()?;
    let output = Path::new("dist/release");
    fs::create_dir_all(output)?;
    if fs::read_dir(output)?.next().is_some() {
        return Err("release output directory must be empty".into());
    }
    let mut assets = BTreeMap::new();
    for row in &matrix.runtimes {
        let directory = input.join(format!("release-product-{}", row.id));
        let workspace = tempfile::tempdir()?;
        artifacts::unpack(&directory, workspace.path())?;
        let host = matrix.host(&row.host)?;
        artifacts::verify(workspace.path(), "product", &host.target, &row.id)?;
        let evidence: serde_json::Value =
            serde_json::from_slice(&fs::read(directory.join("evidence/smoke.json"))?)?;
        if evidence["source"] != artifacts::source()?
            || evidence["variant"] != row.id
            || evidence["backend"] != row.backend
        {
            return Err(format!("missing or mismatched smoke evidence for {}", row.id).into());
        }
        let filename = format!("skippy-{tag}-{}.tar.gz", row.id);
        let destination = output.join(&filename);
        fs::copy(
            directory.join(format!("skippy-{}.tar.gz", row.id)),
            &destination,
        )?;
        let digest = artifacts::digest(&destination)?;
        fs::write(
            output.join(format!("{filename}.sha256")),
            format!("{digest}  {filename}\n"),
        )?;
        assets.insert(filename, digest);
    }
    let release = Release {
        schema_version: 1,
        tag: tag.into(),
        source: artifacts::source()?,
        upstream_pin: fs::read_to_string("llama_cpp/upstream.txt")?.trim().into(),
        assets,
        variants: matrix.runtimes.iter().map(|row| row.id.clone()).collect(),
    };
    artifacts::write_json(&output.join("release.json"), &release)?;
    fs::write(
        output.join("release.json.sha256"),
        format!(
            "{}  release.json\n",
            artifacts::digest(&output.join("release.json"))?
        ),
    )?;
    verify(output)
}

fn verify(directory: &Path) -> DynResult<()> {
    let release: Release = serde_json::from_slice(&fs::read(directory.join("release.json"))?)?;
    let matrix = Matrix::load()?;
    let expected = matrix
        .runtimes
        .iter()
        .map(|row| row.id.clone())
        .collect::<Vec<_>>();
    if release.schema_version != 1
        || release.source != artifacts::source()?
        || release.variants != expected
        || release.assets.len() != expected.len()
    {
        return Err("release roster/source mismatch".into());
    }
    version(&release.tag, &fs::read_to_string("Cargo.toml")?)?;
    for (name, digest) in &release.assets {
        if !expected
            .iter()
            .any(|id| name == &format!("skippy-{}-{id}.tar.gz", release.tag))
            || artifacts::digest(&directory.join(name))? != *digest
        {
            return Err("release archive identity mismatch".into());
        }
        if fs::read_to_string(directory.join(format!("{name}.sha256")))?
            != format!("{digest}  {name}\n")
        {
            return Err("release sidecar mismatch".into());
        }
    }
    if fs::read_to_string(directory.join("release.json.sha256"))?
        != format!(
            "{}  release.json\n",
            artifacts::digest(&directory.join("release.json"))?
        )
    {
        return Err("release inventory checksum mismatch".into());
    }
    let expected_files = release.assets.len() * 2 + 2;
    if fs::read_dir(directory)?.count() != expected_files {
        return Err("release contains unrecognized assets".into());
    }
    Ok(())
}

fn admit_draft(path: &Path) -> DynResult<()> {
    let value: serde_json::Value = serde_json::from_slice(&fs::read(path)?)?;
    if value["isDraft"] != true {
        return Err("refusing to overwrite a published release".into());
    }
    let tag = value["tagName"].as_str().ok_or("release has no tag")?;
    validate(tag)?;
    if value["body"].as_str() != Some(&format!("Skippy source: {}", artifacts::source()?)) {
        return Err("draft source identity does not match checkout".into());
    }
    Ok(())
}
fn github_output(key: &str, value: &str) -> DynResult<()> {
    if let Some(path) = std::env::var_os("GITHUB_OUTPUT") {
        writeln!(
            fs::OpenOptions::new().append(true).open(path)?,
            "{key}={value}"
        )?;
    }
    Ok(())
}

fn select(tag: &str, path: &Path) -> DynResult<()> {
    validate(tag)?;
    let releases: Vec<serde_json::Value> = serde_json::from_slice(&fs::read(path)?)?;
    let matches = releases
        .iter()
        .filter(|release| release["tagName"] == tag)
        .count();
    if matches > 1 {
        return Err("duplicate release tags in API response".into());
    }
    github_output("exists", if matches == 1 { "true" } else { "false" })
}

fn upload_plan(directory: &Path, path: &Path) -> DynResult<()> {
    verify(directory)?;
    let local: Release = serde_json::from_slice(&fs::read(directory.join("release.json"))?)?;
    let remote: serde_json::Value = serde_json::from_slice(&fs::read(path)?)?;
    if remote["draft"] != true
        || remote["tag_name"] != local.tag
        || remote["body"] != format!("Skippy source: {}", local.source)
    {
        return Err("refusing a published or foreign draft release".into());
    }
    let mut missing = artifacts::files(directory)?;
    for asset in remote["assets"]
        .as_array()
        .ok_or("release API omitted assets")?
    {
        let name = asset["name"].as_str().ok_or("asset has no name")?;
        let expected = missing
            .remove(name)
            .ok_or("unknown or duplicate existing release asset")?;
        if asset["digest"] != format!("sha256:{expected}") {
            return Err(
                format!("existing draft asset differs or has no verified digest: {name}").into(),
            );
        }
    }
    let list = missing
        .keys()
        .map(|name| format!("{}\n", directory.join(name).display()))
        .collect::<String>();
    fs::write("release-upload.txt", list)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn tag_must_equal_committed_version() {
        let manifest = "[workspace.package]\nversion='1.2.3-rc.1'";
        assert!(version("v1.2.3-rc.1", manifest).is_ok());
        for tag in ["1.2.3-rc.1", "v1.2.3", "v01.2.3-rc.1", "main"] {
            assert!(version(tag, manifest).is_err());
        }
    }
}
