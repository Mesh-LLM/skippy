use super::DynResult;
use crate::{command::write_json_file, execution};
use serde::{Deserialize, Serialize};
use std::{
    path::Path,
    sync::mpsc,
    time::{Duration, Instant},
};

const SAMPLE_PERIOD: Duration = Duration::from_millis(250);

pub(super) mod metal;

#[derive(Clone, Copy, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Profile {
    Host,
    Cuda,
    Metal,
}

#[derive(Serialize)]
struct Sample {
    offset_ns: u64,
    host_bytes: u64,
    device_bytes: Option<u64>,
}

#[derive(Serialize)]
struct Record<'a> {
    schema_version: u32,
    profile: Profile,
    pid: u32,
    duration_ns: u64,
    sample_period_ms: u64,
    probe_sha256: Option<&'a str>,
    samples: Vec<Sample>,
}

pub(super) struct Monitor<'a> {
    pub(super) profile: Option<Profile>,
    pub(super) pid: u32,
    pub(super) metal: Option<&'a metal::Probe>,
}

impl Monitor<'_> {
    pub(super) fn record<T>(
        &self,
        output: &Path,
        work: impl FnOnce() -> DynResult<T>,
    ) -> DynResult<T> {
        self.record_window(output, |_| work())
    }

    pub(super) fn record_window<T>(
        &self,
        output: &Path,
        work: impl FnOnce(Instant) -> DynResult<T>,
    ) -> DynResult<T> {
        let Some(profile) = self.profile else {
            return work(Instant::now());
        };
        let sample = |offset| take_sample(profile, self.pid, offset, self.metal);
        let first = sample(0)?;
        let started = Instant::now();
        let (stop, interrupted) = mpsc::sync_channel::<()>(1);
        let (result, duration_ns, samples) = std::thread::scope(|scope| -> DynResult<_> {
            let worker = scope.spawn(move || -> Result<_, String> {
                let mut samples = vec![first];
                while let Err(mpsc::RecvTimeoutError::Timeout) =
                    interrupted.recv_timeout(SAMPLE_PERIOD)
                {
                    let mut observed = sample(0).map_err(|error| error.to_string())?;
                    observed.offset_ns = nanos_since(started).map_err(|error| error.to_string())?;
                    samples.push(observed);
                }
                Ok(samples)
            });
            let result = work(started);
            let duration_ns = nanos_since(started);
            drop(stop);
            let samples = worker
                .join()
                .map_err(|_| "memory sampler thread panicked")??;
            Ok((result, duration_ns?, samples))
        })?;
        let mut samples = samples;
        if duration_ns == 0 {
            return Err("memory sampling window was empty".into());
        }
        samples.retain(|sample| sample.offset_ns < duration_ns);
        samples.push(sample(duration_ns)?);
        write_json_file(
            output,
            &Record {
                schema_version: 1,
                profile,
                pid: self.pid,
                duration_ns,
                sample_period_ms: u64::try_from(SAMPLE_PERIOD.as_millis())?,
                probe_sha256: self.metal.map(|probe| probe.sha256.as_str()),
                samples,
            },
        )?;
        result
    }
}

fn nanos_since(started: Instant) -> DynResult<u64> {
    Ok(started.elapsed().as_nanos().try_into()?)
}

fn take_sample(
    profile: Profile,
    pid: u32,
    offset_ns: u64,
    metal: Option<&metal::Probe>,
) -> DynResult<Sample> {
    let host_bytes = host_resident_bytes(pid)?;
    if host_bytes == 0 {
        return Err("process resident memory counter is zero".into());
    }
    let device_bytes = match profile {
        Profile::Host => None,
        Profile::Metal => Some(metal.ok_or("missing Metal memory probe")?.allocated(pid)?),
        Profile::Cuda => {
            if !cfg!(target_os = "linux") {
                return Err("CUDA memory sampling requires Linux".into());
            }
            let output = execution::capture_for(
                "nvidia-smi",
                &execution::strings(&[
                    "--query-compute-apps=pid,used_gpu_memory",
                    "--format=csv,noheader,nounits",
                ]),
                &[],
                3,
            )?;
            Some(cuda_process_bytes(&output, pid)?)
        }
    };
    Ok(Sample {
        offset_ns,
        host_bytes,
        device_bytes,
    })
}

#[cfg(target_os = "macos")]
fn host_resident_bytes(pid: u32) -> DynResult<u64> {
    let pid = i32::try_from(pid)?;
    let mut info = std::mem::MaybeUninit::<libc::proc_taskinfo>::uninit();
    let size = i32::try_from(std::mem::size_of::<libc::proc_taskinfo>())?;
    // PROC_PIDTASKINFO writes the named ABI structure. Only assume its
    // initialization when libproc reports that it wrote the full structure.
    let written = unsafe {
        libc::proc_pidinfo(
            pid,
            libc::PROC_PIDTASKINFO,
            0,
            info.as_mut_ptr().cast(),
            size,
        )
    };
    if written != size {
        return Err(format!(
            "could not sample process {pid} resident memory: {}",
            std::io::Error::last_os_error()
        )
        .into());
    }
    Ok(unsafe { info.assume_init() }.pti_resident_size)
}

#[cfg(target_os = "linux")]
fn host_resident_bytes(pid: u32) -> DynResult<u64> {
    let status = std::fs::read_to_string(format!("/proc/{pid}/status"))?;
    let rss = status
        .lines()
        .find_map(|line| line.strip_prefix("VmRSS:"))
        .ok_or("process status did not report VmRSS")?;
    let fields: Vec<_> = rss.split_whitespace().collect();
    let [kilobytes, "kB"] = fields.as_slice() else {
        return Err("invalid VmRSS measurement".into());
    };
    kilobytes
        .parse::<u64>()?
        .checked_mul(1024)
        .ok_or_else(|| "VmRSS exceeds the supported range".into())
}

#[cfg(not(any(target_os = "macos", target_os = "linux")))]
fn host_resident_bytes(_: u32) -> DynResult<u64> {
    Err("process resident memory sampling requires macOS or Linux".into())
}

fn cuda_process_bytes(output: &str, pid: u32) -> DynResult<u64> {
    let mut found = 0_u64;
    for line in output.lines().filter(|line| !line.trim().is_empty()) {
        let (target, memory) = line
            .split_once(',')
            .ok_or("invalid CUDA process memory output")?;
        if target.trim().parse::<u32>()? == pid {
            found = found
                .checked_add(memory.trim().parse::<u64>()?)
                .ok_or("CUDA process memory exceeds the supported range")?;
        }
    }
    if found == 0 {
        return Err(format!("no positive CUDA device memory sample for process {pid}").into());
    }
    found
        .checked_mul(1024 * 1024)
        .ok_or_else(|| "CUDA process memory exceeds the supported range".into())
}

#[cfg(test)]
#[test]
fn cuda_memory_counts_only_the_owned_pid_across_devices() {
    assert_eq!(
        cuda_process_bytes("17, 42\n12, 128\n17, 5\n", 17).unwrap(),
        47 * 1024 * 1024
    );
    assert!(cuda_process_bytes("12, 128\n", 17).is_err());
    assert!(cuda_process_bytes("17, [Not Supported]\n", 17).is_err());
}
