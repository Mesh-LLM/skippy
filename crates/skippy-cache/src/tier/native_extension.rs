use std::{collections::HashMap, num::NonZeroU64};

use anyhow::{Context, Result, ensure};

use crate::{GeometryBlock, SegmentCodecIdentity};

use super::*;

/// New native rows and recurrent state for a prefix backed by durable pages.
/// The geometry describes the full checkpoint at its final token boundary.
pub struct NativeKvExtension<'a> {
    pub base: &'a L3Location,
    pub reused_rows: NonZeroU64,
    pub geometry: &'a PayloadGeometry,
    pub kv_desc_json: String,
    pub kv_tail: &'a [u8],
    pub recurrent: &'a [u8],
}

impl L3Tier {
    pub fn spill_native_extension(
        &self,
        namespace: &str,
        tokens: &[i32],
        extension: NativeKvExtension<'_>,
    ) -> Result<Option<String>> {
        let _operation = self.manager.operation_guard();
        let _base_pin = self.store().pin(&extension.base.manifest_key);
        let base = self.store().load_manifest(&extension.base.manifest_key)?;
        let base_tokens = tokens
            .get(..usize::try_from(base.token_count)?)
            .context("base extends past the new checkpoint")?;
        ensure!(
            base.version == CHECKPOINT_MANIFEST_VERSION
                && base.uses_native_kv_passthrough()
                && base.model_identity == self.model_identity
                && base.state_identity == self.state_identity
                && base.prefix_key.as_deref() == Some(&l3_prefix_key(namespace, base_tokens)),
            "base checkpoint is incompatible with this extension"
        );
        let reused = extension.reused_rows.get();
        let geometry = extension.geometry;
        ensure!(
            geometry.rows == tokens.len() as u64
                && geometry.window_rows > 0
                && reused.is_multiple_of(geometry.window_rows)
                && reused <= base.token_count
                && reused < geometry.rows
                && geometry.tail_bytes == extension.recurrent.len() as u64,
            "invalid native extension geometry"
        );

        let mut manifest = HandoffManifest::new_for_model(
            self.model_identity.clone(),
            self.state_identity.clone(),
            "kv-recurrent".to_owned(),
        );
        manifest.version = CHECKPOINT_MANIFEST_VERSION;
        manifest.prefix_key = Some(l3_prefix_key(namespace, tokens));
        manifest.token_count = geometry.rows;
        manifest.total_bytes = geometry.total_bytes();
        manifest.recurrent_bytes = geometry.tail_bytes;
        manifest.kv_bytes = manifest
            .total_bytes
            .checked_sub(geometry.tail_bytes)
            .context("native extension KV size underflows")?;
        manifest.kv_decoded_bytes = manifest.kv_bytes;
        manifest.kv_desc_json = Some(extension.kv_desc_json);

        let mut pages = ExtensionPages {
            reusable: base
                .segments
                .iter()
                .filter_map(|page| {
                    page.codec_identity
                        .as_ref()
                        .filter(|codec| codec.is_native_kv_page())?;
                    Some((page.meta_json.as_deref()?, page))
                })
                .collect(),
            fresh: Vec::new(),
            manifest: &mut manifest,
            offset: 0,
        };
        let mut tail = extension.kv_tail;
        for block in &geometry.blocks {
            pages.append_run(block, &mut tail, geometry, reused)?;
        }
        ensure!(tail.is_empty(), "unused native extension KV bytes");
        for chunk in extension.recurrent.chunks(self.segment_bytes) {
            pages.append(
                chunk.len() as u64,
                Some(chunk),
                None,
                SegmentCodecIdentity::raw(chunk.len() as u64),
                "tail".into(),
            )?;
        }
        ensure!(
            pages.offset == pages.manifest.total_bytes,
            "incorrect native extension length"
        );
        let fresh = pages.fresh;
        self.commit_spill(namespace, tokens, manifest, fresh, None)
    }
}

struct ExtensionPages<'base, 'fresh, 'out> {
    reusable: HashMap<&'base str, &'base HandoffSegmentRef>,
    fresh: Vec<Option<&'fresh [u8]>>,
    manifest: &'out mut HandoffManifest,
    offset: u64,
}

impl<'fresh> ExtensionPages<'_, 'fresh, '_> {
    fn append_run(
        &mut self,
        block: &GeometryBlock,
        tail: &mut &'fresh [u8],
        geometry: &PayloadGeometry,
        reused: u64,
    ) -> Result<()> {
        let mut row = 0;
        while row < geometry.rows {
            let rows = geometry.window_rows.min(geometry.rows - row);
            let bytes = rows
                .checked_mul(block.stride)
                .context("page size overflows")?;
            let label = format!(
                "{}:{}:{}:{row}",
                block.kind.as_str(),
                block.layer,
                block.column
            );
            let (fresh, reused_page) = if row < reused {
                let page = self
                    .reusable
                    .get(label.as_str())
                    .filter(|page| page.bytes == bytes)
                    .context("base checkpoint is missing a reusable page")?;
                (None, Some(page.digest.clone()))
            } else {
                let (chunk, next) = tail
                    .split_at_checked(usize::try_from(bytes)?)
                    .context("native extension KV is truncated")?;
                *tail = next;
                (Some(chunk), None)
            };
            self.append(
                bytes,
                fresh,
                reused_page,
                SegmentCodecIdentity::native_kv_page(bytes),
                label,
            )?;
            row += rows;
        }
        Ok(())
    }

    fn append(
        &mut self,
        bytes: u64,
        fresh: Option<&'fresh [u8]>,
        reused: Option<String>,
        codec: SegmentCodecIdentity,
        label: String,
    ) -> Result<()> {
        let digest = fresh
            .map(segment_digest)
            .or(reused)
            .context("page has no digest")?;
        self.manifest.segments.push(HandoffSegmentRef {
            index: u32::try_from(self.manifest.segments.len())?,
            offset: self.offset,
            bytes,
            digest,
            codec_identity: Some(codec),
            meta_json: Some(label),
        });
        self.fresh.push(fresh);
        self.offset = self
            .offset
            .checked_add(bytes)
            .context("checkpoint size overflows")?;
        Ok(())
    }
}
