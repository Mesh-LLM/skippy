use super::{DynResult, Value, http, json, read_json, write_json_file};
use crate::artifact;
use serde::Serialize;
use std::{fs, net::SocketAddr, path::Path};

#[derive(Serialize)]
pub(in crate::cache_parity::capture) struct Receipt {
    client_ns: u64,
    response: Value,
}

impl Receipt {
    pub(in crate::cache_parity::capture) fn retain(&self, directory: &Path) -> DynResult<()> {
        write_json_file(&directory.join("slot-restore.json"), self)
    }
}

#[derive(Clone, Copy)]
pub(in crate::cache_parity::capture) struct Snapshot {
    slot: usize,
    tokens: u64,
    bytes: u64,
}

impl Snapshot {
    pub(super) fn open(state: &Path, published: &Path, slot: usize) -> DynResult<Self> {
        let file = state.join(filename(slot));
        let saved = read_json(&published.join("slot-save.json"))?;
        let bytes = fs::metadata(&file)?.len();
        if saved["response"]["n_written"] != bytes {
            return Err("llama-server snapshot size differs from the save receipt".into());
        }
        let tokens = positive(&saved["response"]["n_saved"])?;
        write_json_file(
            &published.join("snapshot.json"),
            &json!({"bytes":bytes,"sha256":artifact::sha256(&file)?}),
        )?;
        Ok(Self {
            slot,
            tokens,
            bytes,
        })
    }

    pub(super) fn restore_sync(self, address: SocketAddr) -> DynResult<Receipt> {
        let (response, client_ns) = http::post_json(
            address,
            &format!("/slots/{}?action=restore", self.slot),
            body(self.slot)?,
        )?;
        self.check(response, client_ns)
    }

    pub(in crate::cache_parity::capture) async fn restore(
        self,
        address: SocketAddr,
    ) -> DynResult<Receipt> {
        let (response, client_ns) = http::post_json_async(
            address,
            &format!("/slots/{}?action=restore", self.slot),
            body(self.slot)?,
        )
        .await?;
        self.check(response, client_ns)
    }

    fn check(self, response: Value, client_ns: u64) -> DynResult<Receipt> {
        validate_receipt(&response, self.slot, "restore_ms")?;
        if response["n_restored"] != self.tokens || response["n_read"] != self.bytes {
            return Err("llama-server restore differs from the saved snapshot".into());
        }
        Ok(Receipt {
            client_ns,
            response,
        })
    }
}

pub(super) fn save(address: SocketAddr, output: &Path, slot: usize) -> DynResult<()> {
    let (response, client_ns) =
        http::post_json(address, &format!("/slots/{slot}?action=save"), body(slot)?)?;
    let receipt = Receipt {
        client_ns,
        response,
    };
    write_json_file(&output.join("slot-save.json"), &receipt)?;
    validate_receipt(&receipt.response, slot, "save_ms")?;
    positive(&receipt.response["n_saved"])?;
    positive(&receipt.response["n_written"])?;
    Ok(())
}

fn body(slot: usize) -> DynResult<Vec<u8>> {
    Ok(serde_json::to_vec(&json!({"filename": filename(slot)}))?)
}

fn filename(slot: usize) -> String {
    if slot == 0 {
        "checkpoint.bin".into()
    } else {
        format!("checkpoint-{slot:03}.bin")
    }
}

fn positive(value: &Value) -> DynResult<u64> {
    value
        .as_u64()
        .filter(|n| *n > 0)
        .ok_or_else(|| "invalid llama-server snapshot count".into())
}

fn validate_receipt(response: &Value, slot: usize, timing: &str) -> DynResult<()> {
    if response["id_slot"] != slot
        || response["filename"] != filename(slot)
        || response["timings"][timing]
            .as_f64()
            .is_none_or(|ms| !ms.is_finite() || ms < 0.0)
    {
        return Err(
            format!("llama-server returned an invalid {timing} receipt for slot {slot}").into(),
        );
    }
    Ok(())
}
