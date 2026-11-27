//! Stage configuration, topology, and activation contracts.

use serde::{Deserialize, Deserializer, Serialize, de};
use std::num::NonZeroU32;
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct StageIdentity {
    pub run_id: String,
    pub request_id: String,
    pub session_id: String,
    pub topology_id: String,
    pub stage_id: String,
    pub stage_index: u32,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum LoadMode {
    #[default]
    RuntimeSlice,
    LayerPackage,
    ArtifactSlice,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FlashAttentionType {
    #[default]
    Auto,
    Disabled,
    Enabled,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SplitMode {
    #[default]
    Auto,
    None,
    Layer,
    Row,
    Tensor,
}

/// Half-open absolute block range `[start, end)` whose routed-expert tensors
/// (`ffn_{up,down,gate}_exps`) load into host memory instead of accelerator
/// memory. Placement changes where the weights live and which kernels run
/// them; expert routing and precision are unchanged.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
pub struct HostExpertLayers {
    pub start: u32,
    pub end: u32,
}

/// Operator settings that decide a stage's [`HostExpertLayers`].
///
/// `cpu_moe = true` moves every block's routed experts in the stage, and
/// `n_cpu_moe = N` mirrors llama.cpp's `--n-cpu-moe N`: the first `N` model
/// blocks, counted from block 0, so a later split stage may hold none of
/// them. `cpu_moe = false` wins over `n_cpu_moe`.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct HostExpertPolicy {
    pub cpu_moe: Option<bool>,
    pub n_cpu_moe: Option<u32>,
}

impl HostExpertLayers {
    /// Build a range, returning `None` when it is empty.
    pub fn new(start: u32, end: u32) -> Option<Self> {
        (start < end).then_some(Self { start, end })
    }

    pub fn layer_count(self) -> u32 {
        self.end.saturating_sub(self.start)
    }

    /// Resolve the host-resident expert range for a stage holding
    /// `[layer_start, layer_end)`. Returns `None` when every routed expert
    /// stays on the accelerator.
    pub fn for_stage(layer_start: u32, layer_end: u32, policy: HostExpertPolicy) -> Option<Self> {
        let host_end = match (policy.cpu_moe, policy.n_cpu_moe) {
            (Some(false), _) | (None, None) => return None,
            (Some(true), _) => layer_end,
            (None, Some(count)) => count,
        };
        Self::new(layer_start, host_end.min(layer_end))
    }
}

#[cfg(test)]
mod host_expert_layer_tests {
    use super::{HostExpertLayers, HostExpertPolicy};

    fn layers(start: u32, end: u32) -> Option<HostExpertLayers> {
        HostExpertLayers::new(start, end)
    }

    #[test]
    fn n_cpu_moe_counts_blocks_from_block_zero() {
        // A later split stage may hold none of the first N blocks.
        let policy = HostExpertPolicy {
            cpu_moe: None,
            n_cpu_moe: Some(12),
        };
        assert_eq!(HostExpertLayers::for_stage(0, 64, policy), layers(0, 12));
        assert_eq!(HostExpertLayers::for_stage(32, 64, policy), None);
    }

    #[test]
    fn n_cpu_moe_is_clamped_to_the_stage_end() {
        let policy = HostExpertPolicy {
            cpu_moe: None,
            n_cpu_moe: Some(50),
        };
        assert_eq!(HostExpertLayers::for_stage(0, 32, policy), layers(0, 32));
        assert_eq!(HostExpertLayers::for_stage(32, 64, policy), layers(32, 50));
    }

    #[test]
    fn zero_n_cpu_moe_keeps_every_expert_on_the_accelerator() {
        let policy = HostExpertPolicy {
            cpu_moe: None,
            n_cpu_moe: Some(0),
        };
        assert_eq!(HostExpertLayers::for_stage(0, 64, policy), None);
    }

    #[test]
    fn cpu_moe_true_hosts_the_whole_stage() {
        let policy = HostExpertPolicy {
            cpu_moe: Some(true),
            n_cpu_moe: None,
        };
        assert_eq!(HostExpertLayers::for_stage(32, 64, policy), layers(32, 64));
    }

    #[test]
    fn cpu_moe_false_wins_over_n_cpu_moe() {
        let policy = HostExpertPolicy {
            cpu_moe: Some(false),
            n_cpu_moe: Some(32),
        };
        assert_eq!(HostExpertLayers::for_stage(0, 64, policy), None);
    }

    #[test]
    fn no_policy_keeps_every_expert_on_the_accelerator() {
        assert_eq!(
            HostExpertLayers::for_stage(0, 64, HostExpertPolicy::default()),
            None
        );
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum GlmDsaPolicy {
    #[default]
    Auto,
    V1,
}

/// Versioned encoding used for floating-point activation planes between stages.
///
/// Tokens, positions, masks, routing data, and control fields stay in their
/// exact wire representations. This policy applies only to activation values.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
pub enum StageActivationCodec {
    /// Exact little-endian IEEE-754 binary32 payloads. Kept as the correctness
    /// oracle and the default until a lossy codec is explicitly qualified.
    #[default]
    #[serde(rename = "raw-f32-v1")]
    RawF32V1,
    /// IEEE-754 binary16 with round-to-nearest, ties-to-even conversion.
    #[serde(rename = "f16-rne-v1")]
    F16RneV1,
    /// bfloat16 with round-to-nearest, ties-to-even conversion.
    #[serde(rename = "bf16-rne-v1")]
    Bf16RneV1,
    /// Per-logical-row symmetric signed int8 with an inline finite F32 scale.
    #[serde(rename = "s8-row-f32-rne-v1")]
    S8RowF32RneV1,
    /// Per-logical-row OCP FP8 E4M3 (±448, no infinities) with an inline
    /// finite F32 scale; round-to-nearest-even, saturating.
    #[serde(rename = "fp8-e4m3-row-f32-rne-v1")]
    Fp8E4m3RowF32RneV1,
    /// OCP FP8 E4M3 with one E8M0 power-of-two scale per 32 values of each
    /// logical row (the OCP MX layout); round-to-nearest-even, saturating.
    #[serde(rename = "fp8-e4m3-block32-e8m0-rne-v1")]
    Fp8E4m3Block32E8m0RneV1,
    /// Per-logical-row OCP FP8 E5M2 (±57344) with an inline finite F32
    /// scale; round-to-nearest-even, saturating.
    #[serde(rename = "fp8-e5m2-row-f32-rne-v1")]
    Fp8E5m2RowF32RneV1,
}

impl StageActivationCodec {
    /// Every codec, in wire-id order.
    pub const ALL: [Self; 7] = [
        Self::RawF32V1,
        Self::F16RneV1,
        Self::Bf16RneV1,
        Self::S8RowF32RneV1,
        Self::Fp8E4m3RowF32RneV1,
        Self::Fp8E4m3Block32E8m0RneV1,
        Self::Fp8E5m2RowF32RneV1,
    ];

    /// Whether this is one of the FP8 codecs.
    pub const fn is_fp8(self) -> bool {
        matches!(
            self,
            Self::Fp8E4m3RowF32RneV1 | Self::Fp8E4m3Block32E8m0RneV1 | Self::Fp8E5m2RowF32RneV1
        )
    }

    pub const fn identity(self) -> &'static str {
        match self {
            Self::RawF32V1 => "raw-f32-v1",
            Self::F16RneV1 => "f16-rne-v1",
            Self::Bf16RneV1 => "bf16-rne-v1",
            Self::S8RowF32RneV1 => "s8-row-f32-rne-v1",
            Self::Fp8E4m3RowF32RneV1 => "fp8-e4m3-row-f32-rne-v1",
            Self::Fp8E4m3Block32E8m0RneV1 => "fp8-e4m3-block32-e8m0-rne-v1",
            Self::Fp8E5m2RowF32RneV1 => "fp8-e5m2-row-f32-rne-v1",
        }
    }

    pub(crate) const fn binary_wire_id(self) -> i32 {
        match self {
            Self::RawF32V1 => 1,
            Self::F16RneV1 => 2,
            Self::Bf16RneV1 => 3,
            Self::S8RowF32RneV1 => 4,
            Self::Fp8E4m3RowF32RneV1 => 5,
            Self::Fp8E4m3Block32E8m0RneV1 => 6,
            Self::Fp8E5m2RowF32RneV1 => 7,
        }
    }

    pub(crate) const fn from_binary_wire_id(value: i32) -> Option<Self> {
        match value {
            1 => Some(Self::RawF32V1),
            2 => Some(Self::F16RneV1),
            3 => Some(Self::Bf16RneV1),
            4 => Some(Self::S8RowF32RneV1),
            5 => Some(Self::Fp8E4m3RowF32RneV1),
            6 => Some(Self::Fp8E4m3Block32E8m0RneV1),
            7 => Some(Self::Fp8E5m2RowF32RneV1),
            _ => None,
        }
    }
}

/// Admitted activation-codec rules for a topology.
///
/// The per-frame encoding remains [`StageActivationCodec`] (it rides the binary
/// frame header) and `activation_codec` stays the fixed codec for
/// [`StageActivationCodecPolicy::Fixed`] or the mandatory RawF32 fallback
/// codec for [`StageActivationCodecPolicy::AutoLosslessV1`]. The policy only
/// decides which encodings a receiver must admit. Lossless selection under
/// `AutoLosslessV1` decodes bit-identically to raw, so one policy namespace is
/// enough for topology and cache identity; a future lossy policy must
/// additionally namespace per realized frame codec and accumulated error.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StageActivationCodecPolicy {
    /// Legacy behavior: every frame uses exactly the configured codec.
    #[default]
    #[serde(rename = "fixed")]
    Fixed,
    /// Policy v1: the producer selects RawF32, byte-exact BF16, or byte-exact
    /// F16 per realized frame (BF16 wins ties); every receiver must admit all
    /// three. Requires the configured codec to be RawF32V1 (the mandatory
    /// fallback). Fail closed on any other codec.
    #[serde(rename = "auto-lossless-v1")]
    AutoLosslessV1,
}

impl StageActivationCodecPolicy {
    /// Fail-closed admission of a realized frame codec under this policy.
    /// `AutoLosslessV1` also fails when the configured fallback codec is not
    /// RawF32V1, so no caller can admit an Auto frame under an invalid
    /// fallback.
    pub fn permits(
        self,
        configured_codec: StageActivationCodec,
        frame_codec: StageActivationCodec,
    ) -> bool {
        match self {
            Self::Fixed => configured_codec == frame_codec,
            // S8 stays excluded until its lossy quality is qualified.
            // F16/BF16 are admitted only when the producer's
            // selector proves exact round-trip for the complete frame.
            Self::AutoLosslessV1 => {
                configured_codec == StageActivationCodec::RawF32V1
                    && matches!(
                        frame_codec,
                        StageActivationCodec::RawF32V1
                            | StageActivationCodec::Bf16RneV1
                            | StageActivationCodec::F16RneV1
                    )
            }
        }
    }

    /// Whether this policy is admissible with the configured codec.
    /// `AutoLosslessV1` requires RawF32V1 as its fallback codec.
    pub const fn compatible(self, configured_codec: StageActivationCodec) -> bool {
        match self {
            Self::Fixed => true,
            Self::AutoLosslessV1 => {
                matches!(configured_codec, StageActivationCodec::RawF32V1)
            }
        }
    }

    /// Identity string for topology and cache binding. `Fixed` returns the
    /// configured codec's identity byte-for-byte so existing digests are
    /// unchanged.
    pub const fn identity(self, configured_codec: StageActivationCodec) -> &'static str {
        match self {
            Self::Fixed => configured_codec.identity(),
            Self::AutoLosslessV1 => "auto-lossless-v1",
        }
    }
}

/// The algorithm that forms the scheduler's batches: how long a batch waits
/// for more requests, and which queued requests it takes.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BatchPolicyKind {
    /// Requests join a batch in the order they arrived, up to the lane count
    /// and the token budget.
    ArrivalOrder,
    /// The oldest prompts prefill first, one at a time unless
    /// `prefill_prompts` allows more.
    #[default]
    PromptOrder,
}

impl BatchPolicyKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::ArrivalOrder => "arrival_order",
            Self::PromptOrder => "prompt_order",
        }
    }
}

/// How many prompts one batch prefills. The wire is `"all"` or a positive
/// count; zero is refused rather than read as either.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(try_from = "PrefillPromptsWire", into = "PrefillPromptsWire")]
pub enum PrefillPrompts {
    /// Every queued prompt the lanes and the token budget leave room for.
    All,
    /// At most this many; one prefills a prompt at a time.
    AtMost(NonZeroU32),
}

impl PrefillPrompts {
    /// One prompt at a time.
    pub const ONE: Self = Self::AtMost(NonZeroU32::MIN);

    /// Whether one batch may prefill `prompts` prompts.
    pub fn allows(self, prompts: usize) -> bool {
        match self {
            Self::All => true,
            Self::AtMost(limit) => prompts <= usize::try_from(limit.get()).unwrap_or(usize::MAX),
        }
    }
}

#[derive(Deserialize, Serialize)]
#[serde(untagged)]
enum PrefillPromptsWire {
    Count(u32),
    Every(EveryPrompt),
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum EveryPrompt {
    All,
}

impl TryFrom<PrefillPromptsWire> for PrefillPrompts {
    type Error = &'static str;

    fn try_from(wire: PrefillPromptsWire) -> Result<Self, Self::Error> {
        match wire {
            PrefillPromptsWire::Every(EveryPrompt::All) => Ok(Self::All),
            PrefillPromptsWire::Count(count) => NonZeroU32::new(count)
                .map(Self::AtMost)
                .ok_or("prefill_prompts is all or a count of at least 1"),
        }
    }
}

impl From<PrefillPrompts> for PrefillPromptsWire {
    fn from(prompts: PrefillPrompts) -> Self {
        match prompts {
            PrefillPrompts::All => Self::Every(EveryPrompt::All),
            PrefillPrompts::AtMost(limit) => Self::Count(limit.get()),
        }
    }
}

/// How long a batch that holds a prompt waits for more requests.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PrefillWait {
    /// For a full wave of lanes, up to a window sized to the prompt, so
    /// prompts that arrive together prefill together.
    Wave,
    /// For the sessions already running, up to the decode window, as a batch
    /// of generated tokens does.
    Active,
}

/// Whether one batch may hold prompt chunks and generated tokens together.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BatchMixing {
    /// Generated tokens join batches that hold prompt chunks.
    Mixed,
    /// Prompt chunks or generated tokens, never both; the request at the
    /// head of the queue decides which.
    Separate,
}

/// The sessions a batch waits for before it runs.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WaitSessions {
    /// Every session the runtime holds.
    Runtime,
    /// The sessions handing in work one step at a time: prompts in their
    /// chunked prefill and requests generating tokens. A session that will
    /// not hand in a step, such as a prompt queued behind another or one
    /// that has finished, never holds a batch up.
    Direct,
}

/// How many of a prompt's tokens one scheduler step takes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PrefillChunk {
    /// One microbatch (`n_ubatch`), whatever else is running.
    Microbatch,
    /// By load. While no other request is generating tokens, a prompt takes
    /// a whole step, up to `n_batch`; while some are, a step holds
    /// `prefill_shared_tokens`, one token for each of them and the rest from
    /// the prompt.
    Adaptive,
}

/// Where a prompt's shared-prefix checkpoint lands.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CheckpointAlign {
    /// On the cache's stride grid, one stride before the prompt's tail.
    Stride,
    /// At the microbatch boundary at or below that point, so the prefill
    /// reaches it in whole microbatches rather than a short extra step.
    Microbatch,
}

/// Batch-forming settings. A value left unset is the one the policy chooses
/// for the backend the stage runs on.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BatchConfig {
    #[serde(default)]
    pub policy: Option<BatchPolicyKind>,
    /// How long a batch of generated tokens waits for the other active
    /// requests to join it, in microseconds.
    #[serde(default)]
    pub decode_window_us: Option<u32>,
    /// How long a prompt waits for others to join its prefill wave, per
    /// prompt token, in microseconds. `prefill_coalesce_max_ms` caps the total.
    #[serde(default)]
    pub prefill_window_per_token_us: Option<u32>,
    /// How many prompts one batch prefills.
    #[serde(default)]
    pub prefill_prompts: Option<PrefillPrompts>,
    /// How long a batch that holds a prompt waits for more requests.
    #[serde(default)]
    pub prefill_wait: Option<PrefillWait>,
    /// Whether prompt chunks and generated tokens share a batch.
    #[serde(default)]
    pub mixing: Option<BatchMixing>,
    /// The sessions a batch waits for.
    #[serde(default)]
    pub wait_sessions: Option<WaitSessions>,
    /// How many of a prompt's tokens one step takes.
    #[serde(default)]
    pub prefill_chunk: Option<PrefillChunk>,
    /// The tokens of an adaptive step shared with requests generating tokens;
    /// unset, one microbatch.
    #[serde(default)]
    pub prefill_shared_tokens: Option<NonZeroU32>,
    /// Where the shared-prefix checkpoint lands.
    #[serde(default)]
    pub checkpoint_align: Option<CheckpointAlign>,
}

impl BatchConfig {
    pub fn is_default(&self) -> bool {
        *self == Self::default()
    }
}

/// How many threads sample the requests of one decode wave.
///
/// The wire keeps the integer the setting has always been, so absent, null
/// and the legacy zero all read as [`SampleThreads::Auto`].
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(from = "Option<u32>", into = "Option<u32>")]
pub enum SampleThreads {
    /// The scheduler thread plus as many pooled helpers as the wave and the
    /// hardware allow.
    #[default]
    Auto,
    /// At most this many, the scheduler thread included; one samples every
    /// request on the scheduler thread.
    AtMost(NonZeroU32),
}

impl SampleThreads {
    /// The cap, or `None` when the scheduler chooses.
    pub const fn limit(self) -> Option<NonZeroU32> {
        match self {
            Self::Auto => None,
            Self::AtMost(threads) => Some(threads),
        }
    }
}

impl From<Option<u32>> for SampleThreads {
    fn from(threads: Option<u32>) -> Self {
        threads
            .and_then(NonZeroU32::new)
            .map_or(Self::Auto, Self::AtMost)
    }
}

impl From<SampleThreads> for Option<u32> {
    fn from(threads: SampleThreads) -> Self {
        threads.limit().map(NonZeroU32::get)
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StageConfig {
    pub run_id: String,
    pub topology_id: String,
    pub model_id: String,
    #[serde(default)]
    pub package_ref: Option<String>,
    #[serde(default)]
    pub manifest_sha256: Option<String>,
    #[serde(default)]
    pub source_model_path: Option<String>,
    #[serde(default)]
    pub source_model_sha256: Option<String>,
    #[serde(default)]
    pub source_model_bytes: Option<u64>,
    #[serde(default)]
    pub materialized_path: Option<String>,
    #[serde(default)]
    pub materialized_pinned: bool,
    #[serde(default)]
    pub model_path: Option<String>,
    /// Ordered local GGUF artifacts selected from an admitted package-v2
    /// tensor closure. The metadata-bearing primary artifact is first.
    #[serde(default)]
    pub model_part_paths: Vec<String>,
    #[serde(default)]
    pub projector_path: Option<String>,
    #[serde(default)]
    pub projector_use_gpu: Option<bool>,
    #[serde(default)]
    pub media_marker: Option<String>,
    #[serde(default)]
    pub image_min_tokens: Option<u32>,
    #[serde(default)]
    pub image_max_tokens: Option<u32>,
    #[serde(default)]
    pub batch_max_tokens: Option<u32>,
    #[serde(default)]
    pub glm_dsa_policy: GlmDsaPolicy,
    #[serde(default)]
    pub generation_signal_window: Option<u32>,
    /// Floating-point activation encoding for every downstream edge produced
    /// by this stage. Generation 10 peers echo and bind this policy before the
    /// binary data plane starts.
    #[serde(default)]
    pub activation_codec: StageActivationCodec,
    /// Admitted activation-codec rules for this topology. Defaults to
    /// `Fixed(activation_codec)`, which reproduces the legacy single-codec
    /// behavior and identity exactly.
    #[serde(default)]
    pub activation_codec_policy: StageActivationCodecPolicy,
    pub stage_id: String,
    pub stage_index: u32,
    pub layer_start: u32,
    pub layer_end: u32,
    #[serde(default = "default_ctx_size")]
    pub ctx_size: u32,
    #[serde(default = "default_lane_count")]
    pub lane_count: u32,
    #[serde(default)]
    pub n_batch: Option<u32>,
    #[serde(default)]
    pub n_ubatch: Option<u32>,
    #[serde(default)]
    pub n_gpu_layers: i32,
    #[serde(default)]
    pub mmap: Option<bool>,
    #[serde(default)]
    pub mlock: bool,
    #[serde(default)]
    pub repack: bool,
    #[serde(default)]
    pub op_offload: Option<bool>,
    #[serde(default)]
    pub no_host_buffer: bool,
    #[serde(default)]
    pub check_tensors: bool,
    #[serde(default)]
    pub direct_io: bool,
    #[serde(default)]
    pub main_gpu: Option<u32>,
    #[serde(default)]
    pub split_mode: SplitMode,
    /// Routed-expert tensors of these absolute blocks load into host memory.
    #[serde(default)]
    pub host_expert_layers: Option<HostExpertLayers>,
    #[serde(default = "default_cache_type")]
    pub cache_type_k: String,
    #[serde(default = "default_cache_type")]
    pub cache_type_v: String,
    #[serde(default)]
    pub flash_attn_type: FlashAttentionType,
    #[serde(default)]
    pub kv_offload: Option<bool>,
    #[serde(default)]
    pub kv_unified: Option<bool>,
    #[serde(default)]
    pub swa_full: Option<bool>,
    /// Cells in the unified KV pool, which the lanes share; at least
    /// `ctx_size`. None reserves `ctx_size` for every lane, so each can reach
    /// its window whatever the others hold.
    #[serde(default)]
    pub kv_pool_tokens: Option<NonZeroU32>,
    /// Zero KV cells as the pool's high-water mark advances rather than all at
    /// load. None and true are lazy wherever the cache supports it.
    #[serde(default)]
    pub kv_lazy_clear: Option<bool>,
    /// Hand the pages of free KV cells back to the system. None and true
    /// release wherever the pool's memory is host-visible.
    #[serde(default)]
    pub kv_release_pages: Option<bool>,
    /// Read a unified pool through per-owner row lists. None and true use
    /// them wherever the backend supports it; false keeps ordinary attention
    /// over the whole pool.
    #[serde(default)]
    pub kv_row_attention: Option<bool>,
    /// Threads that sample the requests of one decode wave.
    #[serde(default)]
    pub sample_threads: SampleThreads,
    #[serde(default)]
    pub cache_idle_slots: Option<u32>,
    /// The longest a prompt waits for others to join its prefill wave, in
    /// milliseconds. None is the scheduler's own ceiling; zero never waits.
    #[serde(default)]
    pub prefill_coalesce_max_ms: Option<u32>,
    /// How the scheduler forms its batches.
    #[serde(default, skip_serializing_if = "BatchConfig::is_default")]
    pub batch: BatchConfig,
    /// Exact native tensor names resolved locally from admitted package-v2
    /// tensor IDs. Empty is valid only for an unsplit full-model load.
    #[serde(
        default,
        alias = "filter_tensors_on_load",
        deserialize_with = "deserialize_resident_tensor_names"
    )]
    pub resident_tensor_names: Vec<String>,
    /// Opaque native dependency contract, reproduced and checked during admission.
    pub execution_contract: String,
    /// Planner value identities imported by this stage, in native frontier order.
    #[serde(default)]
    pub activation_import_identities: Vec<String>,
    /// Stable live-graph bindings paired with imported planner identities.
    #[serde(default)]
    pub activation_import_bindings: Vec<String>,
    /// Planner value identities exported by this stage, in native frontier order.
    #[serde(default)]
    pub activation_export_identities: Vec<String>,
    /// Stable live-graph bindings paired with exported planner identities.
    #[serde(default)]
    pub activation_export_bindings: Vec<String>,
    #[serde(default)]
    pub selected_device: Option<StageDevice>,
    #[serde(default)]
    pub kv_cache: Option<StageKvCacheConfig>,
    #[serde(default = "default_native_mtp_enabled")]
    pub native_mtp_enabled: bool,
    pub load_mode: LoadMode,
    pub bind_addr: String,
    #[serde(default)]
    pub upstream: Option<PeerConfig>,
    #[serde(default)]
    pub downstream: Option<PeerConfig>,
}

impl StageConfig {
    /// Whether this stage's plan makes it emit an activation frame for a
    /// downstream stage.
    ///
    /// Mirrors `skippy_runtime::RuntimeConfig::is_terminal_stage` and the native
    /// `skippy_emits_activation_frame`: an unsplit full-model load carries no
    /// resident tensor plan, and a terminal stage has no export frontier.
    pub fn emits_activation_frame(&self) -> bool {
        !self.resident_tensor_names.is_empty() && !self.activation_export_identities.is_empty()
    }
}

fn deserialize_resident_tensor_names<'de, D>(deserializer: D) -> Result<Vec<String>, D::Error>
where
    D: Deserializer<'de>,
{
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum ResidentTensorNames {
        Names(Vec<String>),
        Obsolete(bool),
    }

    match ResidentTensorNames::deserialize(deserializer)? {
        ResidentTensorNames::Names(names) => Ok(names),
        ResidentTensorNames::Obsolete(obsolete) => {
            let _ = obsolete;
            Err(de::Error::custom(
                "filter_tensors_on_load is obsolete; provide resident_tensor_names from package-v2 stage admission",
            ))
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct StageDevice {
    pub backend_device: String,
    #[serde(default)]
    pub stable_id: Option<String>,
    #[serde(default)]
    pub index: Option<usize>,
    #[serde(default)]
    pub vram_bytes: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum StageKvCacheMode {
    Disabled,
    Auto,
    Record,
    LookupRecord,
}

/// Durable KV representation used below the in-process exact-state cache.
/// CacheGen is opt-in until every backend/dtype combination clears its
/// hardware quality and latency gate.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum StageKvCacheCodec {
    #[default]
    Native,
    #[serde(rename = "cachegen")]
    CacheGen,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct StageKvCacheConfig {
    #[serde(default = "default_kv_cache_mode")]
    pub mode: StageKvCacheMode,
    #[serde(default = "default_kv_cache_max_entries")]
    pub max_entries: usize,
    #[serde(default)]
    pub max_bytes: u64,
    /// Host-RAM byte budget. Native resident caching uses this for retained
    /// checkpoint and token metadata, independently of disk; zero selects its
    /// bounded default. Serialized runtimes use an opt-in L2 mirror, where zero
    /// disables that mirror.
    #[serde(default)]
    pub l2_max_bytes: u64,
    /// Exact-state retention ceiling; None derives eight times max_bytes.
    /// One indivisible snapshot may exceed it. Some(0) disables the ceiling.
    #[serde(default)]
    pub exact_max_bytes: Option<u64>,
    #[serde(default)]
    pub codec: StageKvCacheCodec,
    #[serde(default = "default_kv_cache_min_tokens")]
    pub min_tokens: u64,
    #[serde(default = "default_kv_cache_shared_stride_tokens")]
    pub shared_prefix_stride_tokens: u64,
    #[serde(default = "default_kv_cache_shared_record_limit")]
    pub shared_prefix_record_limit: u64,
    /// How a prompt enters the runtime once its prefix has been looked up.
    #[serde(default)]
    pub prefill_admission: StagePrefillAdmission,
    /// What finished requests may keep resident in the KV pool for reuse.
    #[serde(default)]
    pub retention: StageKvRetention,
}

/// How many KV pool cells finished requests may keep resident, beyond what
/// active sessions hold, so a later prompt can reuse them. Retention fills
/// whatever it is allowed, and the pool's resident memory follows it there.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum StageKvRetention {
    /// At most this many cells; zero keeps nothing once a request ends.
    Tokens(u64),
    /// Whatever the pool has room for.
    Unbounded,
}

impl StageKvRetention {
    /// A few dozen typical prompts, or four 4k-token contexts.
    pub const DEFAULT_TOKENS: u64 = 16_384;
}

impl Default for StageKvRetention {
    fn default() -> Self {
        Self::Tokens(Self::DEFAULT_TOKENS)
    }
}

/// Prompt admission policy. Prefix lookup and restore always run as one cache
/// operation under the runtime lock; the policy decides who prefills the rest.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Deserialize, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum StagePrefillAdmission {
    /// The cache operation prefills the whole prompt itself: one prompt at a
    /// time, and every other request's decode waits for it.
    Serialized,
    /// The cache operation restores the prefix only; the suffix is prefilled
    /// as iteration batches that coalesce with other requests' prompt chunks
    /// and decode rows.
    #[default]
    Cooperative,
}

fn default_kv_cache_mode() -> StageKvCacheMode {
    StageKvCacheMode::Auto
}

fn default_kv_cache_max_entries() -> usize {
    64
}

fn default_kv_cache_min_tokens() -> u64 {
    64
}

fn default_kv_cache_shared_stride_tokens() -> u64 {
    128
}

fn default_kv_cache_shared_record_limit() -> u64 {
    2
}

fn default_ctx_size() -> u32 {
    512
}

fn default_lane_count() -> u32 {
    4
}

fn default_cache_type() -> String {
    "f16".to_string()
}

fn default_native_mtp_enabled() -> bool {
    true
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct PeerConfig {
    pub stage_id: String,
    pub stage_index: u32,
    pub endpoint: String,
    /// Send the persistent-lane marker only after the caller confirms peer support.
    #[serde(default)]
    pub persistent_lane_marker: bool,
}

#[cfg(test)]
mod peer_config_tests {
    use super::PeerConfig;

    #[test]
    fn persistent_lane_marker_is_opt_in() {
        let mut value = serde_json::json!({
            "stage_id": "stage-1", "stage_index": 1, "endpoint": "127.0.0.1:9001"
        });
        let legacy: PeerConfig = serde_json::from_value(value.clone()).unwrap();
        assert!(!legacy.persistent_lane_marker);
        value["persistent_lane_marker"] = true.into();
        let capable: PeerConfig = serde_json::from_value(value).unwrap();
        assert!(capable.persistent_lane_marker);
    }
}

#[cfg(test)]
mod sample_threads_tests {
    use super::{SampleThreads, StageConfig};
    use std::num::NonZeroU32;

    #[test]
    fn sample_threads_keeps_its_wire_integer() {
        let mut stage = serde_json::to_value(StageConfig::default()).unwrap();
        assert!(stage["sample_threads"].is_null());

        stage["sample_threads"] = 0.into();
        let legacy_zero: StageConfig = serde_json::from_value(stage.clone()).unwrap();
        assert_eq!(legacy_zero.sample_threads, SampleThreads::Auto);

        stage["sample_threads"] = 6.into();
        let capped: StageConfig = serde_json::from_value(stage).unwrap();
        assert_eq!(capped.sample_threads.limit(), NonZeroU32::new(6));
        assert_eq!(
            serde_json::to_value(capped).unwrap()["sample_threads"],
            serde_json::json!(6)
        );
    }
}

#[cfg(test)]
mod prefill_prompts_tests {
    use super::{BatchConfig, PrefillPrompts};
    use serde_json::json;
    use std::num::NonZeroU32;

    #[test]
    fn prefill_prompts_is_all_or_a_positive_count() {
        let read =
            |value| serde_json::from_value::<BatchConfig>(json!({ "prefill_prompts": value }));
        assert_eq!(
            read(json!("all")).unwrap().prefill_prompts,
            Some(PrefillPrompts::All)
        );
        let two = read(json!(2)).unwrap();
        assert_eq!(
            two.prefill_prompts,
            NonZeroU32::new(2).map(PrefillPrompts::AtMost)
        );
        assert_eq!(
            serde_json::to_value(two).unwrap()["prefill_prompts"],
            json!(2)
        );
        assert!(read(json!(0)).is_err());
        assert!(read(json!("some")).is_err());
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct StageTopology {
    pub topology_id: String,
    pub model_id: String,
    pub stages: Vec<StageTopologyEntry>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct StageTopologyEntry {
    pub stage_id: String,
    pub stage_index: u32,
    pub host: Option<String>,
    pub endpoint: String,
    pub layer_start: u32,
    pub layer_end: u32,
    pub load_mode: LoadMode,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ActivationDType {
    Unknown,
    F32,
    F16,
    Bf16,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ActivationLayout {
    Opaque,
    TokenMajor,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct ActivationDescriptor {
    pub version: u32,
    pub dtype: ActivationDType,
    pub layout: ActivationLayout,
    pub producer_stage_index: i32,
    pub layer_start: i32,
    pub layer_end: i32,
    pub token_count: u32,
    pub sequence_count: u32,
    pub payload_bytes: u64,
    #[serde(default)]
    pub flags: u64,
    #[serde(default)]
    pub payload_sha256: Option<String>,
}

#[cfg(test)]
mod operator_cache_config_tests {
    use super::*;

    #[test]
    fn graph_state_and_payload_claims_are_rejected() {
        let stage = serde_json::to_value(StageConfig::default()).unwrap();
        let mut claimed = stage.clone();
        claimed["kv_graph_state"] = serde_json::json!("dense");
        assert!(serde_json::from_value::<StageConfig>(claimed).is_err());
        for payload in ["auto", "resident-kv", "kv-recurrent", "full-state"] {
            let mut claimed = stage.clone();
            claimed["kv_cache"] = serde_json::json!({"payload": payload});
            assert!(serde_json::from_value::<StageConfig>(claimed).is_err());
        }
        let mut resources = stage;
        resources["kv_cache"] = serde_json::json!({
            "mode": "lookup-record", "max_entries": 32, "max_bytes": 1024,
        });
        assert!(serde_json::from_value::<StageConfig>(resources).is_ok());
    }
}
