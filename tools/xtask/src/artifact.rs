//! File identity used by native build and packaging adapters.
use crate::command::DynResult;
use sha2::{Digest, Sha256};
use std::{io::Read, path::Path};

pub(crate) fn sha256(path: &Path) -> DynResult<String> {
    digest(std::fs::File::open(path)?)
}
pub(crate) fn digest(mut reader: impl Read) -> DynResult<String> {
    let mut hash = Sha256::new();
    let mut buffer = [0_u8; 65536];
    loop {
        let count = reader.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(hex::encode(hash.finalize()))
}
