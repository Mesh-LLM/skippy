use super::*;

#[test]
fn batch_admits_only_all_four_distinct_safe_fixture_paths() {
    let body = include_str!("../../../../crates/skippy-model-artifact/src/kv_fixtures/models.json");
    assert!(read_manifest(body).is_ok());
    assert!(read_manifest(&body.replace("hybrid-moe", "attention-moe")).is_err());
    assert!(read_manifest(&body.replace("Qwen3-4B-Q4_K_M.gguf", "../model.gguf")).is_err());
}

#[test]
fn a_passing_child_cannot_hide_missing_or_mismatched_evidence() {
    let manifest = KvFixtureManifest::bundled().unwrap();
    let model = &manifest.models[0];
    assert!(assess_report(Backend::Metal, model, Ok(()), None).is_err());
    let report = |status, backend: &str| Report {
        schema_version: 10,
        status,
        backend: backend.into(),
        fixture: KvFixtureManifest::bundled().unwrap().models.remove(0),
        model_sha256: Some(model.sha256.clone()),
        skippy_binary_sha256: Some("f".repeat(64)),
        reference: None,
        error: (status == Status::Fail).then(|| "a measured difference".into()),
        conversation_results: CONVERSATIONS
            .into_iter()
            .map(|name| (name.into(), ScenarioReport { status }))
            .collect(),
        structured_results: STRUCTURED
            .into_iter()
            .map(|name| (name.into(), ScenarioReport { status }))
            .collect(),
        template_result: Some(status),
    };
    let evidence = |status, backend: &str| Some(Ok(report(status, backend)));
    let metal = Backend::Metal.profile();
    assert!(assess_report(Backend::Metal, model, Ok(()), evidence(Status::Pass, metal)).is_ok());
    assert!(assess_report(Backend::Cuda, model, Ok(()), evidence(Status::Pass, metal)).is_err());
    let error = assess_report(
        Backend::Metal,
        model,
        Err("child failed".into()),
        evidence(Status::Fail, metal),
    )
    .unwrap_err();
    assert_eq!(error.to_string(), "a measured difference");
    let mut incomplete = report(Status::Pass, metal);
    incomplete.conversation_results.remove("divergent_tail");
    assert!(assess_report(Backend::Metal, model, Ok(()), Some(Ok(incomplete))).is_err());
    let mut failed = report(Status::Pass, metal);
    failed
        .conversation_results
        .get_mut("divergent_tail")
        .unwrap()
        .status = Status::Fail;
    assert!(assess_report(Backend::Metal, model, Ok(()), Some(Ok(failed))).is_err());
    assert!(
        assess_report(
            Backend::Metal,
            model,
            Err("child failed".into()),
            evidence(Status::Pass, metal),
        )
        .is_err()
    );
    let mut missing = report(Status::Pass, metal);
    missing.structured_results.remove("long_retrieval");
    assert!(assess_report(Backend::Metal, model, Ok(()), Some(Ok(missing))).is_err());
    let mut missing_template = report(Status::Pass, metal);
    missing_template.template_result = None;
    assert!(assess_report(Backend::Metal, model, Ok(()), Some(Ok(missing_template))).is_err());
    let mut stale = report(Status::Pass, metal);
    stale.fixture.revision = "e".repeat(40);
    assert!(assess_report(Backend::Metal, model, Ok(()), Some(Ok(stale))).is_err());
    let mut foreign = report(Status::Pass, metal);
    foreign.model_sha256 = Some("e".repeat(64));
    assert!(assess_report(Backend::Metal, model, Ok(()), Some(Ok(foreign))).is_err());
}
