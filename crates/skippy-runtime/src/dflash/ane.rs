//! ANE DFlash attachment and package selection.

use std::path::Path;

use anyhow::{Context, Result, bail};
use skippy_ane::dflash::{AneDraft, PairedGguf};

use super::{DFlashDraft, DFlashDraftOptions, DFlashVariant, RuntimeConfig};

pub(super) fn requested(options: &DFlashDraftOptions) -> bool {
    options.device.as_deref() == Some("ANE")
        || std::env::var_os("SKIPPY_DFLASH_ANE_MODEL").is_some()
}

pub(super) fn attach(
    draft: &mut DFlashDraft,
    options: &DFlashDraftOptions,
    config: &RuntimeConfig,
    draft_path: &Path,
    target_path: Option<&Path>,
) -> Result<()> {
    if !requested(options) {
        return Ok(());
    }
    if draft.info.variant != DFlashVariant::DFlash {
        bail!("ANE conversion is available only for plain DFlash drafts");
    }
    if config.layer_start != 0 {
        bail!("ANE DFlash currently requires a single-stage target GGUF");
    }
    let target = target_path.context("ANE DFlash requires a local target GGUF")?;
    let lanes = usize::try_from(draft.lanes).context("negative ANE lane count")?;
    if options.device.as_deref() == Some("ANE") {
        let rows = draft.info.max_draft_tokens + 1;
        let context = usize::try_from(config.ctx_size)?.min(512).max(rows);
        draft.ane = Some(AneDraft::from_pair(
            lanes,
            draft.n_vocab,
            draft.mask_token,
            PairedGguf {
                draft: draft_path,
                target,
                context,
                rows,
            },
        )?);
    } else {
        draft.ane = AneDraft::from_env(lanes, draft.n_vocab, draft.mask_token, draft_path, target)?;
        if draft
            .ane
            .as_ref()
            .is_some_and(|ane| draft.info.max_draft_tokens > ane.max_proposals())
        {
            bail!("ANE DFlash package has fewer proposal rows than the configured draft limit");
        }
    }
    Ok(())
}
