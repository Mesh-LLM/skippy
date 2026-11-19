use super::*;

#[test]
fn test_only_fields_and_cfg_attributes_have_no_native_production_effect() {
    for (before, after) in [
        (
            "struct S { x: u32, #[cfg(test)] check: bool }",
            "struct S { x: u32, #[cfg(test)] check: u64 }",
        ),
        (
            "enum E { Value, #[cfg(test)] Check }",
            "enum E { Value, #[cfg(test)] Different }",
        ),
        ("#[cfg_attr(test, allow(unused))] fn f() {}", "fn f() {}"),
        (
            "#[cfg(windows)] fn f() { old(); }",
            "#[cfg(windows)] fn f() { new(); }",
        ),
    ] {
        assert_eq!(
            rust::projection(before).unwrap(),
            rust::projection(after).unwrap()
        );
    }
    assert!(rust::possible_target("cfg(all(unix, target_arch=\"aarch64\"))").unwrap());
    assert!(!rust::possible_target("cfg(windows)").unwrap());
    assert!(rust::possible_target("cfg(feature=\"cuda\")").unwrap());
}

#[test]
fn embedded_assets_are_inputs_even_when_the_extension_looks_like_documentation() {
    let mut files = fixture();
    files.both(
        "crates/skippy-cli/src/main.rs",
        "const PROMPT: &str = include_str!(\"../prompt.md\"); fn main() {}",
    );
    assert!(
        !decision(&mut files, "crates/skippy-cli/prompt.md")
            .reasons
            .is_empty()
    );
    assert!(
        decision(&mut files, "crates/skippy-cli/LICENSE")
            .reasons
            .is_empty()
    );
}

#[test]
fn native_profiles_ignore_release_and_unrelated_package_settings() {
    let mut files = fixture();
    files.set("head", "Cargo.toml", "[workspace]\nresolver='2'\n[profile.release]\nlto=true\n[profile.dev.package.unrelated]\nopt-level=3\n");
    assert!(decision(&mut files, "Cargo.toml").reasons.is_empty());
    files.set(
        "head",
        "Cargo.toml",
        "[workspace]\nresolver='2'\n[profile.dev]\nopt-level=3\n",
    );
    assert!(!decision(&mut files, "Cargo.toml").reasons.is_empty());
}

#[test]
fn dependency_inheritance_target_selection_and_features_are_compared() {
    let mut files = fixture();
    files.both(
        "Cargo.toml",
        "[workspace]\nresolver='2'\n[workspace.dependencies]\nserde='1'\n",
    );
    files.both("crates/skippy-cli/Cargo.toml", "[package]\nname='skippy-cli'\nversion='1.0.0'\n[dependencies]\nserde={workspace=true,features=['derive']}\n");
    files.set("head", "crates/skippy-cli/Cargo.toml", "[package]\nname='skippy-cli'\nversion='1.0.0'\n[dependencies]\nserde={workspace=true,features=['std']}\n");
    assert!(
        !decision(&mut files, "crates/skippy-cli/Cargo.toml")
            .reasons
            .is_empty()
    );
    let mut files = fixture();
    files.set("head", "crates/skippy-cli/Cargo.toml", "[package]\nname='skippy-cli'\nversion='1.0.0'\n[target.'cfg(windows)'.dependencies]\nwindows-only='1'\n");
    assert!(
        decision(&mut files, "crates/skippy-cli/Cargo.toml")
            .reasons
            .is_empty()
    );
}

#[test]
fn actual_justfile_and_configuration_have_supported_projections() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let read = |path: &str| std::fs::read_to_string(root.join(path)).unwrap();
    let justfile = read("Justfile");
    let changed = justfile.replace(
        "@npm ci --prefix docs-site",
        "@npm ci --prefix changed-docs",
    );
    assert_eq!(
        recipes::projection(&justfile).unwrap(),
        recipes::projection(&changed).unwrap()
    );
    assert_ne!(
        recipes::projection(&justfile).unwrap(),
        recipes::projection(&justfile.replace(
            "cargo build --locked -p skippy-cli -p skippy-correctness",
            "cargo build --locked -p changed-cli"
        ))
        .unwrap()
    );
    config::templates(&read(".gi/templates.yaml")).unwrap();
    config::cargo_config(&read(".cargo/config.toml")).unwrap();
    let chained = justfile.replace("inference-build:", "inference-build: native-helper");
    let chained = format!("{chained}\nnative-helper:\n    original-command\n");
    assert_ne!(
        recipes::projection(&chained).unwrap(),
        recipes::projection(&chained.replace("original-command", "changed-command")).unwrap()
    );
}

#[test]
fn candidate_worker_dependencies_remain_explicit_and_complete() {
    use quote::ToTokens;
    fn identifiers(
        tokens: proc_macro2::TokenStream,
        found: &mut std::collections::BTreeSet<String>,
    ) {
        for token in tokens {
            match token {
                proc_macro2::TokenTree::Ident(ident) => {
                    found.insert(ident.to_string());
                }
                proc_macro2::TokenTree::Group(group) => identifiers(group.stream(), found),
                _ => {}
            }
        }
    }
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut names = std::collections::BTreeSet::new();
    for path in dependencies::WORKER_FILES {
        let text = std::fs::read_to_string(root.join(path)).unwrap();
        let projected = rust::projection(&text).unwrap();
        identifiers(
            syn::parse_file(&projected).unwrap().into_token_stream(),
            &mut names,
        );
    }
    let manifest: toml::Value =
        toml::from_str(&std::fs::read_to_string(root.join("tools/xtask/Cargo.toml")).unwrap())
            .unwrap();
    for dependency in manifest["dependencies"].as_table().unwrap().keys() {
        if names.contains(&dependency.replace('-', "_")) {
            assert!(
                dependencies::WORKER_DEPENDENCIES.contains(&dependency.as_str()),
                "candidate worker uses untracked dependency {dependency}"
            );
        }
    }
}

#[test]
fn the_immutable_git_reader_is_offline_and_does_not_read_worktree_changes() {
    let revision =
        crate::execution::capture("git", &["rev-parse".into(), "HEAD".into()], &[]).unwrap();
    let revision = revision.trim();
    let mut source = GitSource::local(revision, revision).unwrap();
    assert!(
        source
            .read(revision, "Cargo.toml")
            .unwrap()
            .unwrap()
            .contains("[workspace]")
    );
    assert!(source.read(revision, "missing-input").unwrap().is_none());
    assert!(source.read(revision, "../Cargo.toml").is_err());
    assert_eq!(source.changes(revision, revision).unwrap(), json!([[]]));
}
