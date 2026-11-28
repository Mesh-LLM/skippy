use std::error::Error;
use std::path::Path;

pub(crate) type DynResult<T> = Result<T, Box<dyn Error>>;

pub(crate) fn write_json_file<T: serde::Serialize>(path: &Path, value: &T) -> DynResult<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut bytes = serde_json::to_vec_pretty(value)?;
    bytes.push(b'\n');
    std::fs::write(path, bytes)?;
    Ok(())
}
