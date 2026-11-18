use anyhow::{Result, ensure};
use serde::Serialize;
use skippy_cache::exact_state_identity_for_stage;
use skippy_protocol::{SplitMode, StageConfig};
use skippy_runtime::{BackendDevice, BackendDeviceType};

/// The native runtime can choose devices automatically or split a stage
/// between devices. Keep registry order because the native placement can use
/// that order. Free memory and other transient counters are deliberately
/// excluded so an otherwise identical replacement process can restore.
#[derive(Serialize)]
struct DeviceIdentity<'a> {
    name: &'a str,
    description: Option<&'a str>,
    device_id: Option<&'a str>,
    kind: &'static str,
}

impl<'a> From<&'a BackendDevice> for DeviceIdentity<'a> {
    fn from(device: &'a BackendDevice) -> Self {
        Self {
            name: &device.name,
            description: device.description.as_deref(),
            device_id: device.device_id.as_deref(),
            kind: match device.device_type {
                BackendDeviceType::Cpu => "cpu",
                BackendDeviceType::Gpu => "gpu",
                BackendDeviceType::IntegratedGpu => "integrated_gpu",
                BackendDeviceType::Accelerator => "accelerator",
                BackendDeviceType::Meta => "meta",
            },
        }
    }
}

#[derive(Serialize)]
struct RuntimePlacement<'a> {
    os: &'static str,
    native_abi: [u32; 3],
    devices: Vec<DeviceIdentity<'a>>,
    main_gpu: Option<u32>,
    split_mode: SplitMode,
    kv_offload: Option<bool>,
    op_offload: Option<bool>,
    no_host_buffer: bool,
}

pub(super) fn read(config: &StageConfig, payload: &str) -> Result<String> {
    let native_abi = [
        skippy_runtime::ABI_VERSION_MAJOR,
        skippy_runtime::ABI_VERSION_MINOR,
        skippy_runtime::ABI_VERSION_PATCH,
    ];
    let loaded = skippy_runtime::native_runtime_loaded();
    #[cfg(test)]
    if !loaded {
        // Modelless tests can pass synthetic cache capabilities with no FFI.
        return state_identity(config, payload, native_abi, &[]);
    }
    ensure!(loaded, "no native runtime device inventory");
    let devices = skippy_runtime::backend_devices()?;
    ensure!(
        !devices.is_empty() && devices.iter().all(|device| !device.name.is_empty()),
        "native runtime reported an incomplete device inventory"
    );
    state_identity(config, payload, native_abi, &devices)
}

fn state_identity(
    config: &StageConfig,
    payload: &str,
    native_abi: [u32; 3],
    devices: &[BackendDevice],
) -> Result<String> {
    let placement = RuntimePlacement {
        os: std::env::consts::OS,
        native_abi,
        devices: devices.iter().map(DeviceIdentity::from).collect(),
        main_gpu: config.main_gpu,
        split_mode: config.split_mode,
        kv_offload: config.kv_offload,
        op_offload: config.op_offload,
        no_host_buffer: config.no_host_buffer,
    };
    let mut hash = blake3::Hasher::new();
    hash.update(b"skippy-durable-placement-v1");
    hash.update(exact_state_identity_for_stage(config, payload).as_bytes());
    hash.update(&serde_json::to_vec(&placement)?);
    Ok(format!("blake3:{}", hash.finalize().to_hex()))
}

#[cfg(test)]
mod tests;
