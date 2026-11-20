use super::capabilities;
use super::details::{
    file_preference_score, gguf_variant_size_bytes_from_siblings,
    matching_remote_catalog_model_for_huggingface, merge_capabilities,
    quant_selector_from_gguf_file, remote_hf_size_label_with_api,
};
use super::details::{remote_catalog_model_draft_ref, remote_catalog_model_ref};
use skippy_model_artifact::capabilities::ModelCapabilities;
use skippy_model_hf::remote_catalog;

use anyhow::{Context, Result};
use hf_hub::repository::ModelInfo;
use serde_json::{Value, json};
pub use skippy_model_hf::search::{ArtifactFilter as SearchArtifactFilter, Sort as SearchSort};
use std::collections::HashSet;
use std::io::Write;
use tokio::task::JoinSet;

#[derive(Clone, Debug)]
pub struct SearchHit {
    pub repo_id: String,
    pub kind: &'static str,
    pub exact_ref: String,
    pub variant_count: Option<usize>,
    pub size_label: Option<String>,
    pub downloads: Option<u64>,
    pub likes: Option<u64>,
    pub catalog: Option<remote_catalog::RemoteCatalogModel>,
    pub capabilities: ModelCapabilities,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SearchProgress {
    SearchingHub,
    InspectingRepos { completed: usize, total: usize },
}

pub fn search_catalog_models(query: &str) -> Result<Vec<remote_catalog::RemoteCatalogModel>> {
    remote_catalog::ensure_catalog().context("load configured model catalog for catalog search")?;
    let q = query.to_lowercase();
    let mut results: Vec<_> = remote_catalog::loaded_models()
        .context("parse configured model catalog models for catalog search")?
        .into_iter()
        .filter(|model| {
            model.name.to_lowercase().contains(&q)
                || model.file.to_lowercase().contains(&q)
                || model
                    .description
                    .as_deref()
                    .unwrap_or_default()
                    .to_lowercase()
                    .contains(&q)
        })
        .collect();
    results.sort_by(|left, right| left.name.cmp(&right.name));
    Ok(results)
}

pub fn search_catalog_json_payload(
    query: &str,
    filter: SearchArtifactFilter,
    sort: SearchSort,
    results: &[remote_catalog::RemoteCatalogModel],
    limit: usize,
) -> Value {
    let payload_results: Vec<Value> = results
        .iter()
        .take(limit)
        .map(|model| {
            let model_ref = remote_catalog_model_ref(model);
            json!({
                "name": model.name,
                "repo_id": model.source_repo(),
                "type": "gguf",
                "size": model.size,
                "description": model.description,
                "fit": model.size.as_deref().and_then(fit_code_for_size_label),
                "ref": model_ref,
                "show": format!("{program} models show {model_ref}", program = crate::models::output::program()),
                "download": format!("{program} models download {model_ref}", program = crate::models::output::program()),
                "draft": remote_catalog_model_draft_ref(model),
                "capabilities": capabilities_json(capabilities::infer_remote_catalog_capabilities(model)),
            })
        })
        .collect();
    json!({
        "query": query,
        "filter": search_filter_name(filter),
        "sort": search_sort_name(sort),
        "source": "catalog",
        "machine": local_capacity_json(),
        "results": payload_results,
    })
}

pub fn search_huggingface_json_payload(
    query: &str,
    filter: SearchArtifactFilter,
    sort: SearchSort,
    results: &[SearchHit],
) -> Value {
    let payload_results: Vec<Value> = results
        .iter()
        .map(|result| {
            json!({
                "repo_id": result.repo_id,
                "repo_url": huggingface_repo_url(&result.repo_id),
                "type": "gguf",
                "variant_count": result.variant_count,
                "size": result.size_label,
                "downloads": result.downloads,
                "likes": result.likes,
                "fit": result
                    .size_label
                    .as_deref()
                    .and_then(fit_code_for_size_label),
                "ref": result.exact_ref,
                "show": format!("{program} models show {}", result.exact_ref, program = crate::models::output::program()),
                "download": format!("{program} models download {}", result.exact_ref, program = crate::models::output::program()),
                "capabilities": capabilities_json(result.capabilities),
                "catalog": result.catalog.as_ref().map(|model| {
                    json!({
                        "name": model.name,
                        "size": model.size,
                        "description": model.description,
                    })
                }),
            })
        })
        .collect();
    json!({
        "query": query,
        "filter": search_filter_name(filter),
        "sort": search_sort_name(sort),
        "source": "huggingface",
        "machine": local_capacity_json(),
        "results": payload_results,
    })
}

// Keep search custom for now. `hf-hub` handles cache and file transport well,
// but it does not expose a Hub search surface in this crate version.
pub async fn search_huggingface<F>(
    query: &str,
    limit: usize,
    filter: SearchArtifactFilter,
    sort: SearchSort,
    mut progress: F,
) -> Result<Vec<SearchHit>>
where
    F: FnMut(SearchProgress),
{
    let mut console = crate::models::output::console_err();
    const SEARCH_CONCURRENCY: usize = 10;

    progress(SearchProgress::SearchingHub);
    let api = skippy_model_hf::build_hf_async_api()?;
    let repos = skippy_model_hf::search::search_repositories(query, limit, filter, sort).await?;

    let total = repos.len();
    progress(SearchProgress::InspectingRepos {
        completed: 0,
        total,
    });

    let context = super::output::context();
    let mut pending = repos.into_iter().enumerate();
    let mut join_set = JoinSet::new();
    for _ in 0..SEARCH_CONCURRENCY.min(total.max(1)) {
        if let Some((index, repo)) = pending.next() {
            let api = api.clone();
            join_set.spawn(super::output::scope(context.clone(), async move {
                (index, build_search_hit(api, repo).await)
            }));
        }
    }

    let mut completed = 0usize;
    let mut indexed_hits = Vec::new();
    while let Some(joined) = join_set.join_next().await {
        let (index, result) = joined.context("Join Hugging Face repo inspection task")?;
        completed += 1;
        progress(SearchProgress::InspectingRepos { completed, total });
        match result {
            Ok(Some(hit)) => {
                indexed_hits.push((index, hit));
            }
            Ok(None) => {}
            Err(err) => {
                writeln!(console, "⚠️  Failed to inspect Hugging Face repo: {err:#}")?;
            }
        }
        if let Some((next_index, repo)) = pending.next() {
            let api = api.clone();
            join_set.spawn(super::output::scope(context.clone(), async move {
                (next_index, build_search_hit(api, repo).await)
            }));
        }
    }

    indexed_hits.sort_by_key(|(index, _)| *index);
    let mut hits: Vec<SearchHit> = indexed_hits.into_iter().map(|(_, hit)| hit).collect();
    apply_local_search_sort(&mut hits, sort);
    hits.truncate(limit);
    Ok(hits)
}

async fn build_search_hit(api: hf_hub::HFClient, repo: ModelInfo) -> Result<Option<SearchHit>> {
    let repo_downloads = repo.downloads;
    let repo_likes = repo.likes;
    let detail = repo;
    let repo_id = detail.id.clone();
    let siblings = detail.siblings.clone().unwrap_or_default();
    let sibling_names: Vec<String> = siblings
        .iter()
        .map(|sibling| sibling.rfilename.clone())
        .collect();
    let sibling_size_entries: Vec<(String, Option<u64>)> = siblings
        .iter()
        .map(|sibling| (sibling.rfilename.clone(), sibling.size))
        .collect();
    let candidates = collect_repo_artifact_candidates(&sibling_names);
    let Some(candidate) = candidates.first() else {
        return Ok(None);
    };

    let variant_count = Some(gguf_variant_count_from_candidates(&repo_id, &candidates));
    let remote_metadata = capabilities::fetch_remote_hf_metadata_jsons(&repo_id, None).await;
    let catalog = matching_remote_catalog_model_for_huggingface(&repo_id, None, candidate);
    let size_label = match catalog {
        Some(ref model) => model.size.clone(),
        None => match size_label_from_sibling_entries(candidate, &sibling_size_entries) {
            Some(size_label) => Some(size_label),
            None => remote_hf_size_label_with_api(&api, &repo_id, None, candidate).await,
        },
    };
    let remote_caps = capabilities::infer_remote_hf_capabilities_with_metadata(
        &repo_id,
        candidate,
        Some(&sibling_names),
        &remote_metadata,
    );
    let capabilities = match catalog {
        Some(ref model) => {
            let base = capabilities::infer_remote_catalog_capabilities(model);
            merge_capabilities(base, remote_caps)
        }
        None => remote_caps,
    };
    Ok(Some(SearchHit {
        repo_id: repo_id.clone(),
        kind: "🦙 GGUF",
        exact_ref: display_exact_ref(&repo_id, candidate),
        variant_count,
        size_label,
        downloads: detail.downloads.or(repo_downloads),
        likes: detail.likes.or(repo_likes),
        catalog,
        capabilities,
    }))
}

fn search_filter_name(filter: SearchArtifactFilter) -> &'static str {
    let SearchArtifactFilter::Gguf = filter;
    "gguf"
}

fn search_sort_name(sort: SearchSort) -> &'static str {
    match sort {
        SearchSort::Trending => "trending",
        SearchSort::Downloads => "downloads",
        SearchSort::Likes => "likes",
        SearchSort::Created => "created",
        SearchSort::Updated => "updated",
        SearchSort::ParametersDesc => "parameters-desc",
        SearchSort::ParametersAsc => "parameters-asc",
    }
}

fn local_capacity_json() -> Value {
    let vram_bytes = crate::models::output::fit_budget_bytes();
    let vram_gb = vram_bytes as f64 / 1e9;
    json!({
        "vram_bytes": vram_bytes,
        "vram_gb": vram_gb,
    })
}

fn capabilities_json(caps: ModelCapabilities) -> Value {
    json!({
        "text": true,
        "multimodal": caps.multimodal_status(),
        "vision": caps.vision_status(),
        "audio": caps.audio_status(),
        "reasoning": caps.reasoning_status(),
        "tool_use": caps.tool_use_status(),
    })
}

fn fit_code_for_size_label(size_label: &str) -> Option<&'static str> {
    let model_gb = skippy_model_artifact::sizes::parse_size_gb(size_label);
    let vram_gb = crate::models::output::fit_budget_bytes() as f64 / 1e9;
    if model_gb <= 0.0 || vram_gb <= 0.0 {
        return None;
    }

    let code = if model_gb <= vram_gb * 0.6 {
        "comfortable"
    } else if model_gb <= vram_gb * 0.9 {
        "tight"
    } else if model_gb <= vram_gb * 1.1 {
        "tradeoff"
    } else {
        "too_large"
    };
    Some(code)
}

fn huggingface_repo_url(repo_id: &str) -> String {
    format!("https://huggingface.co/{repo_id}")
}

fn apply_local_search_sort(hits: &mut [SearchHit], sort: SearchSort) {
    match sort {
        SearchSort::ParametersDesc => {
            hits.sort_by(|left, right| {
                approx_parameter_count_b(right)
                    .partial_cmp(&approx_parameter_count_b(left))
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| left.repo_id.cmp(&right.repo_id))
            });
        }
        SearchSort::ParametersAsc => {
            hits.sort_by(|left, right| {
                approx_parameter_count_b(left)
                    .partial_cmp(&approx_parameter_count_b(right))
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| left.repo_id.cmp(&right.repo_id))
            });
        }
        _ => {}
    }
}

fn approx_parameter_count_b(hit: &SearchHit) -> f64 {
    skippy_model_hf::search::approximate_parameter_count_b_from_text(&format!(
        "{} {}",
        hit.repo_id, hit.exact_ref
    ))
    .unwrap_or(-1.0)
}

fn display_exact_ref(repo: &str, file: &str) -> String {
    match quant_selector_from_gguf_file(file) {
        Some(selector) => format!("{repo}:{selector}"),
        None => format!("{repo}/{}", display_ref_file(file)),
    }
}

fn display_ref_file(file: &str) -> String {
    if let Some(without_ext) = file.strip_suffix(".gguf") {
        if !without_ext.contains("-00001-of-") {
            return without_ext.to_string();
        }
        let Some((prefix, suffix)) = without_ext.rsplit_once("-00001-of-") else {
            return without_ext.to_string();
        };
        if suffix.len() == 5 && suffix.chars().all(|ch| ch.is_ascii_digit()) {
            return prefix.to_string();
        }
        return without_ext.to_string();
    }

    file.to_string()
}

fn size_label_from_sibling_entries(
    file: &str,
    siblings: &[(String, Option<u64>)],
) -> Option<String> {
    gguf_variant_size_bytes_from_siblings(file, siblings).map(super::storage::format_size_bytes)
}

fn collect_repo_artifact_candidates(siblings: &[String]) -> Vec<String> {
    use skippy_model_artifact::{ModelArtifactFile, selection::is_gguf_model_candidate};

    let files = siblings
        .iter()
        .cloned()
        .map(ModelArtifactFile::new)
        .collect::<Vec<_>>();
    let mut candidates = siblings
        .iter()
        .filter(|file| is_gguf_model_candidate(file, &files))
        .cloned()
        .collect::<Vec<_>>();
    candidates.sort_by(|left, right| {
        file_preference_score(left)
            .cmp(&file_preference_score(right))
            .then_with(|| left.cmp(right))
    });
    candidates
}

fn gguf_variant_count_from_candidates(repo: &str, candidates: &[String]) -> usize {
    candidates
        .iter()
        .filter_map(|file| {
            quant_selector_from_gguf_file(file).map(|_| display_exact_ref(repo, file))
        })
        .collect::<HashSet<_>>()
        .len()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};

    fn test_remote_catalog_model() -> remote_catalog::RemoteCatalogModel {
        remote_catalog::RemoteCatalogModel {
            name: "Qwen3-Coder-Next-Q4_K_M".to_string(),
            file: "Qwen3-Coder-Next-Q4_K_M.gguf".to_string(),
            repo: "Qwen/Qwen3-Coder-Next-GGUF".to_string(),
            revision: Some("main".to_string()),
            source_file: "Qwen3-Coder-Next-Q4_K_M.gguf".to_string(),
            size: Some("20GB".to_string()),
            description: Some("Coding model".to_string()),
            draft: None,
            extra_files: Vec::new(),
            mmproj: None,
        }
    }

    fn assert_progress_sequence(events: &[SearchProgress]) {
        assert!(
            events
                .first()
                .is_some_and(|event| matches!(event, SearchProgress::SearchingHub)),
            "expected initial SearchingHub event, got {events:?}"
        );

        let mut last_completed = 0usize;
        let mut last_total = None;
        for event in events {
            if let SearchProgress::InspectingRepos { completed, total } = *event {
                assert!(
                    completed <= total,
                    "completed {completed} exceeded total {total}"
                );
                assert!(
                    completed >= last_completed,
                    "progress regressed from {last_completed} to {completed}"
                );
                if let Some(previous_total) = last_total {
                    assert_eq!(
                        total, previous_total,
                        "repo inspection total changed from {previous_total} to {total}"
                    );
                }
                last_completed = completed;
                last_total = Some(total);
            }
        }

        if let Some(total) = last_total {
            assert_eq!(
                last_completed, total,
                "expected final inspection progress to reach total repos"
            );
        }
    }

    #[test]
    fn collect_repo_artifact_candidates_excludes_checkpoints() {
        let siblings = vec![
            "model.safetensors".to_string(),
            "model.safetensors.index.json".to_string(),
        ];
        let candidates = collect_repo_artifact_candidates(&siblings);
        assert!(candidates.is_empty());
    }

    #[test]
    fn collect_repo_artifact_candidates_keeps_gguf_first_split_only() {
        let siblings = vec![
            "GLM-5.1-UD-Q5_K_XL-00002-of-00002.gguf".to_string(),
            "GLM-5.1-UD-Q5_K_XL-00001-of-00002.gguf".to_string(),
            "GLM-5.1-UD-Q4_K_M.gguf".to_string(),
        ];
        let candidates = collect_repo_artifact_candidates(&siblings);
        assert_eq!(
            candidates,
            vec![
                "GLM-5.1-UD-Q5_K_XL-00001-of-00002.gguf".to_string(),
                "GLM-5.1-UD-Q4_K_M.gguf".to_string(),
            ]
        );
    }

    #[test]
    fn collect_repo_artifact_candidates_excludes_mmproj_gguf_sidecars() {
        let siblings = vec![
            "mmproj-BF16.gguf".to_string(),
            "vision/mmproj-F16.gguf".to_string(),
            "gemma-4-26B-A4B-it-UD-Q3_K_S.gguf".to_string(),
        ];
        let candidates = collect_repo_artifact_candidates(&siblings);
        assert_eq!(
            candidates,
            vec!["gemma-4-26B-A4B-it-UD-Q3_K_S.gguf".to_string()]
        );
    }

    #[test]
    fn gguf_variant_count_from_candidates_counts_selectable_variants() {
        let siblings = vec![
            "mmproj-BF16.gguf".to_string(),
            "BF16/Qwen3.6-35B-A3B-BF16-00001-of-00002.gguf".to_string(),
            "BF16/Qwen3.6-35B-A3B-BF16-00002-of-00002.gguf".to_string(),
            "Qwen3.6-35B-A3B-Q8_0.gguf".to_string(),
            "Qwen3.6-35B-A3B-Q4_K_M.gguf".to_string(),
        ];
        let candidates = collect_repo_artifact_candidates(&siblings);
        assert_eq!(
            gguf_variant_count_from_candidates("unsloth/Qwen3.6-35B-A3B-GGUF", &candidates),
            3
        );
    }

    #[test]
    fn size_label_from_sibling_entries_prefers_repo_metadata_size() {
        let siblings = vec![
            ("model-q4.gguf".to_string(), Some(16_900_000_000)),
            ("model-q5.gguf".to_string(), Some(18_800_000_000)),
        ];
        assert_eq!(
            size_label_from_sibling_entries("model-q4.gguf", &siblings).as_deref(),
            Some("16.9GB")
        );
    }

    #[test]
    fn size_label_from_sibling_entries_sums_split_variant_sizes() {
        let siblings = vec![
            (
                "IQ3_K/Kimi-K2.6-IQ3_K-00001-of-00012.gguf".to_string(),
                Some(6_912_800),
            ),
            (
                "IQ3_K/Kimi-K2.6-IQ3_K-00002-of-00012.gguf".to_string(),
                Some(45_004_320_032),
            ),
            (
                "IQ3_K/Kimi-K2.6-IQ3_K-00003-of-00012.gguf".to_string(),
                Some(45_669_680_480),
            ),
        ];
        assert_eq!(
            size_label_from_sibling_entries("IQ3_K/Kimi-K2.6-IQ3_K-00001-of-00012.gguf", &siblings)
                .as_deref(),
            Some("90.7GB")
        );
    }

    #[test]
    fn size_label_from_sibling_entries_returns_none_when_missing() {
        let siblings = vec![("model-q4.gguf".to_string(), None)];
        assert_eq!(
            size_label_from_sibling_entries("model-q4.gguf", &siblings),
            None
        );
        assert_eq!(
            size_label_from_sibling_entries("model-q5.gguf", &siblings),
            None
        );
    }

    #[test]
    fn display_ref_file_uses_gguf_stems() {
        assert_eq!(display_ref_file("Qwen3-8B-Q4_K_M.gguf"), "Qwen3-8B-Q4_K_M");
        assert_eq!(
            display_ref_file("GLM-5.1-UD-Q5_K_XL-00001-of-00002.gguf"),
            "GLM-5.1-UD-Q5_K_XL"
        );
    }

    #[test]
    fn display_exact_ref_uses_short_quant_for_gguf() {
        assert_eq!(
            display_exact_ref(
                "unsloth/gemma-4-26B-A4B-it-GGUF",
                "gemma-4-26B-A4B-it-UD-Q3_K_S-00001-of-00009.gguf"
            ),
            "unsloth/gemma-4-26B-A4B-it-GGUF:UD-Q3_K_S"
        );
        assert_eq!(
            display_exact_ref(
                "QuantFactory/Meta-Llama-3.1-8B-Instruct-GGUF",
                "Meta-Llama-3.1-8B-Instruct.Q4_K_M.gguf"
            ),
            "QuantFactory/Meta-Llama-3.1-8B-Instruct-GGUF:Q4_K_M"
        );
    }

    #[test]
    fn search_catalog_json_payload_uses_cli_contract_fields() {
        let model = test_remote_catalog_model();
        let payload = search_catalog_json_payload(
            "qwen",
            SearchArtifactFilter::Gguf,
            SearchSort::ParametersDesc,
            std::slice::from_ref(&model),
            1,
        );

        assert_eq!(payload["filter"], serde_json::json!("gguf"));
        assert_eq!(payload["sort"], serde_json::json!("parameters-desc"));
        assert!(payload.get("machine").is_some());
        let result = &payload["results"][0];
        let model_ref = remote_catalog_model_ref(&model);
        assert_eq!(result["ref"], serde_json::json!(model_ref));
        assert_eq!(result["type"], serde_json::json!("gguf"));
        assert_eq!(
            result["show"],
            serde_json::json!(format!(
                "{program} models show {model_ref}",
                program = crate::models::output::program()
            ))
        );
    }

    #[test]
    fn search_huggingface_json_payload_uses_cli_contract_fields() {
        let model = test_remote_catalog_model();
        let payload = search_huggingface_json_payload(
            "qwen",
            SearchArtifactFilter::Gguf,
            SearchSort::ParametersAsc,
            &[SearchHit {
                repo_id: "Qwen/Qwen3-Coder-Next-GGUF".to_string(),
                kind: "🦙 GGUF",
                exact_ref: "Qwen3-Coder-Next-Q4_K_M".to_string(),
                variant_count: Some(3),
                size_label: model.size.clone(),
                downloads: Some(42),
                likes: Some(7),
                catalog: Some(model.clone()),
                capabilities: capabilities::infer_remote_catalog_capabilities(&model),
            }],
        );

        assert_eq!(payload["filter"], serde_json::json!("gguf"));
        assert_eq!(payload["sort"], serde_json::json!("parameters-asc"));
        let result = &payload["results"][0];
        assert_eq!(result["ref"], serde_json::json!("Qwen3-Coder-Next-Q4_K_M"));
        assert_eq!(result["type"], serde_json::json!("gguf"));
        assert_eq!(
            result["repo_url"],
            serde_json::json!("https://huggingface.co/Qwen/Qwen3-Coder-Next-GGUF")
        );
        assert_eq!(
            result["catalog"]["name"],
            serde_json::json!("Qwen3-Coder-Next-Q4_K_M")
        );
    }

    #[tokio::test]
    #[ignore = "live Hugging Face search; run explicitly when validating hub integration"]
    async fn live_search_huggingface_gguf_returns_results_and_reports_progress() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let results = search_huggingface(
            "llama",
            5,
            SearchArtifactFilter::Gguf,
            SearchSort::Trending,
            {
                let events = Arc::clone(&events);
                move |progress| events.lock().unwrap().push(progress)
            },
        )
        .await
        .expect("live gguf search should succeed");

        assert!(
            !results.is_empty(),
            "expected at least one live gguf result"
        );
        assert!(
            results.iter().all(|hit| hit.kind == "🦙 GGUF"),
            "expected only GGUF hits, got {results:?}"
        );
        assert!(
            results
                .iter()
                .all(|hit| !hit.repo_id.is_empty() && !hit.exact_ref.is_empty()),
            "expected populated repo ids and refs, got {results:?}"
        );

        let events = events.lock().unwrap().clone();
        assert_progress_sequence(&events);
    }
}
