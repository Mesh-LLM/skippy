//! Exact package metadata resolution for embedding hosts.

use std::{
    fmt, fs,
    io::ErrorKind,
    path::{Path, PathBuf},
};

use anyhow::{Context, Result, ensure};
use skippy_model_ref::package_reference::PackageReference;
use skippy_package_format::{
    Artifact, PACKAGE_SCHEMA_VERSION, PackageManifest, stage_admission::StageAdmissionDescriptor,
};

use super::remote::PackageAcquisition;
use crate::{
    materialization::{
        ensure_package_manifest_sha, safe_manifest_file_path, verify_package_v2_artifact,
    },
    package::{
        PACKAGE_V2_MANIFEST, SkippyPackageIdentity, metadata::identity_from_package_v2_metadata,
    },
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum PackageResolutionPolicy {
    CacheOnly,
    AllowUpstream,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum PackageResolutionSource {
    Local,
    Cache,
    Upstream,
}

#[derive(Debug)]
#[non_exhaustive]
pub struct UnsupportedPackageSchema {
    pub schema: u64,
    pub source: PackageResolutionSource,
}

impl fmt::Display for UnsupportedPackageSchema {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "unsupported package schema {} from {:?}; Skippy requires schema {PACKAGE_SCHEMA_VERSION}",
            self.schema, self.source
        )
    }
}

impl std::error::Error for UnsupportedPackageSchema {}

/// Verified metadata and its exact source. For HF, the identity contains a
/// commit-qualified reference. For local inputs, it contains a canonical path
/// and a manifest digest that the stage acquisition method checks again.
#[derive(Debug)]
pub struct ResolvedPackageIdentity {
    identity: SkippyPackageIdentity,
    snapshot: PathBuf,
    blobs: Option<PathBuf>,
    source: PackageResolutionSource,
    policy: PackageResolutionPolicy,
}

impl ResolvedPackageIdentity {
    pub fn identity(&self) -> &SkippyPackageIdentity {
        &self.identity
    }

    pub fn snapshot(&self) -> &Path {
        &self.snapshot
    }

    pub fn source(&self) -> PackageResolutionSource {
        self.source
    }

    /// Acquire the exact admitted artifact closure for the inspected identity.
    /// Cache-only inspection also opts out of network access here.
    pub fn acquire_stage(
        &self,
        acquisition: &PackageAcquisition,
        admission: &StageAdmissionDescriptor,
    ) -> Result<(String, Vec<PathBuf>, Option<PathBuf>)> {
        let path = self
            .snapshot
            .to_str()
            .context("package snapshot path is not UTF-8")?;
        let check_manifest = || {
            confined_file(
                &self.snapshot.join(PACKAGE_V2_MANIFEST),
                &self.snapshot,
                self.blobs.as_deref(),
            )?;
            ensure_package_manifest_sha(path, &self.identity.manifest_sha256)
        };
        check_manifest()?;
        let remote_ref = (self.policy == PackageResolutionPolicy::AllowUpstream
            && self.blobs.is_some())
        .then_some(self.identity.package_ref.as_str());
        let result = acquisition.acquire_stage_from_snapshot(
            &self.snapshot,
            remote_ref,
            admission,
            Some(&|path| confined_file(path, &self.snapshot, self.blobs.as_deref()).map(|_| ())),
        )?;
        check_manifest()?;
        Ok(result)
    }
}

impl PackageAcquisition {
    /// Resolve and verify supported package metadata without acquiring layers.
    ///
    /// A stale cached schema can trigger one upstream manifest lookup for a
    /// mutable revision. Invalid schema markers, parse errors and digest
    /// mismatches are returned immediately.
    pub fn resolve_and_inspect(
        &self,
        reference: &str,
        policy: PackageResolutionPolicy,
    ) -> Result<ResolvedPackageIdentity> {
        if !reference.starts_with("hf://") {
            let snapshot = Path::new(reference)
                .canonicalize()
                .context("locate local package")?;
            let reference = snapshot
                .to_str()
                .context("local package path is not UTF-8")?;
            let metadata = inspect_manifest(&snapshot, None, PackageResolutionSource::Local)?;
            return verified_identity(
                reference,
                &snapshot,
                None,
                &metadata,
                PackageResolutionSource::Local,
                policy,
            );
        }

        let reference = PackageReference::parse(reference)?;
        let repo = reference.repo();
        let revision = reference.revision();
        let folder = PackageCache::new(&self.hub_cache, repo)?;
        if let Some(commit) = folder.cached_commit(repo, revision)? {
            let snapshot = folder.snapshot(&commit)?;
            if file_present(&snapshot.join(PACKAGE_V2_MANIFEST))? {
                match inspect_manifest(
                    &snapshot,
                    Some(&folder.blobs),
                    PackageResolutionSource::Cache,
                ) {
                    Ok(metadata) => {
                        let pinned = format!("hf://{repo}@{commit}");
                        let source = self.ensure_identity_metadata(
                            &folder, &snapshot, repo, &commit, &metadata, policy,
                        )?;
                        return verified_identity(
                            &pinned,
                            &snapshot,
                            Some(folder.blobs),
                            &metadata,
                            source,
                            policy,
                        );
                    }
                    Err(error) if permits_refresh(&error, revision, policy) => {}
                    Err(error) => return Err(error),
                }
            }
        }
        ensure!(
            policy == PackageResolutionPolicy::AllowUpstream,
            "package metadata for {repo}@{revision} is absent from the local cache"
        );
        self.inspect_upstream(repo, revision, policy)
    }

    fn inspect_upstream(
        &self,
        repo: &str,
        revision: &str,
        policy: PackageResolutionPolicy,
    ) -> Result<ResolvedPackageIdentity> {
        let manifest = self.fetch_identity_file(repo, revision, PACKAGE_V2_MANIFEST, None)?;
        let snapshot = manifest
            .parent()
            .context("manifest has no HF snapshot directory")?;
        let commit = snapshot
            .file_name()
            .and_then(|name| name.to_str())
            .context("snapshot has no commit")?;
        let folder = PackageCache::new(&self.hub_cache, repo)?;
        let expected = folder.snapshot(commit)?;
        ensure!(
            snapshot.canonicalize()? == expected && (!is_commit(revision) || revision == commit),
            "Hub manifest resolved outside the requested package snapshot"
        );
        let metadata = inspect_manifest(
            &expected,
            Some(&folder.blobs),
            PackageResolutionSource::Upstream,
        )?;
        self.ensure_identity_metadata(&folder, &expected, repo, commit, &metadata, policy)?;
        let pinned = format!("hf://{repo}@{commit}");
        verified_identity(
            &pinned,
            &expected,
            Some(folder.blobs),
            &metadata,
            PackageResolutionSource::Upstream,
            policy,
        )
    }

    fn ensure_identity_metadata(
        &self,
        folder: &PackageCache,
        snapshot: &Path,
        repo: &str,
        commit: &str,
        metadata: &Artifact,
        policy: PackageResolutionPolicy,
    ) -> Result<PackageResolutionSource> {
        let path = snapshot.join(safe_manifest_file_path(&metadata.path)?);
        if file_present(&path)? {
            return Ok(PackageResolutionSource::Cache);
        }
        ensure!(
            policy == PackageResolutionPolicy::AllowUpstream,
            "package metadata artifact is absent from the local cache"
        );
        let downloaded =
            self.fetch_identity_file(repo, commit, &metadata.path, Some(metadata.byte_size))?;
        ensure!(
            confined_file(&path, snapshot, Some(&folder.blobs))? == downloaded.canonicalize()?,
            "Hub metadata returned a different package artifact"
        );
        Ok(PackageResolutionSource::Upstream)
    }
}

struct PackageCache {
    root: PathBuf,
    repository: PathBuf,
    blobs: PathBuf,
}

impl PackageCache {
    fn new(root: &Path, repo: &str) -> Result<Self> {
        ensure!(!repo.contains("--"), "unsafe HF repository ID");
        // Normalize existing roots. For a cold cache the client may create it.
        let root = match root.canonicalize() {
            Ok(root) => root,
            Err(error) if error.kind() == ErrorKind::NotFound => std::path::absolute(root)?,
            Err(error) => return Err(error.into()),
        };
        let repository = root.join(format!("models--{}", repo.replace('/', "--")));
        let blobs = repository.join("blobs");
        for path in [
            &repository,
            &repository.join("snapshots"),
            &repository.join("refs"),
            &blobs,
        ] {
            if file_present(path)? {
                ensure!(
                    path.is_dir() && path.canonicalize()? == *path,
                    "HF package cache directory redirected outside its exact path: {}",
                    path.display()
                );
            }
        }
        Ok(Self {
            root,
            blobs,
            repository,
        })
    }

    fn cached_commit(&self, repo: &str, revision: &str) -> Result<Option<String>> {
        if !is_commit(revision) {
            let reference = self.repository.join("refs").join(revision);
            if file_present(&reference)? {
                ensure!(
                    reference.canonicalize()? == reference,
                    "HF cache ref redirected outside its exact path"
                );
            }
        }
        Ok(hf_hub::cache::resolve_cached_revision(
            &self.root,
            repo,
            hf_hub::RepoTypeModel,
            revision,
        )?)
    }

    fn snapshot(&self, commit: &str) -> Result<PathBuf> {
        ensure!(
            is_commit(commit),
            "HF cache snapshot is not an immutable commit"
        );
        let snapshot = self.repository.join("snapshots").join(commit);
        match snapshot.canonicalize() {
            Ok(actual) => ensure!(
                actual == snapshot && actual.is_dir(),
                "HF package snapshot escaped its exact cache path"
            ),
            Err(error) if error.kind() == ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        Ok(snapshot)
    }
}

fn is_commit(revision: &str) -> bool {
    revision.len() == 40 && revision.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn file_present(path: &Path) -> Result<bool> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error.into()),
    }
}

fn confined_file(path: &Path, snapshot: &Path, blobs: Option<&Path>) -> Result<PathBuf> {
    let actual = path
        .canonicalize()
        .with_context(|| format!("locate package file {}", path.display()))?;
    ensure!(
        actual.is_file()
            && (actual.starts_with(snapshot)
                || blobs.is_some_and(|blobs| actual.starts_with(blobs))),
        "package file escaped its snapshot and repository blobs: {}",
        path.display()
    );
    Ok(actual)
}

fn permits_refresh(error: &anyhow::Error, revision: &str, policy: PackageResolutionPolicy) -> bool {
    policy == PackageResolutionPolicy::AllowUpstream
        && !is_commit(revision)
        && error
            .downcast_ref::<UnsupportedPackageSchema>()
            .is_some_and(|unsupported| unsupported.schema < u64::from(PACKAGE_SCHEMA_VERSION))
}

fn inspect_manifest(
    snapshot: &Path,
    blobs: Option<&Path>,
    source: PackageResolutionSource,
) -> Result<Artifact> {
    let path = confined_file(&snapshot.join(PACKAGE_V2_MANIFEST), snapshot, blobs)?;
    let bytes = fs::read(&path).context("read package manifest")?;
    let envelope: serde_json::Value =
        serde_json::from_slice(&bytes).context("parse package manifest envelope")?;
    let schema = envelope
        .get("schema_version")
        .and_then(serde_json::Value::as_u64)
        .context("package manifest is missing integer schema_version")?;
    if schema != u64::from(PACKAGE_SCHEMA_VERSION) {
        return Err(UnsupportedPackageSchema { schema, source }.into());
    }
    let manifest: PackageManifest =
        serde_json::from_slice(&bytes).context("parse package-v2 manifest")?;
    manifest
        .validate_root()
        .context("validate package-v2 manifest")?;
    ensure!(
        manifest.package_id == manifest.computed_package_id()?,
        "package-v2 manifest package_id does not match its content"
    );
    manifest
        .artifact_catalog
        .entries
        .into_iter()
        .find(|artifact| artifact.id == manifest.source_model.metadata_artifact_id)
        .context("package-v2 metadata artifact is absent")
}

fn verified_identity(
    reference: &str,
    snapshot: &Path,
    blobs: Option<PathBuf>,
    metadata: &Artifact,
    source: PackageResolutionSource,
    policy: PackageResolutionPolicy,
) -> Result<ResolvedPackageIdentity> {
    let metadata_path = snapshot.join(safe_manifest_file_path(&metadata.path)?);
    confined_file(&metadata_path, snapshot, blobs.as_deref())?;
    let local = snapshot
        .to_str()
        .context("package snapshot path is not UTF-8")?;
    verify_package_v2_artifact(snapshot, metadata)?;
    let mut identity = identity_from_package_v2_metadata(reference, local, None)?;
    identity.package_ref = reference.to_owned();
    Ok(ResolvedPackageIdentity {
        identity,
        snapshot: snapshot.into(),
        blobs,
        source,
        policy,
    })
}
