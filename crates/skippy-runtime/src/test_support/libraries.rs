//! Native runtime library discovery for tests that load a runtime bundle: the
//! gated `runtime_events_native` integration test and `test_support`'s
//! `require_native_runtime`.

use anyhow::{Context, Result};
use skippy_native_runtime::{InstalledNativeRuntime, NativeRuntimeLoadPlan, NativeRuntimeManifest};
use std::path::{Path, PathBuf};

/// Resolves the real installed-runtime layout: `SKIPPY_NATIVE_RUNTIME_BUNDLE_DIR`
/// names the PARENT of one or more `<runtime-id>/{manifest.json,lib/*}`
/// subdirectories (see `dist/native-runtimes/README.md` and
/// `skippy-runtime-install`'s own discovery convention), not a flat
/// directory of libraries. Prefers each candidate's own `manifest.json`
/// `runtime.libraries` ORDER — dependencies before the primary
/// `libllama.dylib` — over a lexicographic guess, since symbol-search order
/// in `skippy-ffi::dynamic::Symbols::load_paths` walks the list in reverse
/// and a naive alphabetical sort places `libllama*` before `libmtmd*`,
/// inverting the manifest's own dependency-then-primary contract.
pub fn discover_load_plan(bundle_dir: &Path) -> Result<NativeRuntimeLoadPlan> {
    if bundle_dir.join("manifest.json").is_file() {
        return plan_from_manifest(bundle_dir);
    }
    let mut subdirs: Vec<PathBuf> = std::fs::read_dir(bundle_dir)?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.join("manifest.json").is_file())
        .collect();
    subdirs.sort();
    if let Some(path) = subdirs.first() {
        return plan_from_manifest(path);
    }
    // An explicit flat library directory is also supported for local
    // test builds. The loader checks their exported ABI and selects any
    // CPU backend from the explicitly supplied file list.
    let libraries = libraries_from_flat_dir(bundle_dir)
        .with_context(|| format!("no native runtime under {}", bundle_dir.display()))?;
    Ok(NativeRuntimeLoadPlan {
        release_version: String::new(),
        native_runtime_id: "local-test".into(),
        root: bundle_dir.into(),
        libraries,
        cpu_backend_modules: Vec::new(),
    })
}

fn libraries_from_flat_dir(dir: &Path) -> Option<Vec<PathBuf>> {
    let entries = std::fs::read_dir(dir).ok()?;
    let mut libraries: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.extension()
                .and_then(|extension| extension.to_str())
                .is_some_and(|extension| matches!(extension, "dylib" | "so" | "dll"))
        })
        .collect();
    if libraries.is_empty() {
        return None;
    }
    libraries.sort();
    Some(libraries)
}

fn plan_from_manifest(dir: &Path) -> Result<NativeRuntimeLoadPlan> {
    let manifest = NativeRuntimeManifest::read_from_dir(dir)?;
    manifest.verify_payload(dir)?;
    InstalledNativeRuntime {
        release_version: manifest.runtime.release_version.clone().unwrap_or_default(),
        native_runtime_id: manifest.runtime.id.clone(),
        flavor: manifest.runtime.backend.kind.to_string(),
        path: dir.into(),
        manifest,
    }
    .load_plan()
}
