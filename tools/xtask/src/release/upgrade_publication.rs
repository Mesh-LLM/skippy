//! Admit complete independent family receipts before publishing an immutable upgrade PR.
use crate::{ci::artifacts, command::DynResult, execution};
use base64::Engine;
use process_supervision::{Cancellation, RawCaptureOptions, Value};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Candidate {
    schema_version: u32,
    base_sha: String,
    candidate_sha: String,
    upstream_sha: String,
    bundle_sha256: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Receipt {
    schema_version: u32,
    candidate_sha: String,
    upstream_sha: String,
    manifest_sha256: String,
    native_recipe_sha256: String,
    backend: String,
    pass: String,
    result: String,
    families: Vec<Family>,
    artifacts: BTreeMap<String, String>,
}
#[derive(Clone, Deserialize, Serialize, PartialEq, Debug)]
#[serde(deny_unknown_fields)]
struct Family {
    family: String,
    profile: String,
    revision: String,
    files: BTreeMap<String, String>,
    lanes: Vec<String>,
}

pub(super) fn run(args: &[String]) -> DynResult<()> {
    let [candidate_option, bundle, evidence_option, evidence] = args else {
        return Err("upgrade publish --candidate BUNDLE --evidence DIRECTORY".into());
    };
    if candidate_option != "--candidate" || evidence_option != "--evidence" {
        return Err("invalid upgrade publication arguments".into());
    }
    require_publisher()?;
    let bundle = Path::new(bundle).canonicalize()?;
    let candidate: Candidate =
        serde_json::from_slice(&fs::read(bundle.with_file_name("candidate.json"))?)?;
    import_candidate(&bundle, &candidate)?;
    let manifest_text = execution::capture(
        "git",
        &execution::strings(&[
            "show",
            &format!(
                "{}:ci/llama-canary/family-certified.json",
                candidate.candidate_sha
            ),
        ]),
        &[],
    )?;
    let manifest_hash = hex::encode(Sha256::digest(manifest_text.as_bytes()));
    let manifest: serde_json::Value = serde_json::from_str(&manifest_text)?;
    let recipe = candidate_recipe(&candidate.candidate_sha)?;
    admit_receipts(
        Path::new(evidence),
        &candidate,
        &manifest,
        &manifest_hash,
        &recipe,
    )?;
    publish(&candidate)
}

fn require_publisher() -> DynResult<()> {
    if std::env::var("GITHUB_REPOSITORY").as_deref() != Ok("general-inference/skippy")
        || std::env::var("GITHUB_REF").as_deref() != Ok("refs/heads/main")
        || std::env::var("GITHUB_WORKFLOW_REF").as_deref()
            != Ok("general-inference/skippy/.github/workflows/llama-upgrade.yml@refs/heads/main")
    {
        return Err(
            "upgrade publication requires the protected-main Skippy upgrade workflow".into(),
        );
    }
    Ok(())
}
fn sha(value: &str) -> bool {
    value.len() == 40
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
fn import_candidate(bundle: &Path, candidate: &Candidate) -> DynResult<()> {
    if candidate.schema_version != 1
        || ![
            &candidate.base_sha,
            &candidate.candidate_sha,
            &candidate.upstream_sha,
        ]
        .into_iter()
        .all(|value| sha(value))
        || artifacts::digest(bundle)? != candidate.bundle_sha256
        || artifacts::source()? != candidate.base_sha
    {
        return Err("candidate identity does not match frozen protected source".into());
    }
    execution::run(
        "git",
        &execution::strings(&["bundle", "verify", &bundle.to_string_lossy()]),
        &[],
        120,
    )?;
    let heads = execution::capture(
        "git",
        &execution::strings(&["bundle", "list-heads", &bundle.to_string_lossy()]),
        &[],
    )?;
    if heads.trim() != format!("{} HEAD", candidate.candidate_sha) {
        return Err("unexpected candidate bundle refs".into());
    }
    execution::run(
        "git",
        &execution::strings(&["fetch", "--no-tags", &bundle.to_string_lossy(), "HEAD"]),
        &[],
        120,
    )?;
    let parents = execution::capture(
        "git",
        &execution::strings(&["rev-list", "--parents", "-n1", &candidate.candidate_sha]),
        &[],
    )?;
    require_direct_child(&parents, &candidate.base_sha, &candidate.candidate_sha)?;
    let changed = execution::capture(
        "git",
        &execution::strings(&[
            "diff",
            "--name-only",
            &candidate.base_sha,
            &candidate.candidate_sha,
        ]),
        &[],
    )?;
    for path in changed.lines() {
        if path != "llama_cpp/upstream.txt"
            && path != "docs/native-api.md"
            && !path.starts_with("llama_cpp/patches/generated/")
            && !path.starts_with("crates/skippy-ffi/src/bindings/")
        {
            return Err(
                format!("candidate changes a path outside upstream generation: {path}").into(),
            );
        }
    }
    let pin = execution::capture(
        "git",
        &execution::strings(&[
            "show",
            &format!("{}:llama_cpp/upstream.txt", candidate.candidate_sha),
        ]),
        &[],
    )?;
    if pin.trim() != candidate.upstream_sha {
        return Err("candidate pin differs from qualified upstream".into());
    }
    Ok(())
}

fn require_direct_child(parents: &str, base: &str, candidate: &str) -> DynResult<()> {
    if parents.trim() != format!("{candidate} {base}") {
        return Err("upgrade candidate must be a direct non-merge child of its frozen base".into());
    }
    Ok(())
}
fn candidate_recipe(candidate: &str) -> DynResult<String> {
    let temp = tempfile::tempdir()?;
    let archive = temp.path().join("source.tar");
    execution::run(
        "git",
        &execution::strings(&[
            "archive",
            "--format=tar",
            "--output",
            &archive.to_string_lossy(),
            candidate,
            "--",
            "llama_cpp",
            "scripts/build-llama.sh",
            "scripts/prepare-llama.sh",
            "skippy_api",
            "crates/skippy-ffi",
        ]),
        &[],
        180,
    )?;
    let source = temp.path().join("source");
    fs::create_dir_all(&source)?;
    tar::Archive::new(fs::File::open(archive)?).unpack(&source)?;
    crate::upgrade::evidence::recipe_digest_at(&source)
}

fn admit_receipts(
    root: &Path,
    candidate: &Candidate,
    manifest: &serde_json::Value,
    manifest_hash: &str,
    recipe: &str,
) -> DynResult<()> {
    let expected = expected_families(manifest)?;
    for pass in ["candidate", "independent"] {
        for backend in ["cpu", "metal", "cuda"] {
            let directory = root.join(format!("llama-{pass}-{backend}-evidence"));
            let file_roster = regular_evidence_files(&directory)?;
            let receipt: Receipt =
                serde_json::from_slice(&fs::read(directory.join("receipt.json"))?)?;
            let expected_roster = receipt
                .artifacts
                .keys()
                .cloned()
                .chain(std::iter::once("receipt.json".into()))
                .collect::<BTreeSet<_>>();
            if receipt.artifacts.contains_key("receipt.json") || file_roster != expected_roster {
                return Err("family evidence contains missing or unaccounted files".into());
            }
            if receipt.schema_version != 1
                || receipt.candidate_sha != candidate.candidate_sha
                || receipt.upstream_sha != candidate.upstream_sha
                || receipt.manifest_sha256 != manifest_hash
                || receipt.native_recipe_sha256 != recipe
                || receipt.backend != backend
                || receipt.pass != pass
                || receipt.result != "pass"
            {
                return Err(format!(
                    "invalid source, recipe or result in {pass}/{backend} receipt"
                )
                .into());
            }
            if receipt.families.len() != expected.len() {
                return Err("duplicate or missing family evidence".into());
            }
            let actual = receipt
                .families
                .into_iter()
                .map(|mut family| {
                    family.lanes.sort();
                    (family.family.clone(), family)
                })
                .collect::<BTreeMap<_, _>>();
            if actual != expected {
                return Err(
                    format!("incomplete family/model/lane coverage in {pass}/{backend}").into(),
                );
            }
            if receipt.artifacts.is_empty() {
                return Err("family receipt has no retained executable evidence".into());
            }
            for (relative, digest) in &receipt.artifacts {
                let path = directory.join(relative).canonicalize()?;
                if !path.starts_with(directory.canonicalize()?)
                    || artifacts::digest(&path)? != *digest
                {
                    return Err("foreign or changed family evidence artifact".into());
                }
            }
        }
    }
    Ok(())
}

fn regular_evidence_files(root: &Path) -> DynResult<BTreeSet<String>> {
    let mut files = BTreeSet::new();
    collect_evidence(root, root, &mut files)?;
    Ok(files)
}
fn collect_evidence(root: &Path, path: &Path, files: &mut BTreeSet<String>) -> DynResult<()> {
    let kind = fs::symlink_metadata(path)?.file_type();
    if kind.is_symlink() {
        return Err("family evidence symlinks are not admitted".into());
    }
    if kind.is_file() {
        files.insert(
            path.strip_prefix(root)?
                .to_str()
                .ok_or("non-Unicode evidence path")?
                .replace('\\', "/"),
        );
    } else if kind.is_dir() {
        for entry in fs::read_dir(path)? {
            collect_evidence(root, &entry?.path(), files)?;
        }
    } else {
        return Err("family evidence contains a special file".into());
    }
    Ok(())
}

fn expected_families(manifest: &serde_json::Value) -> DynResult<BTreeMap<String, Family>> {
    let mut expected = BTreeMap::new();
    for row in manifest["models"]
        .as_array()
        .ok_or("missing supported family roster")?
    {
        let name = text(row, "family")?;
        let mut files = BTreeMap::new();
        for key in ["artifact", "mmproj_artifact", "draft_artifact"] {
            if let Some(artifact) = row.get(key) {
                let prefix = format!(
                    "{}@{}",
                    text(artifact, "repo")?,
                    text(artifact, "revision")?
                );
                for filename in artifact["files"].as_array().ok_or("missing model files")? {
                    let filename = filename.as_str().ok_or("invalid model filename")?;
                    files.insert(
                        format!("{prefix}/{filename}"),
                        text(&artifact["file_integrity"][filename], "blob_id")?,
                    );
                }
            }
        }
        let mut lanes = if row["class"] == "causal_generation" {
            let mut lanes = execution::strings(&["single-step", "chain", "state-handoff"]);
            if row["execution"]["mtp_layers"].as_u64().unwrap_or(0) > 0 {
                lanes.push("native-mtp-heads".into());
            }
            if row.get("mmproj_artifact").is_some() {
                lanes.push("multimodal-smoke".into());
            }
            lanes
        } else {
            let class = text(row, "class")?;
            vec![format!("{class}-smoke"), format!("{class}-oracle")]
        };
        lanes.sort();
        let family = Family {
            family: name.clone(),
            profile: text(row, "profile")?,
            revision: text(&row["artifact"], "revision")?,
            files,
            lanes,
        };
        if expected.insert(name, family).is_some() {
            return Err("duplicate supported family".into());
        }
    }
    let names = expected.keys().map(String::as_str).collect::<BTreeSet<_>>();
    for family in [
        "qwen3-dense",
        "qwen3-moe",
        "qwen3-next",
        "qwen3-vl",
        "qwen3vlmoe",
        "qwen3tts",
    ] {
        if !names.contains(family) {
            return Err(format!("Qwen3 family missing: {family}").into());
        }
    }
    Ok(expected)
}
fn text(value: &serde_json::Value, key: &str) -> DynResult<String> {
    value[key]
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("missing string field {key}").into())
}

fn authenticated(program: &str, args: &[String]) -> DynResult<String> {
    let token =
        std::env::var("GH_TOKEN").map_err(|_| "upgrade publisher requires a scoped App token")?;
    let mut spec = execution::specification(program, args, &[])?;
    spec.environment
        .insert("GH_TOKEN".into(), Value::Secret(token.clone().into()));
    spec.environment
        .insert("GIT_CONFIG_COUNT".into(), Value::Public("1".into()));
    spec.environment.insert(
        "GIT_CONFIG_KEY_0".into(),
        Value::Public("http.https://github.com/.extraheader".into()),
    );
    let header = format!(
        "AUTHORIZATION: basic {}",
        base64::engine::general_purpose::STANDARD.encode(format!("x-access-token:{token}"))
    );
    spec.environment
        .insert("GIT_CONFIG_VALUE_0".into(), Value::Secret(header.into()));
    let report = process_supervision::supervise_raw(
        &spec,
        &execution::limits(180),
        &Cancellation::default(),
        RawCaptureOptions {
            stdout: std::num::NonZeroUsize::new(1024 * 1024),
            stderr: None,
        },
    )?;
    if !report.process.success() {
        return Err(format!(
            "authenticated {program} failed: {:?}",
            report.process.outcome
        )
        .into());
    }
    Ok(String::from_utf8(
        report
            .stdout
            .ok_or("missing publication output")?
            .as_bytes()
            .to_vec(),
    )?)
}
fn publish(candidate: &Candidate) -> DynResult<()> {
    if let Some(url) = crate::upgrade::resolution::open_upgrade(&candidate.upstream_sha)? {
        println!("{url}");
        return Ok(());
    }
    let branch = format!("automation/llama-{}", &candidate.candidate_sha[..12]);
    authenticated(
        "git",
        &execution::strings(&[
            "push",
            "https://github.com/general-inference/skippy.git",
            &format!("{}:refs/heads/{branch}", candidate.candidate_sha),
        ]),
    )?;
    let existing: Vec<serde_json::Value> = serde_json::from_str(&authenticated(
        "gh",
        &execution::strings(&[
            "pr",
            "list",
            "--repo",
            "general-inference/skippy",
            "--head",
            &branch,
            "--state",
            "open",
            "--json",
            "headRefOid,baseRefName,url",
        ]),
    )?)?;
    if !existing.is_empty() {
        if existing.len() != 1
            || existing[0]["headRefOid"] != candidate.candidate_sha
            || existing[0]["baseRefName"] != "main"
        {
            return Err("existing upgrade PR identity mismatch".into());
        }
        println!("{}", text(&existing[0], "url")?);
        return Ok(());
    }
    let body = tempfile::NamedTempFile::new()?;
    fs::write(
        body.path(),
        format!(
            "Upgrade Skippy's native inference engine to llama.cpp `{}`.\n\nThe complete supported-family roster passed CPU, Metal and CUDA qualification and an independent clean verification of this exact candidate. Both passes used the normal platform CI and packaged Qwen3 smoke matrix.\n\nCandidate: `{}`\n",
            candidate.upstream_sha, candidate.candidate_sha
        ),
    )?;
    let url = authenticated(
        "gh",
        &execution::strings(&[
            "pr",
            "create",
            "--repo",
            "general-inference/skippy",
            "--base",
            "main",
            "--head",
            &branch,
            "--title",
            &format!("Upgrade llama.cpp to {}", &candidate.upstream_sha[..12]),
            "--body-file",
            &body.path().to_string_lossy(),
        ]),
    )?;
    println!("{}", url.trim());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn requires_both_complete_backend_passes_and_unchanged_artifacts() {
        let manifest: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../ci/llama-canary/family-certified.json"
        ))
        .unwrap();
        let expected = expected_families(&manifest).unwrap();
        assert!(expected["qwen3-vl"].files.len() >= 2);
        let candidate = Candidate {
            schema_version: 1,
            base_sha: "a".repeat(40),
            candidate_sha: "b".repeat(40),
            upstream_sha: "c".repeat(40),
            bundle_sha256: "d".repeat(64),
        };
        let temp = tempfile::tempdir().unwrap();
        for pass in ["candidate", "independent"] {
            for backend in ["cpu", "metal", "cuda"] {
                let directory = temp.path().join(format!("llama-{pass}-{backend}-evidence"));
                fs::create_dir(&directory).unwrap();
                fs::write(directory.join("result.json"), "{}").unwrap();
                let value = serde_json::json!({"schema_version":1,"candidate_sha":candidate.candidate_sha,"upstream_sha":candidate.upstream_sha,
                    "manifest_sha256":"manifest","native_recipe_sha256":"recipe","backend":backend,"pass":pass,"result":"pass",
                    "families":expected.values().collect::<Vec<_>>(),"artifacts":{"result.json":artifacts::digest(&directory.join("result.json")).unwrap()}});
                artifacts::write_json(&directory.join("receipt.json"), &value).unwrap();
            }
        }
        admit_receipts(temp.path(), &candidate, &manifest, "manifest", "recipe").unwrap();
        let independent = temp.path().join("llama-independent-cuda-evidence");
        fs::write(independent.join("unaccounted.json"), "{}").unwrap();
        assert!(admit_receipts(temp.path(), &candidate, &manifest, "manifest", "recipe").is_err());
        fs::remove_file(independent.join("unaccounted.json")).unwrap();
        fs::write(independent.join("result.json"), "changed").unwrap();
        assert!(admit_receipts(temp.path(), &candidate, &manifest, "manifest", "recipe").is_err());
        fs::remove_file(independent.join("receipt.json")).unwrap();
        assert!(admit_receipts(temp.path(), &candidate, &manifest, "manifest", "recipe").is_err());
    }
    #[test]
    fn candidate_must_be_direct_child_without_merge_parents() {
        assert!(require_direct_child("candidate base\n", "base", "candidate").is_ok());
        for parents in [
            "candidate other",
            "candidate base other",
            "candidate",
            "base",
        ] {
            assert!(require_direct_child(parents, "base", "candidate").is_err());
        }
    }
    #[cfg(unix)]
    #[test]
    fn evidence_rejects_symlinks_and_special_files() {
        use std::{os::unix::fs::symlink, os::unix::net::UnixListener};
        let temp = tempfile::tempdir().unwrap();
        fs::write(temp.path().join("regular"), "evidence").unwrap();
        symlink("regular", temp.path().join("link")).unwrap();
        assert!(regular_evidence_files(temp.path()).is_err());
        fs::remove_file(temp.path().join("link")).unwrap();
        let _socket = UnixListener::bind(temp.path().join("socket")).unwrap();
        assert!(regular_evidence_files(temp.path()).is_err());
    }
}
