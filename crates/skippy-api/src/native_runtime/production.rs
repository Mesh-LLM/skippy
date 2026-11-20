use std::{
    path::{Path, PathBuf},
    sync::Mutex,
};

use anyhow::{Context, Result, ensure};
use sha2::{Digest, Sha256};
use skippy_native_runtime::{
    InstalledNativeRuntime, NATIVE_RUNTIME_MANIFEST_FILE, NativeRuntimeManifest, RuntimeSelection,
};
use skippy_runtime_install::{current_skippy_abi_version, host_runtime_profile};

/// Identity of the native bundle admitted for this process.
#[derive(Clone, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub struct RuntimeIdentity {
    pub manifest_sha256: String,
    pub runtime_id: String,
    pub release: String,
    pub abi: String,
    pub llama_upstream_sha: String,
    pub llama_patched_sha: String,
    pub llama_patch_digest: String,
    pub bundle: PathBuf,
}

#[derive(serde::Deserialize)]
struct BundleBuild {
    llama_upstream_sha: String,
    llama_patched_sha: String,
    llama_patch_digest: String,
}

#[derive(serde::Deserialize)]
struct BundleProvenance {
    build: BundleBuild,
}

/// Verified dynamic runtime selection. Libraries remain loaded for the process lifetime.
#[derive(Clone, Debug)]
pub struct ProductionRuntime(RuntimeIdentity);

static RUNTIME: Mutex<Option<RuntimeIdentity>> = Mutex::new(None);

impl ProductionRuntime {
    /// Verify a trusted bundle manifest pin, its payload, host compatibility, and the
    /// ABI exported by the native library. Keep the bundle immutable while it is in use.
    ///
    /// A second call with the same bundle and pin succeeds. A different bundle,
    /// or libraries already installed by another loader, are rejected.
    pub fn open_bundle(bundle: impl AsRef<Path>, manifest_sha256: &str) -> Result<Self> {
        let bundle = bundle
            .as_ref()
            .canonicalize()
            .context("locate native bundle")?;
        let bytes = std::fs::read(bundle.join(NATIVE_RUNTIME_MANIFEST_FILE))
            .context("read native bundle manifest")?;
        let digest = hex::encode(Sha256::digest(&bytes));
        ensure!(
            digest == manifest_sha256,
            "native bundle manifest SHA-256 mismatch"
        );

        let mut active = RUNTIME
            .lock()
            .map_err(|_| anyhow::anyhow!("native startup lock poisoned"))?;
        if let Some(identity) = active.as_ref() {
            ensure!(
                identity.bundle == bundle && identity.manifest_sha256 == digest,
                "another production native bundle is already loaded"
            );
            return Ok(Self(identity.clone()));
        }
        ensure!(
            !skippy_runtime::native_runtime_loaded(),
            "native runtime was installed outside the production bundle loader"
        );

        let manifest: NativeRuntimeManifest =
            serde_json::from_slice(&bytes).context("parse native bundle manifest")?;
        let provenance: BundleProvenance =
            serde_json::from_slice(&bytes).context("read native bundle recipe")?;
        let build = provenance.build;
        let expected_patch_digest = option_env!("SKIPPY_RUNTIME_PATCH_DIGEST").context(
            "this build does not have the source patch queue for production verification",
        )?;
        ensure!(
            build.llama_upstream_sha == env!("SKIPPY_LLAMA_UPSTREAM_SHA")
                && build.llama_patch_digest == expected_patch_digest,
            "native bundle recipe differs from the embedded source revision"
        );
        ensure!(
            build.llama_patched_sha.len() == 40
                && build
                    .llama_patched_sha
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit()),
            "native bundle patched source SHA is invalid"
        );
        let release = skippy_native_runtime::runtime_release_version();
        let abi = current_skippy_abi_version();
        let runtime = InstalledNativeRuntime {
            release_version: manifest
                .runtime
                .release_version
                .clone()
                .context("native bundle release is missing")?,
            native_runtime_id: manifest.runtime.id.clone(),
            flavor: manifest.runtime.backend.kind.as_str().to_owned(),
            path: bundle.clone(),
            manifest,
        };
        let plan = skippy_runtime_install::startup::select_local_native_runtime_plan(
            std::slice::from_ref(&runtime),
            &host_runtime_profile(),
            release,
            Some(&abi),
            &RuntimeSelection::parse(None)?,
        )?
        .context("native bundle is incompatible with this Skippy release, ABI, or host")?;
        runtime.manifest.verify_payload(&bundle)?;
        // SAFETY: the host supplied a trusted manifest pin, and the runtime
        // resolver and payload checks bound the platform, ABI and library set.
        unsafe { skippy_runtime::load_native_runtime_libraries(&plan.libraries) }
            .context("load verified production native libraries")?;

        let identity = RuntimeIdentity {
            manifest_sha256: digest,
            runtime_id: plan.native_runtime_id,
            release: plan.release_version,
            abi: runtime.manifest.runtime.skippy_abi,
            llama_upstream_sha: build.llama_upstream_sha,
            llama_patched_sha: build.llama_patched_sha,
            llama_patch_digest: build.llama_patch_digest,
            bundle,
        };
        *active = Some(identity.clone());
        Ok(Self(identity))
    }

    pub fn identity(&self) -> &RuntimeIdentity {
        &self.0
    }
}

/// The upstream and ordered patch queue compiled into this Skippy source revision.
pub fn native_source_recipe() -> (&'static str, &'static str) {
    (
        env!("SKIPPY_LLAMA_UPSTREAM_SHA"),
        env!("SKIPPY_PATCH_QUEUE_SHA256"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn pinned_manifest_with_wrong_recipe_fails_before_library_load() {
        let directory = tempfile::tempdir().unwrap();
        let manifest = json!({
            "schema_version": 2,
            "runtime": {
                "id": "skippy-offline-recipe-test",
                "release_version": skippy_native_runtime::runtime_release_version(),
                "skippy_abi": current_skippy_abi_version(),
                "platform": { "os": std::env::consts::OS, "arch": std::env::consts::ARCH },
                "backend": { "kind": "cpu" },
                "libraries": ["lib/native"],
                "files": { "lib/native": "0".repeat(64) },
            },
            "build": {
                "llama_upstream_sha": "0".repeat(40),
                "llama_patched_sha": "0".repeat(40),
                "llama_patch_digest": "0".repeat(64),
            },
        });
        let bytes = serde_json::to_vec(&manifest).unwrap();
        std::fs::write(directory.path().join(NATIVE_RUNTIME_MANIFEST_FILE), &bytes).unwrap();
        let digest = hex::encode(Sha256::digest(&bytes));
        let error = ProductionRuntime::open_bundle(directory.path(), &digest).unwrap_err();
        assert!(
            error.to_string().contains("native bundle recipe differs"),
            "{error:#}"
        );
    }
}
