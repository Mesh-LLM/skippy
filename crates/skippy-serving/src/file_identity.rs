//! A strong file identity on Windows, shared by the model content proof and
//! `skippy-api`'s source registry.
//!
//! std exposes no device, inode or change time on Windows, so they are read
//! from an open handle: the volume serial number and 128-bit file ID
//! (`FileIdInfo`, which also covers ReFS) identify the file, and `ChangeTime`
//! advances on any content or metadata change, which is what lets a same-size
//! rewrite with a restored last-write time be told apart. Unlike Unix ctime, a
//! process with write-attributes access can set `ChangeTime`; the stamp gates
//! reuse of a verified hash and is not a defence against a local writer.

/// Whether a filesystem keeps the change time a strong stamp relies on. FAT
/// and exFAT do not, so a file there gets no strong identity and its content
/// is rehashed instead.
pub fn filesystem_keeps_change_time(filesystem: &str) -> bool {
    filesystem.eq_ignore_ascii_case("NTFS") || filesystem.eq_ignore_ascii_case("ReFS")
}

/// The volume serial number, 128-bit file ID and `ChangeTime` of `file`, or
/// `None` when its filesystem does not keep a change time or the handle
/// cannot report them.
#[cfg(windows)]
pub fn windows_strong_identity(file: &std::fs::File) -> Option<(u64, u128, i64)> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_BASIC_INFO, FILE_ID_INFO, FileBasicInfo, FileIdInfo, GetFileInformationByHandleEx,
        GetVolumeInformationByHandleW,
    };

    let handle = file.as_raw_handle();
    let mut filesystem = [0_u16; 32];
    // SAFETY: `handle` belongs to `file`, which outlives the call; only the
    // filesystem name is requested, into a buffer passed with its length.
    let named = unsafe {
        GetVolumeInformationByHandleW(
            handle,
            std::ptr::null_mut(),
            0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            filesystem.as_mut_ptr(),
            filesystem.len() as u32,
        ) != 0
    };
    let length = filesystem.iter().position(|&c| c == 0).unwrap_or(0);
    if !named || !filesystem_keeps_change_time(&String::from_utf16_lossy(&filesystem[..length])) {
        return None;
    }
    let mut id = FILE_ID_INFO::default();
    let mut basic = FILE_BASIC_INFO::default();
    // SAFETY: as above, and each buffer is the structure its information class
    // documents, passed with that structure's exact size.
    let queried = unsafe {
        GetFileInformationByHandleEx(
            handle,
            FileIdInfo,
            (&raw mut id).cast(),
            size_of::<FILE_ID_INFO>() as u32,
        ) != 0
            && GetFileInformationByHandleEx(
                handle,
                FileBasicInfo,
                (&raw mut basic).cast(),
                size_of::<FILE_BASIC_INFO>() as u32,
            ) != 0
    };
    (queried && basic.ChangeTime != 0).then(|| {
        (
            id.VolumeSerialNumber,
            u128::from_le_bytes(id.FileId.Identifier),
            basic.ChangeTime,
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_filesystems_that_keep_a_change_time_vouch_for_a_stamp() {
        for keeps in ["NTFS", "ReFS", "ntfs"] {
            assert!(filesystem_keeps_change_time(keeps), "{keeps}");
        }
        for loses in ["exFAT", "FAT32", "FAT", "UDF", ""] {
            assert!(!filesystem_keeps_change_time(loses), "{loses}");
        }
    }

    #[cfg(windows)]
    #[test]
    fn a_file_on_the_system_volume_has_a_strong_identity() {
        let file = tempfile::NamedTempFile::new().unwrap();
        assert!(windows_strong_identity(file.as_file()).is_some());
    }
}
