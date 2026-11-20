use std::{net::SocketAddr, path::Path, time::Duration};

use anyhow::{Result, ensure};
use skippy_api::{
    native_runtime::ProductionRuntime,
    serving::{InferenceOptions, ModelLoadRequest, in_process::LoadedModel, readiness},
    source, split_certification,
    stage_admission::{StagePlannerProfile, realize_direct_gguf_stage_admissions},
    stage_load::{AdmittedStageOptions, admitted_resident_tensor_names, admitted_stage_config},
};
use skippy_production_consumer::{
    LoadEvidence, MODEL_ID, exercise_streams, open_runtime, required,
};
use skippy_protocol::{LoadMode, PeerConfig, StageAdmissionDescriptor, StageConfig};
use skippy_serving::{
    EmbeddedRuntimeOptions,
    binary_transport::{BinaryStageOptions, WireCondition},
    settings::ServingTuning,
    start_binary_stage,
    telemetry::TelemetryLevel,
};

#[tokio::main]
async fn main() -> Result<()> {
    let (runtime, model) = open_runtime()?;
    let entry_endpoint: SocketAddr = required("SKIPPY_FIXTURE_ENTRY")?.parse()?;
    let worker_endpoint: SocketAddr = required("SKIPPY_FIXTURE_WORKER")?.parse()?;
    ensure!(
        entry_endpoint.ip().is_loopback()
            && worker_endpoint.ip().is_loopback()
            && entry_endpoint.port() != 0
            && worker_endpoint.port() != 0
            && entry_endpoint != worker_endpoint,
        "fixture requires two distinct explicit loopback endpoints"
    );
    let (entry, worker) =
        tokio::task::spawn_blocking(move || plan(&model, entry_endpoint, worker_endpoint))
            .await??;
    let worker = start_binary_stage(worker_options(worker, worker_endpoint)?);
    let outcome = async {
        readiness::start_binary_stage_ready_probe(worker_endpoint, Duration::from_secs(120))
            .wait()
            .await?;
        let evidence = LoadEvidence::default();
        let observations = evidence.observe();
        let model = tokio::task::spawn_blocking(move || load_entry(&runtime, entry, observations))
            .await??;
        let result = async {
            evidence.verify()?;
            exercise_streams(&model).await
        }
        .await;
        tokio::task::spawn_blocking(move || model.shutdown()).await?;
        result
    }
    .await;
    let shutdown = worker.shutdown().await;
    outcome.and(shutdown)
}

fn plan(path: &Path, entry: SocketAddr, worker: SocketAddr) -> Result<(StageConfig, StageConfig)> {
    let identity = source::synthetic_direct_gguf_package(MODEL_ID, path, None)?;
    let (manifest, _) =
        source::planning::direct_gguf_planning_manifest_from_identity(MODEL_ID, &identity)?;
    let architecture = manifest.model_metadata["general.architecture"]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("missing model architecture"))?;
    split_certification::require_split_certification(&identity, architecture, false)?;
    ensure!(
        identity.layer_count >= 2,
        "fixture model must have at least two layers"
    );
    let middle = identity.layer_count / 2;
    let profiles = [
        StagePlannerProfile {
            profile_id: "decode".into(),
            n_tokens: 1,
            n_sequences: 1,
            n_outputs: 1,
            n_recurrent_rollback_sequences: 0,
        },
        StagePlannerProfile {
            profile_id: "prefill".into(),
            n_tokens: 8,
            n_sequences: 1,
            n_outputs: 8,
            n_recurrent_rollback_sequences: 0,
        },
    ];
    let descriptors = realize_direct_gguf_stage_admissions(
        MODEL_ID,
        &identity,
        &[(0, middle), (middle, identity.layer_count)],
        &profiles,
        "skippy-graph-configuration:v1:production-fixture",
        "skippy-backend:cpu:v1",
    )?;
    let [first, second]: [StageAdmissionDescriptor; 2] = descriptors
        .try_into()
        .map_err(|_| anyhow::anyhow!("expected exactly two admitted stages"))?;
    println!(
        "stage_package={} admission_version={} entry_plan={} worker_plan={}",
        first.package_id, first.version, first.plan_id, second.plan_id,
    );
    let mut entry_options = options(&identity, first, 0, entry)?;
    entry_options.downstream = Some(peer(1, worker));
    let mut worker_options = options(&identity, second, 1, worker)?;
    worker_options.upstream = Some(peer(0, entry));

    let first_names = admitted_resident_tensor_names(&entry_options.admission, &manifest)?;
    let second_names = admitted_resident_tensor_names(&worker_options.admission, &manifest)?;
    let mut incompatible = worker_options.clone();
    incompatible.admission.version += 1;
    let error = admitted_stage_config(&incompatible, None, second_names.clone()).unwrap_err();
    ensure!(
        format!("{error:#}").contains("unsupported descriptor version"),
        "unexpected admission failure: {error:#}"
    );
    Ok((
        admitted_stage_config(&entry_options, None, first_names)?,
        admitted_stage_config(&worker_options, None, second_names)?,
    ))
}

fn peer(index: u32, endpoint: SocketAddr) -> PeerConfig {
    PeerConfig {
        persistent_lane_marker: false,
        stage_id: format!("stage-{index}"),
        stage_index: index,
        endpoint: endpoint.to_string(),
    }
}

fn options(
    identity: &skippy_api::package::SkippyPackageIdentity,
    admission: StageAdmissionDescriptor,
    index: u32,
    endpoint: SocketAddr,
) -> Result<AdmittedStageOptions> {
    Ok(AdmittedStageOptions {
        topology_id: "embedding-topology".into(),
        run_id: "embedding-split".into(),
        model_id: MODEL_ID.into(),
        stage_id: format!("stage-{index}"),
        layer_start: admission.layer_start,
        layer_end: admission.layer_end,
        ctx_size: 512,
        lane_count: 1,
        selected_device: None,
        package_ref: identity.package_ref.clone(),
        manifest_sha256: identity.manifest_sha256.clone(),
        model_path: Some(
            identity
                .source_model_path
                .to_str()
                .ok_or_else(|| anyhow::anyhow!("model path is not UTF-8"))?
                .to_owned(),
        ),
        source_model_sha256: Some(identity.source_model_sha256.clone()),
        source_model_bytes: Some(identity.source_model_bytes),
        projector_path: None,
        projector_use_gpu: None,
        media_marker: None,
        image_min_tokens: None,
        image_max_tokens: None,
        batch_max_tokens: None,
        glm_dsa_policy: Default::default(),
        generation_signal_window: None,
        activation_codec: Default::default(),
        activation_codec_policy: Default::default(),
        stage_index: index,
        n_batch: Some(8),
        n_ubatch: Some(8),
        n_gpu_layers: 0,
        mmap: None,
        mlock: false,
        runtime_settings: Default::default(),
        cache_type_k: "f16".into(),
        cache_type_v: "f16".into(),
        flash_attn_type: Default::default(),
        load_mode: LoadMode::RuntimeSlice,
        native_mtp_enabled: false,
        bind_addr: endpoint.to_string(),
        upstream: None,
        downstream: None,
        admission,
    })
}

fn worker_options(config: StageConfig, bind_addr: SocketAddr) -> Result<BinaryStageOptions> {
    Ok(BinaryStageOptions {
        tuning: ServingTuning::default(),
        config,
        topology: None,
        bind_addr,
        metrics_otlp_grpc: None,
        telemetry_queue_capacity: 0,
        telemetry_level: TelemetryLevel::Off,
        max_inflight: 1,
        reply_credit_limit: None,
        async_prefill_forward: false,
        downstream_wire_condition: WireCondition::new(0.0, None)?,
        downstream_connect_timeout_secs: 10,
        native_mtp_enabled: false,
        last_stage_decode_batch: None,
        continuous_batching: false,
        openai: None,
        compute_meter: None,
        l3_manager: None,
        split_dflash: None,
    })
}

fn load_entry(
    runtime: &ProductionRuntime,
    config: StageConfig,
    observations: skippy_api::serving::ModelLoadObservations,
) -> Result<LoadedModel> {
    let openai = InferenceOptions::embedded_stage_defaults(Some(MODEL_ID.into()), 128, 1, 0, false);
    let options = EmbeddedRuntimeOptions {
        config,
        topology: None,
        n_threads: None,
        n_threads_batch: None,
        mtp_source: Default::default(),
        recurrent_rollback_tokens: 0,
        metrics_otlp_grpc: None,
        telemetry_queue_capacity: 0,
        telemetry_level: TelemetryLevel::Off,
        session_lifecycle_observer: None,
    };
    LoadedModel::load_prepared(
        runtime,
        ModelLoadRequest::new(options, openai),
        observations,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn incompatible_stage_generation_is_rejected_offline() {
        let identity = skippy_api::package::SkippyPackageIdentity {
            package_ref: "fixture.gguf".into(),
            manifest_sha256: "0".repeat(64),
            source_model_path: "fixture.gguf".into(),
            source_model_sha256: "0".repeat(64),
            source_model_bytes: 1,
            source_files: Vec::new(),
            layer_weight_bytes: Vec::new(),
            layer_count: 2,
            activation_width: 1,
            tensor_count: 1,
            generation: None,
            publisher_defaults: None,
        };
        let admission = StageAdmissionDescriptor {
            version: skippy_protocol::STAGE_ADMISSION_DESCRIPTOR_VERSION + 1,
            package_id: format!("sha256:{}", "0".repeat(64)),
            plan_id: format!("skippy-plan:v1:{}", "0".repeat(64)),
            execution_contract: String::new(),
            layer_start: 1,
            layer_end: 2,
            resident_tensor_ids: vec!["blk.1.weight".into()],
            sidecars: Vec::new(),
            profiles: Vec::new(),
            kv_graph_state: String::new(),
        };
        let options = options(&identity, admission, 1, "127.0.0.1:19332".parse().unwrap()).unwrap();
        let error = admitted_stage_config(&options, None, vec!["blk.1.weight".into()]).unwrap_err();
        assert!(format!("{error:#}").contains("unsupported descriptor version"));
    }
}
