use super::DynResult;
use crate::artifact;
use std::path::{Path, PathBuf};

#[cfg(any(target_os = "macos", test))]
const MAGIC: u64 = 0x534b_4950_5059_4d32;

pub(in crate::cache_parity::capture) struct Probe {
    _directory: tempfile::TempDir,
    socket: PathBuf,
    library: PathBuf,
    pub(super) sha256: String,
}

impl Probe {
    pub(in crate::cache_parity::capture) fn start(path: &Path) -> DynResult<Self> {
        if !cfg!(all(target_os = "macos", target_arch = "aarch64")) {
            return Err("Metal memory sampling requires macOS arm64".into());
        }
        let library = path.canonicalize()?;
        if !path.is_absolute() || !library.is_file() {
            return Err("Metal memory probe must be an absolute path to a built library".into());
        }
        let sha256 = artifact::sha256(&library)?;
        // The private directory also keeps the macOS Unix socket path below
        // the sun_path limit, regardless of the artifact checkout path.
        let directory = tempfile::Builder::new()
            .prefix("skippy-metal.")
            .tempdir_in("/tmp")?;
        Ok(Self {
            socket: directory.path().join("sample.sock"),
            _directory: directory,
            library,
            sha256,
        })
    }

    pub(in crate::cache_parity::capture) fn environment(
        &self,
    ) -> DynResult<[(&'static str, String); 2]> {
        Ok([
            (
                "DYLD_INSERT_LIBRARIES",
                self.library
                    .to_str()
                    .ok_or("invalid Metal probe path")?
                    .into(),
            ),
            (
                "SKIPPY_METAL_MEMORY_SOCKET",
                self.socket
                    .to_str()
                    .ok_or("invalid Metal sample socket")?
                    .into(),
            ),
        ])
    }

    #[cfg(target_os = "macos")]
    pub(super) fn allocated(&self, pid: u32) -> DynResult<u64> {
        use std::{
            io::{Read, Write},
            os::unix::net::UnixStream,
            time::Duration,
        };

        let mut stream = UnixStream::connect(&self.socket).map_err(|error| {
            format!("Metal probe for process {pid} did not accept a sample: {error}")
        })?;
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .map_err(|error| {
                format!("could not configure Metal probe timeout for process {pid}: {error}")
            })?;
        stream.write_all(b"?").map_err(|error| {
            format!("could not request Metal memory sample for process {pid}: {error}")
        })?;
        let mut reply = [0u8; 24];
        stream.read_exact(&mut reply).map_err(|error| {
            format!("could not read Metal memory sample for process {pid}: {error}")
        })?;
        parse_reply(&reply, pid)
    }

    #[cfg(not(target_os = "macos"))]
    pub(super) fn allocated(&self, _: u32) -> DynResult<u64> {
        Err("Metal memory sampling requires macOS arm64".into())
    }
}

#[cfg(any(target_os = "macos", test))]
fn parse_reply(reply: &[u8; 24], pid: u32) -> DynResult<u64> {
    let word = |index: usize| u64::from_le_bytes(reply[index..index + 8].try_into().unwrap());
    if word(0) != MAGIC || word(8) != u64::from(pid) || word(16) == 0 {
        return Err(format!(
            "Metal probe did not report positive allocated bytes for process {pid}"
        )
        .into());
    }
    Ok(word(16))
}

#[test]
fn reply_must_identify_the_measured_process_and_nonzero_bytes() {
    let reply = |pid: u64, bytes: u64| {
        let mut result = [0_u8; 24];
        result[0..8].copy_from_slice(&MAGIC.to_le_bytes());
        result[8..16].copy_from_slice(&pid.to_le_bytes());
        result[16..24].copy_from_slice(&bytes.to_le_bytes());
        result
    };
    assert_eq!(parse_reply(&reply(52, 1 << 26), 52).unwrap(), 1 << 26);
    assert!(parse_reply(&reply(53, 1 << 26), 52).is_err());
    assert!(parse_reply(&reply(52, 0), 52).is_err());
}
