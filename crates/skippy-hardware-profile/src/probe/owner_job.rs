//! Windows: a probe never outlives the process that started it.
//!
//! process-wrap's job object lets a survey kill its tool, but it creates that
//! job without `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, so a probe still running
//! when this process exits outlives it. It then keeps every inheritable handle
//! it got from this process, including the stdout and stderr pipes of whoever
//! started it, and that caller waits for end of file until the probe ends. So
//! every probe also joins one process-wide job that kills its members when its
//! last handle closes, which Windows does when this process exits, however it
//! exits.

use process_wrap::std::ChildWrapper;
use std::io;
use std::os::windows::io::AsRawHandle;
use std::sync::OnceLock;
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
    SetInformationJobObject,
};

/// Put a just-started probe in the job that dies with this process.
pub(super) fn adopt(child: &dyn ChildWrapper) -> io::Result<()> {
    static JOB: OnceLock<Result<usize, i32>> = OnceLock::new();
    let job = *JOB
        .get_or_init(create)
        .as_ref()
        .map_err(|&code| io::Error::from_raw_os_error(code))?;
    let process = child
        .process_handle()
        .ok_or_else(|| io::Error::new(io::ErrorKind::Unsupported, "probe has no process handle"))?;
    // SAFETY: `job` is a job handle this process keeps open until it exits,
    // and `process` is borrowed from the live child for the call.
    if unsafe { AssignProcessToJobObject(job as HANDLE, process.as_raw_handle()) } == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn create() -> Result<usize, i32> {
    let os_error = || io::Error::last_os_error().raw_os_error().unwrap_or(0);
    // SAFETY: no security attributes and no name ask for a new anonymous job.
    let job = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if job.is_null() {
        return Err(os_error());
    }
    // SAFETY: an all-zero JOBOBJECT_EXTENDED_LIMIT_INFORMATION sets no limit.
    let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    // SAFETY: `info` is the structure this information class documents, passed
    // with its exact size.
    let set = unsafe {
        SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            (&raw const info).cast(),
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
    };
    if set == 0 {
        let code = os_error();
        // SAFETY: `job` was created above and is not shared yet.
        unsafe { CloseHandle(job) };
        return Err(code);
    }
    // Never closed on purpose: closing the last handle is what kills the
    // probes, and that must only happen when this process exits.
    Ok(job as usize)
}
