use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use std::env;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub(super) enum Backend {
    #[serde(rename = "macos-arm64-metal")]
    Metal,
    #[serde(rename = "linux-x86_64-cuda")]
    Cuda,
    #[serde(rename = "linux-aarch64-cuda")]
    CudaArm64,
}

impl Backend {
    pub(super) fn selected() -> Result<Self> {
        match env::var("SKIPPY_CACHE_QUAL_BACKEND") {
            Ok(value) if value == "metal" => Ok(Self::Metal),
            Ok(value) if value == "cuda" => Ok(Self::Cuda),
            Ok(value) if value == "cuda-arm64" => Ok(Self::CudaArm64),
            Ok(value) => bail!("unknown qualification backend {value:?}"),
            Err(env::VarError::NotPresent) => Ok(Self::Metal),
            Err(error) => Err(error.into()),
        }
    }

    pub(super) fn require_host(self) -> Result<()> {
        let supported = match self {
            Self::Metal => cfg!(all(target_os = "macos", target_arch = "aarch64")),
            Self::Cuda => cfg!(all(target_os = "linux", target_arch = "x86_64")),
            Self::CudaArm64 => cfg!(all(target_os = "linux", target_arch = "aarch64")),
        };
        ensure!(
            supported,
            "host does not match the {self:?} qualification profile"
        );
        Ok(())
    }

    pub(super) fn offloaded_layers(self, log: &str) -> Result<usize> {
        ensure!(
            log.lines().any(|line| {
                line.split_ascii_whitespace()
                    .any(|word| self.is_device_buffer(word))
                    && (line.contains("KV buffer") || line.contains("compute buffer"))
            }),
            "no {self:?} device buffer allocation was observed"
        );
        log.lines()
            .filter_map(|line| {
                let (_, count) = line.split_once("offloaded ")?;
                let (count, _) = count.split_once('/')?;
                line.contains("layers to GPU")
                    .then(|| count.parse::<usize>().ok())
                    .flatten()
            })
            .find(|&count| count > 0)
            .context("no actual GPU layer offload was observed")
    }

    fn is_device_buffer(self, word: &str) -> bool {
        let prefix = match self {
            Self::Metal if word == "Metal" => return true,
            Self::Metal => "MTL",
            Self::Cuda | Self::CudaArm64 => "CUDA",
        };
        word.strip_prefix(prefix).is_some_and(|index| {
            !index.is_empty() && index.bytes().all(|byte| byte.is_ascii_digit())
        })
    }
}

#[test]
fn backend_evidence_requires_matching_device_buffers_and_positive_offload() {
    let metal = "sched_reserve: MTL0 compute buffer size = 256.00 MiB";
    let cuda = "sched_reserve: CUDA0 compute buffer size = 256.00 MiB";
    let pinned_host = "sched_reserve: CUDA_Host compute buffer size = 256.00 MiB";
    let offload = "load_tensors: offloaded 37/37 layers to GPU";
    assert_eq!(
        Backend::Metal
            .offloaded_layers(&format!("{metal}\n{offload}"))
            .unwrap(),
        37
    );
    assert_eq!(
        Backend::Cuda
            .offloaded_layers(&format!("{cuda}\n{offload}"))
            .unwrap(),
        37
    );
    assert!(
        Backend::Cuda
            .offloaded_layers(&format!("{pinned_host}\n{offload}"))
            .is_err()
    );
    assert!(
        Backend::Metal
            .offloaded_layers(&format!("{cuda}\n{offload}"))
            .is_err()
    );
    assert!(Backend::Metal.offloaded_layers(metal).is_err());
    assert_eq!(
        Backend::CudaArm64
            .offloaded_layers(&format!("{cuda}\n{offload}"))
            .unwrap(),
        37
    );
}
