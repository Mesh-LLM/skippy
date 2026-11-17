use super::options::Options;
use crate::command::DynResult;
use std::path::{Path, PathBuf};
pub(super) enum Candidate {
    Prebuilt(PathBuf),
}
pub(super) struct Admission {
    pub patched: String,
    pub candidate: Candidate,
}
fn stamp(path: &Path, patched: &str, tools: bool) -> DynResult<()> {
    let bytes = super::regular_input::read(path, 65536, "TTS native stamp")?;
    let lines = std::str::from_utf8(&bytes)?
        .lines()
        .collect::<std::collections::BTreeSet<_>>();
    let mut required = vec![
        format!("patched-sha={patched}"),
        "backend=cpu".into(),
        "link-mode=static".into(),
        "cmake-arg=-DGGML_NATIVE=OFF".into(),
        "cmake-arg=-DGGML_METAL=OFF".into(),
    ];
    if tools {
        required.push("cmake-arg=-DLLAMA_BUILD_TOOLS=ON".into());
    }
    for item in required {
        if !lines.contains(item.as_str()) {
            return Err(format!("TTS native stamp missing {item}").into());
        }
    }
    Ok(())
}
pub(super) fn admit(options: &Options) -> DynResult<Admission> {
    let patched = crate::upgrade::source::prepared_head(&options.root)?;
    let native = PathBuf::from(
        std::env::var_os("LLAMA_STAGE_BUILD_DIR").ok_or("missing CPU native directory")?,
    )
    .canonicalize()?;
    stamp(&native.join(".skippy-build-stamp"), &patched, false)?;
    stamp(
        &options
            .oracle
            .parent()
            .and_then(Path::parent)
            .ok_or("invalid oracle path")?
            .join(".skippy-build-stamp"),
        &patched,
        true,
    )?;
    let binary = PathBuf::from(
        std::env::var_os("SKIPPY_UPGRADE_SERVING_TEST_BIN")
            .ok_or("missing source-bound serving test binary")?,
    )
    .canonicalize()?;
    if !binary.is_file()
        || !options.oracle.is_file()
        || !options.model_path.is_file()
        || !options.projector.is_file()
    {
        return Err("missing TTS qualification input".into());
    }
    Ok(Admission {
        patched,
        candidate: Candidate::Prebuilt(binary),
    })
}
