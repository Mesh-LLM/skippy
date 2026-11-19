use super::*;

#[test]
fn native_templates_ignore_ui_and_other_templates_but_keep_worker_commands() {
    let original = json!({"templates":[
        {"key":"inference-pr-standalone","command":"serve","name":"old"},
        {"key":"inference-pr-split","command":"split"}, {"key":"review","command":"old"}
    ]});
    let mut next = original.clone();
    next["templates"][0]["name"] = "new label".into();
    next["templates"][2]["command"] = "new review".into();
    assert_eq!(
        config::templates(&original.to_string()).unwrap(),
        config::templates(&next.to_string()).unwrap()
    );
    next["templates"][0]["command"] = "different serving".into();
    assert_ne!(
        config::templates(&original.to_string()).unwrap(),
        config::templates(&next.to_string()).unwrap()
    );
    assert!(config::templates("{}").is_err());
}

#[test]
fn justfile_ignores_unrelated_recipes_and_tracks_native_recipes_and_exports() {
    let original = "unused := 'old'\nexport CUDA := 'on'\nautomation-run *args:\n    cargo xtool {{ args }}\ninference-build:\n    build\ninference-build-uncached:\n    compile\ninference-execute:\n    execute\nllama-build:\n    native\nllama-prepare:\n    prepare\ndocs-build:\n    old-docs\n";
    assert_eq!(
        recipes::projection(original).unwrap(),
        recipes::projection(&original.replace("old", "new")).unwrap()
    );
    assert_ne!(
        recipes::projection(original).unwrap(),
        recipes::projection(&original.replace("    compile", "    different-compile")).unwrap()
    );
    assert_ne!(
        recipes::projection(original).unwrap(),
        recipes::projection(&original.replace("CUDA := 'on'", "CUDA := 'off'")).unwrap()
    );
    assert!(recipes::projection("docs:\n    build").is_err());
}

#[test]
fn manifests_ignore_metadata_and_dev_dependencies_but_track_runtime_dependencies() {
    let mut files = fixture();
    let path = "crates/skippy-cli/Cargo.toml";
    let original = files.read("base", path).unwrap().unwrap();
    files.set(
        "head",
        path,
        &format!("{original}description='new description'\n[dev-dependencies]\ntempfile='3'\n"),
    );
    assert!(decision(&mut files, path).reasons.is_empty());
    files.set(
        "head",
        path,
        &format!("{original}[dependencies]\nserde='1'\n"),
    );
    assert!(!decision(&mut files, path).reasons.is_empty());
}
