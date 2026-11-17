use crate::{
    artifact,
    command::{DynResult, write_json_file},
    execution,
};
use serde::Serialize;
use std::path::{Path, PathBuf};

const PIN: &str = "llama_cpp/upstream.txt";
const WORKFLOW: &str =
    "general-inference/skippy/.github/workflows/llama-upgrade.yml@refs/heads/main";

#[derive(Serialize)]
struct Candidate {
    schema_version: u32,
    base_sha: String,
    candidate_sha: String,
    upstream_sha: String,
    bundle_sha256: String,
}

pub(super) fn validate_sha(sha: &str) -> DynResult<()> {
    if sha.len() != 40
        || !sha
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("expected a complete lowercase Git SHA-1".into());
    }
    Ok(())
}

fn git(root: &Path, args: &[&str]) -> DynResult<String> {
    let mut command = vec!["-C".into(), root.display().to_string()];
    command.extend(execution::strings(args));
    Ok(execution::output("git", &command)?.trim().to_owned())
}

pub(crate) fn prepared_head(root: &Path) -> DynResult<String> {
    let source = root.join(".deps/llama.cpp");
    let head = git(&source, &["rev-parse", "HEAD"])?;
    validate_sha(&head)?;
    if std::fs::read_to_string(source.join(".skippy-patched-sha"))?.trim() != head
        || !git(&source, &["status", "--porcelain", "--untracked-files=no"])?.is_empty()
    {
        return Err("prepared llama source is dirty or unstamped".into());
    }
    if std::fs::read_to_string(source.join(".skippy-upstream-sha"))?.trim()
        != std::fs::read_to_string(root.join(PIN))?.trim()
    {
        return Err("prepared llama source does not match pin".into());
    }
    Ok(head)
}

fn clean_root() -> DynResult<PathBuf> {
    let root = std::env::current_dir()?.canonicalize()?;
    if Path::new(&git(&root, &["rev-parse", "--show-toplevel"])?) != root {
        return Err("upgrade commands must run at the repository root".into());
    }
    if !git(
        &root,
        &["status", "--porcelain", "--untracked-files=normal"],
    )?
    .is_empty()
    {
        return Err("upgrade source checkout must be clean".into());
    }
    Ok(root)
}

pub(super) fn prepare(upstream: &str) -> DynResult<()> {
    validate_sha(upstream)?;
    let root = clean_root()?;
    let output = root.join("target/llama-upgrade");
    std::fs::create_dir_all(&output)?;
    let temporary = tempfile::tempdir_in(&output)?;
    let checkout = temporary.path().join("source");
    let base = git(&root, &["rev-parse", "HEAD"])?;
    execution::run(
        "git",
        &execution::strings(&[
            "clone",
            "--no-hardlinks",
            "--no-checkout",
            &root.display().to_string(),
            &checkout.display().to_string(),
        ]),
        &[],
        600,
    )?;
    git(&checkout, &["checkout", "--detach", &base])?;
    std::fs::write(checkout.join(PIN), format!("{upstream}\n"))?;
    super::regeneration::run(&checkout)?;
    replay(&checkout)?;
    let candidate = commit(&checkout, &base, upstream)?;
    let bundle = output.join("candidate.bundle");
    git(
        &checkout,
        &[
            "bundle",
            "create",
            &bundle.display().to_string(),
            &format!("{base}..HEAD"),
        ],
    )?;
    let record = Candidate {
        schema_version: 1,
        base_sha: base,
        candidate_sha: candidate.clone(),
        upstream_sha: upstream.into(),
        bundle_sha256: artifact::sha256(&bundle)?,
    };
    write_json_file(&output.join("candidate.json"), &record)?;
    super::output("candidate_sha", &candidate)
}

fn replay(checkout: &Path) -> DynResult<()> {
    let environment = [
        (
            "LLAMA_WORKDIR",
            checkout.join(".deps/llama.cpp").display().to_string(),
        ),
        ("LLAMA_PIN_FILE", checkout.join(PIN).display().to_string()),
        (
            "LLAMA_PATCH_DIR",
            checkout.join("llama_cpp/patches").display().to_string(),
        ),
        (
            "SKIPPY_AUTOMATION_BIN",
            std::env::current_exe()?.display().to_string(),
        ),
    ];
    execution::run(
        "just",
        &execution::strings(&[
            "--justfile",
            &checkout.join("Justfile").display().to_string(),
            "llama-prepare",
        ]),
        &environment,
        3600,
    )
}

fn commit(checkout: &Path, base: &str, upstream: &str) -> DynResult<String> {
    let changed = git(checkout, &["diff", "--name-only"])?;
    if changed.lines().any(|path| !allowed(path)) {
        return Err(format!("candidate unexpectedly changed paths: {changed}").into());
    }
    git(
        checkout,
        &[
            "add",
            "--",
            PIN,
            "llama_cpp/patches/generated",
            "crates/skippy-ffi/src/bindings",
            "docs/native-api.md",
        ],
    )?;
    let timestamp = git(checkout, &["show", "-s", "--format=%ct", base])?
        .parse::<u64>()?
        .checked_add(1)
        .ok_or("candidate timestamp overflow")?;
    let date = format!("@{timestamp} +0000");
    let environment = [
        ("GIT_AUTHOR_NAME", "Skippy llama updater".into()),
        ("GIT_AUTHOR_EMAIL", "ci@skippy.local".into()),
        ("GIT_COMMITTER_NAME", "Skippy llama updater".into()),
        ("GIT_COMMITTER_EMAIL", "ci@skippy.local".into()),
        ("GIT_AUTHOR_DATE", date.clone()),
        ("GIT_COMMITTER_DATE", date),
    ];
    execution::run(
        "git",
        &execution::strings(&[
            "-C",
            &checkout.display().to_string(),
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-m",
            &format!("Update llama.cpp to {upstream}"),
        ]),
        &environment,
        120,
    )?;
    git(checkout, &["rev-parse", "HEAD"])
}

pub(super) fn restore(bundle: &str, expected: &str) -> DynResult<()> {
    validate_sha(expected)?;
    if std::env::var("GITHUB_ACTIONS").as_deref() == Ok("true")
        && std::env::var("GITHUB_WORKFLOW_REF").as_deref() != Ok(WORKFLOW)
    {
        return Err("candidate restoration requires the protected llama-upgrade workflow".into());
    }
    let root = clean_root()?;
    restore_at(&root, Path::new(bundle), expected)?;
    super::output("candidate_sha", expected)
}

fn restore_at(root: &Path, bundle: &Path, expected: &str) -> DynResult<()> {
    let base = git(root, &["rev-parse", "HEAD"])?;
    let bundle = bundle.canonicalize()?;
    git(root, &["bundle", "verify", &bundle.display().to_string()])?;
    let advertised = git(
        root,
        &["bundle", "list-heads", &bundle.display().to_string()],
    )?;
    if advertised != format!("{expected} HEAD") {
        return Err("candidate bundle advertises unexpected refs".into());
    }
    git(
        root,
        &["fetch", "--no-tags", &bundle.display().to_string(), "HEAD"],
    )?;
    let parents = git(root, &["rev-list", "--parents", "-n", "1", expected])?;
    if parents != format!("{expected} {base}") {
        return Err("candidate must directly descend from the protected checkout".into());
    }
    if git(root, &["diff", "--name-only", &base, expected])?
        .lines()
        .any(|path| !allowed(path))
    {
        return Err("candidate modified a path outside the update allowlist".into());
    }
    let pin = git(root, &["show", &format!("{expected}:{PIN}")])?;
    validate_sha(&pin)?;
    git(root, &["checkout", "--detach", expected])?;
    Ok(())
}

fn allowed(path: &str) -> bool {
    path == PIN
        || path == "docs/native-api.md"
        || path.starts_with("llama_cpp/patches/generated/")
        || path.starts_with("crates/skippy-ffi/src/bindings/")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn refs_and_abbreviations_are_not_source_identities() {
        assert!(super::validate_sha(&"a".repeat(40)).is_ok());
        for sha in ["master", "HEAD", "-other", "1234567", &"A".repeat(40)] {
            assert!(super::validate_sha(sha).is_err());
        }
    }

    #[test]
    fn immutable_bundle_restores_only_allowed_direct_descendant() -> DynResult<()> {
        for allowed_change in [false, true] {
            let temporary = tempfile::tempdir()?;
            let root = temporary.path().join("repository");
            std::fs::create_dir(&root)?;
            git(&root, &["init", "--initial-branch=main"])?;
            git(&root, &["config", "user.name", "Fixture"])?;
            git(&root, &["config", "user.email", "fixture@skippy.local"])?;
            std::fs::create_dir(root.join("llama_cpp"))?;
            std::fs::write(root.join(PIN), format!("{}\n", "a".repeat(40)))?;
            git(&root, &["add", "."])?;
            git(
                &root,
                &["-c", "commit.gpgsign=false", "commit", "-m", "base"],
            )?;
            let base = git(&root, &["rev-parse", "HEAD"])?;
            if allowed_change {
                std::fs::write(root.join(PIN), format!("{}\n", "b".repeat(40)))?;
            } else {
                std::fs::write(root.join("unapproved.rs"), "fn injected() {}")?;
            }
            git(&root, &["add", "."])?;
            git(
                &root,
                &["-c", "commit.gpgsign=false", "commit", "-m", "candidate"],
            )?;
            let candidate = git(&root, &["rev-parse", "HEAD"])?;
            let bundle = temporary.path().join("candidate.bundle");
            git(
                &root,
                &[
                    "bundle",
                    "create",
                    &bundle.display().to_string(),
                    &format!("{base}..HEAD"),
                ],
            )?;
            git(&root, &["checkout", "--detach", &base])?;
            assert_eq!(
                restore_at(&root, &bundle, &candidate).is_ok(),
                allowed_change
            );
            assert_eq!(
                git(&root, &["rev-parse", "HEAD"])?,
                if allowed_change { candidate } else { base }
            );
        }
        Ok(())
    }
}
