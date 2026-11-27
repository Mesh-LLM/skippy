//! Native runtime library discovery for tests that load a runtime bundle: the
//! gated `runtime_events_native` integration test and `test_support`'s
//! `require_native_runtime`.

use std::path::{Path, PathBuf};

/// Resolves the real installed-runtime layout: `SKIPPY_NATIVE_RUNTIME_BUNDLE_DIR`
/// names the PARENT of one or more `<runtime-id>/{manifest.json,lib/*}`
/// subdirectories (see `dist/native-runtimes/README.md` and
/// `skippy-runtime-install`'s own discovery convention), not a flat
/// directory of libraries. A runtime directory named directly, the one with
/// the `manifest.json`, is read the same way. Prefers each candidate's own `manifest.json`
/// `runtime.libraries` ORDER — dependencies before the primary
/// `libllama.dylib` — over a lexicographic guess, since symbol-search order
/// in `skippy-ffi::dynamic::Symbols::load_paths` walks the list in reverse
/// and a naive alphabetical sort places `libllama*` before `libmtmd*`,
/// inverting the manifest's own dependency-then-primary contract.
pub fn discover_libraries(bundle_dir: &Path) -> Vec<PathBuf> {
    if let Some(libraries) = libraries_from_flat_dir(bundle_dir) {
        return libraries;
    }
    if let Some(libraries) = libraries_from_manifest(bundle_dir) {
        return libraries;
    }
    let Ok(entries) = std::fs::read_dir(bundle_dir) else {
        return Vec::new();
    };
    let mut subdirs: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.is_dir())
        .collect();
    subdirs.sort();
    subdirs
        .iter()
        .find_map(|subdir| libraries_from_manifest(subdir))
        .unwrap_or_default()
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

fn libraries_from_manifest(runtime_dir: &Path) -> Option<Vec<PathBuf>> {
    let manifest_path = runtime_dir.join("manifest.json");
    let manifest_text = std::fs::read_to_string(&manifest_path).ok()?;
    let manifest: serde_json::Value = serde_json::from_str(&manifest_text).ok()?;
    let entries = manifest
        .get("runtime")?
        .get("libraries")?
        .as_array()?
        .iter()
        .filter_map(|value| value.as_str());
    let libraries: Vec<PathBuf> = entries
        .map(|relative| runtime_dir.join(relative))
        .filter(|path| path.is_file())
        .collect();
    (!libraries.is_empty()).then_some(libraries)
}

#[cfg(test)]
mod tests {
    use super::discover_libraries;
    use std::path::Path;

    /// A runtime directory as the packager lays it out, with its libraries
    /// listed dependency first.
    fn runtime_dir(parent: &Path) -> std::path::PathBuf {
        let runtime = parent.join("skippy-native-runtime-windows-x86_64-vulkan");
        std::fs::create_dir_all(runtime.join("lib")).unwrap();
        for library in ["ggml.dll", "llama.dll"] {
            std::fs::write(runtime.join("lib").join(library), b"").unwrap();
        }
        std::fs::write(
            runtime.join("manifest.json"),
            r#"{"runtime":{"libraries":["lib/ggml.dll","lib/llama.dll"]}}"#,
        )
        .unwrap();
        runtime
    }

    #[test]
    fn the_parent_of_a_runtime_finds_its_libraries_in_manifest_order() {
        let parent = tempfile::tempdir().unwrap();
        let runtime = runtime_dir(parent.path());
        assert_eq!(
            discover_libraries(parent.path()),
            vec![runtime.join("lib/ggml.dll"), runtime.join("lib/llama.dll")]
        );
    }

    #[test]
    fn a_runtime_named_directly_finds_the_same_libraries() {
        let parent = tempfile::tempdir().unwrap();
        let runtime = runtime_dir(parent.path());
        assert_eq!(
            discover_libraries(&runtime),
            vec![runtime.join("lib/ggml.dll"), runtime.join("lib/llama.dll")]
        );
    }
}
