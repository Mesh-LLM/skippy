use crate::{command::DynResult, execution};
use serde::Deserialize;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct PullRequest {
    head_ref_name: String,
    head_ref_oid: String,
    base_ref_name: String,
    url: String,
}
fn automation_pr(pr: &PullRequest) -> DynResult<bool> {
    if pr.base_ref_name != "main" || !pr.head_ref_name.starts_with("automation/llama-") {
        return Ok(false);
    }
    super::source::validate_sha(&pr.head_ref_oid)?;
    if pr.head_ref_name != format!("automation/llama-{}", &pr.head_ref_oid[..12])
        || !pr
            .url
            .starts_with("https://github.com/general-inference/skippy/pull/")
    {
        return Err("open upgrade PR has inconsistent immutable branch identity".into());
    }
    Ok(true)
}
pub(crate) fn open_upgrade(upstream: &str) -> DynResult<Option<String>> {
    super::source::validate_sha(upstream)?;
    let environment = token();
    let output = execution::capture(
        "gh",
        &execution::strings(&[
            "pr",
            "list",
            "--repo",
            "general-inference/skippy",
            "--state",
            "open",
            "--base",
            "main",
            "--limit",
            "1000",
            "--json",
            "headRefName,headRefOid,baseRefName,url",
        ]),
        &environment,
    )?;
    let prs: Vec<PullRequest> = serde_json::from_str(&output)?;
    if prs.len() >= 1000 {
        return Err("open PR query may be truncated".into());
    }
    for pr in prs {
        if automation_pr(&pr)? {
            let pin = execution::capture(
                "gh",
                &execution::strings(&[
                    "api",
                    "-H",
                    "Accept: application/vnd.github.raw+json",
                    &format!(
                        "/repos/general-inference/skippy/contents/llama_cpp/upstream.txt?ref={}",
                        pr.head_ref_oid
                    ),
                ]),
                &environment,
            )?;
            super::source::validate_sha(pin.trim())?;
            if pin.trim() == upstream {
                return Ok(Some(pr.url));
            }
        }
    }
    Ok(None)
}
fn token() -> Vec<(&'static str, String)> {
    std::env::var("GH_TOKEN")
        .ok()
        .filter(|value| !value.is_empty())
        .map(|value| vec![("GH_TOKEN", value)])
        .unwrap_or_default()
}

pub(super) fn latest() -> DynResult<String> {
    // gh paginates the complete published release list. Emit only immutable
    // selection inputs, keeping verbose release bodies outside capture limits.
    let env = token();
    let releases = execution::capture_for(
        "gh",
        &execution::strings(&[
            "api",
            "--paginate",
            "/repos/ggml-org/llama.cpp/releases?per_page=100",
            "--jq",
            ".[] | select(.draft == false and .published_at != null) | [.published_at, .tag_name] | @tsv",
        ]),
        &env,
        600,
    )?;
    resolve_tag(select(&releases)?)
}
fn select(releases: &str) -> DynResult<&str> {
    let mut latest = None;
    for line in releases.lines() {
        let (date, tag) = line
            .split_once('\t')
            .ok_or("invalid release selection record")?;
        if date.is_empty() || tag.is_empty() || tag.contains('\t') {
            return Err("invalid published release metadata".into());
        }
        if latest.is_none_or(|(prior, _)| date > prior) {
            latest = Some((date, tag));
        }
    }
    latest
        .map(|(_, tag)| tag)
        .ok_or_else(|| "no published upstream release".into())
}
fn resolve_tag(tag: &str) -> DynResult<String> {
    if tag.is_empty() || tag.starts_with('-') || tag.contains(['\r', '\n', ' ']) {
        return Err("invalid upstream release tag".into());
    }
    let reference = format!("refs/tags/{tag}");
    let peeled = format!("{reference}^{{}}");
    let refs = execution::output(
        "git",
        &execution::strings(&[
            "ls-remote",
            "https://github.com/ggml-org/llama.cpp.git",
            &reference,
            &peeled,
        ]),
    )?;
    let mut tags = std::collections::BTreeMap::new();
    for line in refs.lines() {
        let pair = line.split_whitespace().collect::<Vec<_>>();
        let [sha, name] = pair.as_slice() else {
            return Err("invalid upstream ref advertisement".into());
        };
        super::source::validate_sha(sha)?;
        tags.insert(*name, *sha);
    }
    Ok(tags
        .get(peeled.as_str())
        .or_else(|| tags.get(reference.as_str()))
        .ok_or("published release tag has no immutable Git target")?
        .to_string())
}
#[cfg(test)]
mod tests {
    #[test]
    fn pr_admission_requires_full_immutable_branch_and_base_identity() {
        let mut pr:super::PullRequest=serde_json::from_value(serde_json::json!({"headRefName":"automation/llama-aaaaaaaaaaaa","headRefOid":"a".repeat(40),"baseRefName":"main","url":"https://github.com/general-inference/skippy/pull/1"})).unwrap();
        assert!(super::automation_pr(&pr).unwrap());
        pr.head_ref_oid = "b".repeat(40);
        assert!(super::automation_pr(&pr).is_err());
        pr.base_ref_name = "other".into();
        assert!(!super::automation_pr(&pr).unwrap());
    }
    #[test]
    fn publication_time_selects_release_independently_of_api_page_order() {
        assert_eq!(super::select("2026-11-01T12:00:00Z\tb900\n2026-11-07T12:00:00Z\tb901\n2026-11-02T12:00:00Z\tb902\n").unwrap(),"b901");
        assert!(super::select("").is_err());
        assert!(super::select("unframed").is_err());
    }
}
