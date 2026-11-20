use super::*;
use crate::package::acquisition::{
    PackageResolutionPolicy::{AllowUpstream, CacheOnly},
    PackageResolutionSource, UnsupportedPackageSchema,
};
use skippy_model::gguf_metadata::{GgufKv, write_kv};
use skippy_package_format::{
    Artifact, ArtifactCatalog, PACKAGE_SCHEMA_VERSION, PackageManifest, SourceFile, SourceModel,
};
use std::sync::atomic::AtomicUsize;

const OLD_COMMIT: &str = "1111111111111111111111111111111111111111";
const REFERENCE: &str = "hf://test/package";
const MANIFEST: &str = "model-package.json";
const METADATA: &str = "metadata.gguf";
const LAYER: &str = "layer.gguf";

fn package_fixture() -> (PackageManifest, BTreeMap<String, Vec<u8>>) {
    let metadata = carrier();
    let layer = vec![7_u8; 32];
    let artifact = |id: &str, path: &str, bytes: &[u8]| Artifact {
        id: id.into(),
        path: path.into(),
        byte_size: bytes.len() as u64,
        sha256: sha256_hex(bytes),
    };
    let mut manifest = PackageManifest {
        schema_version: PACKAGE_SCHEMA_VERSION,
        package_id: String::new(),
        model_id: "fixture/model".into(),
        source_model: SourceModel {
            sha256: sha256_hex(&layer),
            metadata_artifact_id: "metadata".into(),
            repo: None,
            revision: None,
            primary_file: Some("model.gguf".into()),
            canonical_ref: None,
            distribution_id: None,
            files: vec![SourceFile {
                path: "model.gguf".into(),
                byte_size: layer.len() as u64,
                sha256: sha256_hex(&layer),
            }],
        },
        format: "gguf".into(),
        layer_count: 1,
        model_metadata: Default::default(),
        artifact_catalog: ArtifactCatalog {
            entries: vec![
                artifact("layer-00000", LAYER, &layer),
                artifact("metadata", METADATA, &metadata),
            ],
        },
        tensor_catalog: skippy_package_format::TensorCatalog {
            entries: Vec::new(),
        },
        sidecars: Vec::new(),
        publisher_metadata: Vec::new(),
        publisher_defaults: None,
        generation: None,
        native_abi_version: "0.1.80".into(),
        generator_version: "test".into(),
        created_at_unix_secs: 1,
    };
    manifest.package_id = manifest.computed_package_id().unwrap();
    manifest.validate_root().unwrap();
    let files = BTreeMap::from([
        (MANIFEST.into(), serde_json::to_vec(&manifest).unwrap()),
        (METADATA.into(), metadata),
        (LAYER.into(), layer),
    ]);
    (manifest, files)
}

fn carrier() -> Vec<u8> {
    let fields = [
        GgufKv::string("general.architecture", "llama"),
        GgufKv::u32("llama.embedding_length", 8),
        GgufKv::u32("llama.block_count", 1),
        GgufKv::bool("skippy.package.metadata_only", true),
        GgufKv::u32("skippy.package.part_count", 1),
        GgufKv::u32("skippy.package.locator_schema", 1),
        GgufKv::array_u32("skippy.package.tensor_part", vec![0]),
        GgufKv::array_u32("skippy.package.tensor_offset", vec![0]),
        GgufKv::array_u32("skippy.package.tensor_size", vec![32]),
        GgufKv::array_u32("skippy.package.tensor_alignment", vec![32]),
    ];
    let mut data = b"GGUF".to_vec();
    data.extend(3_u32.to_le_bytes());
    data.extend(1_u64.to_le_bytes());
    data.extend((fields.len() as u64).to_le_bytes());
    for field in fields {
        write_kv(&mut data, &field).unwrap();
    }
    let tensor = b"blk.0.weight";
    data.extend((tensor.len() as u64).to_le_bytes());
    data.extend(tensor);
    data.extend(1_u32.to_le_bytes()); // rank
    data.extend(8_u64.to_le_bytes()); // elements
    data.extend(0_u32.to_le_bytes()); // F32
    data.extend(0_u64.to_le_bytes()); // carrier offset
    data.resize(data.len().next_multiple_of(32), 0);
    data
}

fn cache_snapshot(cache: &Path, commit: &str, files: &[(&str, &[u8])]) -> PathBuf {
    let repository = cache.join("models--test--package");
    let snapshot = repository.join("snapshots").join(commit);
    fs::create_dir_all(&snapshot).unwrap();
    fs::create_dir_all(repository.join("refs")).unwrap();
    fs::write(repository.join("refs/main"), commit).unwrap();
    for (name, bytes) in files {
        fs::write(snapshot.join(name), bytes).unwrap();
    }
    snapshot
}

fn no_client(cache: &Path) -> PackageAcquisition {
    PackageAcquisition::new(cache.into(), || {
        panic!("cache-only resolution constructed a client")
    })
}

#[test]
fn stale_cache_refreshes_to_verified_v2_and_pins_later_stage_acquisition() {
    let cache = tempfile::tempdir().unwrap();
    let stale = cache_snapshot(
        cache.path(),
        OLD_COMMIT,
        &[(MANIFEST, br#"{"schema_version":1}"#)],
    );
    let (manifest, files) = package_fixture();
    let server = PackageServer::new(files);
    let acquisition = acquisition(cache.path(), &server.endpoint);
    let resolved = acquisition
        .resolve_and_inspect(REFERENCE, AllowUpstream)
        .unwrap();
    assert_eq!(resolved.source(), PackageResolutionSource::Upstream);
    assert_eq!(
        resolved.identity().package_ref,
        format!("{REFERENCE}@{COMMIT}")
    );
    assert_eq!(resolved.identity().activation_width, 8);
    assert!(!resolved.snapshot().join(LAYER).exists());
    assert!(stale.join(MANIFEST).is_file());

    cache_snapshot(cache.path(), OLD_COMMIT, &[]);
    let admission = PackageV2StageAdmissionDescriptor {
        package_id: manifest.package_id,
        resident_tensor_ids: vec!["blk.0.weight".into()],
        sidecars: Vec::new(),
    };
    let (path, files, _) = resolved.acquire_stage(&acquisition, &admission).unwrap();
    assert_eq!(Path::new(&path), resolved.snapshot());
    assert_eq!(
        files,
        [
            resolved.snapshot().join(METADATA),
            resolved.snapshot().join(LAYER)
        ]
    );
    assert_eq!(fs::read(resolved.snapshot().join(LAYER)).unwrap(), [7; 32]);
    assert!(!stale.join(LAYER).exists());
}

#[test]
fn unsupported_upstream_stays_typed_and_pins_never_refresh() {
    let cache = tempfile::tempdir().unwrap();
    cache_snapshot(
        cache.path(),
        OLD_COMMIT,
        &[(MANIFEST, br#"{"schema_version":1}"#)],
    );
    let server = PackageServer::new(BTreeMap::from([(
        MANIFEST.into(),
        br#"{"schema_version":1}"#.to_vec(),
    )]));
    let acquisition = acquisition(cache.path(), &server.endpoint);
    let offline = no_client(cache.path())
        .resolve_and_inspect(REFERENCE, CacheOnly)
        .unwrap_err();
    assert_eq!(
        offline
            .downcast_ref::<UnsupportedPackageSchema>()
            .unwrap()
            .source,
        PackageResolutionSource::Cache
    );
    let pinned = no_client(cache.path())
        .resolve_and_inspect(&format!("{REFERENCE}@{OLD_COMMIT}"), AllowUpstream)
        .unwrap_err();
    assert_eq!(
        pinned
            .downcast_ref::<UnsupportedPackageSchema>()
            .unwrap()
            .source,
        PackageResolutionSource::Cache
    );
    let upstream = acquisition
        .resolve_and_inspect(REFERENCE, AllowUpstream)
        .unwrap_err();
    assert_eq!(
        upstream
            .downcast_ref::<UnsupportedPackageSchema>()
            .unwrap()
            .source,
        PackageResolutionSource::Upstream
    );
}

#[test]
fn verified_cache_constructs_no_client_and_integrity_failures_never_refresh() {
    let cache = tempfile::tempdir().unwrap();
    let (_, files) = package_fixture();
    let snapshot = cache_snapshot(
        cache.path(),
        COMMIT,
        &[(MANIFEST, &files[MANIFEST]), (METADATA, &files[METADATA])],
    );
    let calls = Arc::new(AtomicUsize::new(0));
    let client_calls = calls.clone();
    let acquisition = PackageAcquisition::new(cache.path().into(), move || {
        client_calls.fetch_add(1, Ordering::Relaxed);
        anyhow::bail!("unexpected client construction")
    });
    for policy in [CacheOnly, AllowUpstream] {
        let resolved = acquisition.resolve_and_inspect(REFERENCE, policy).unwrap();
        assert_eq!(resolved.source(), PackageResolutionSource::Cache);
        assert_eq!(
            resolved.identity().package_ref,
            format!("{REFERENCE}@{COMMIT}")
        );
    }
    assert!(!snapshot.join(LAYER).exists());
    fs::write(snapshot.join(METADATA), vec![0; files[METADATA].len()]).unwrap();
    let error = acquisition
        .resolve_and_inspect(REFERENCE, AllowUpstream)
        .unwrap_err();
    assert!(format!("{error:#}").contains("SHA-256 differs"));
    fs::write(snapshot.join(MANIFEST), b"{}").unwrap();
    let error = acquisition
        .resolve_and_inspect(REFERENCE, AllowUpstream)
        .unwrap_err();
    assert!(format!("{error:#}").contains("missing integer schema_version"));
    assert_eq!(calls.load(Ordering::Relaxed), 0);
}
