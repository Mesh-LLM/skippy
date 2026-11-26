//! The native functions a feature family needs, which the capability probe
//! asks about by name.
//!
//! A dynamic runtime answers through its loader. A static one answers from a
//! direct reference to each function, so the linker keeps it and a missing
//! one fails the link. Asking `dlsym` instead finds nothing on Linux, whose
//! executables export none of their own symbols, and turns every family off.

/// A function a feature family needs.
#[derive(Clone, Copy, Debug)]
pub struct NativeSymbol {
    name: &'static [u8],
    #[cfg(not(feature = "dynamic-runtime"))]
    linked: *const std::ffi::c_void,
}

impl NativeSymbol {
    /// The NUL-terminated symbol name.
    pub const fn name(&self) -> &'static [u8] {
        self.name
    }

    /// Whether the runtime has this function.
    ///
    /// # Panics
    ///
    /// In a dynamic build, if no native runtime library has been loaded yet.
    pub fn present(&self) -> bool {
        #[cfg(feature = "dynamic-runtime")]
        {
            crate::dynamic::symbol_present(self.name)
        }
        #[cfg(not(feature = "dynamic-runtime"))]
        {
            // Read at run time, so the reference, and the link it forces, stay.
            !std::hint::black_box(self.linked).is_null()
        }
    }
}

macro_rules! native_symbols {
    ($($constant:ident = $function:ident,)*) => {
        $(
            pub const $constant: NativeSymbol = NativeSymbol {
                name: concat!(stringify!($function), "\0").as_bytes(),
                #[cfg(not(feature = "dynamic-runtime"))]
                linked: crate::static_bindings::$function as *const std::ffi::c_void,
            };
        )*
    };
}

native_symbols! {
    SKIPPY_MODEL_OPEN_WITH_EVENTS = skippy_model_open_with_events,
    SKIPPY_MODEL_OPEN_FROM_PARTS_WITH_EVENTS = skippy_model_open_from_parts_with_events,
    SKIPPY_SET_RUNTIME_EVENT_REPORTER = skippy_set_runtime_event_reporter,
    SKIPPY_CLEAR_RUNTIME_EVENT_REPORTER = skippy_clear_runtime_event_reporter,
    SKIPPY_EMIT_MODEL_LOAD_EVENT_V2 = skippy_emit_model_load_event_v2,
    SKIPPY_EMIT_KV_EVENT = skippy_emit_kv_event,
    SKIPPY_EMIT_DEVICE_EVENT = skippy_emit_device_event,
    SKIPPY_EMIT_DIAGNOSTIC_EVENT = skippy_emit_diagnostic_event,
    SKIPPY_EMIT_UNLOAD_EVENT = skippy_emit_unload_event,
    SKIPPY_MODEL_WORKLOAD_INFO_V1 = skippy_model_workload_info_v1,
    SKIPPY_SESSION_EMBED = skippy_session_embed,
    SKIPPY_SESSION_RERANK = skippy_session_rerank,
    SKIPPY_SESSION_ENCODE_PROMPT = skippy_session_encode_prompt,
    SKIPPY_LAYA_MODEL_OPEN = skippy_laya_model_open,
    SKIPPY_LAYA_MODEL_FREE = skippy_laya_model_free,
    SKIPPY_LAYA_MODEL_INFO_V1 = skippy_laya_model_info_v1,
    SKIPPY_LAYA_MODEL_MEMORY_V1 = skippy_laya_model_memory_v1,
    SKIPPY_LAYA_TOKENIZE = skippy_laya_tokenize,
    SKIPPY_LAYA_READ = skippy_laya_read,
    SKIPPY_CLEF_MODEL_OPEN = skippy_clef_model_open,
    SKIPPY_CLEF_MODEL_FREE = skippy_clef_model_free,
    SKIPPY_CLEF_MODEL_INFO_V1 = skippy_clef_model_info_v1,
    SKIPPY_CLEF_MODEL_DECISION_METADATA = skippy_clef_model_decision_metadata,
    SKIPPY_CLEF_PLAN = skippy_clef_plan,
    SKIPPY_CLEF_READ = skippy_clef_read,
    SKIPPY_ASR_MODEL_OPEN = skippy_asr_model_open,
    SKIPPY_ASR_MODEL_FREE = skippy_asr_model_free,
    SKIPPY_ASR_MODEL_INFO_V1 = skippy_asr_model_info_v1,
    SKIPPY_ASR_DECODE_AUDIO = skippy_asr_decode_audio,
    SKIPPY_ASR_SAMPLES_FREE = skippy_asr_samples_free,
    SKIPPY_ASR_TRANSCRIBE = skippy_asr_transcribe,
    SKIPPY_ASR_RESULT_SEGMENT_COUNT = skippy_asr_result_segment_count,
    SKIPPY_ASR_RESULT_SEGMENT_V1 = skippy_asr_result_segment_v1,
    SKIPPY_ASR_RESULT_LANGUAGE = skippy_asr_result_language,
    SKIPPY_ASR_RESULT_DURATION_SECONDS = skippy_asr_result_duration_seconds,
    SKIPPY_ASR_RESULT_FREE = skippy_asr_result_free,
    SKIPPY_SET_NATIVE_SPAN_CALLBACK = skippy_set_native_span_callback,
    SKIPPY_SET_GPU_COMMAND_TIMING_CALLBACK = skippy_set_gpu_command_timing_callback,
    SKIPPY_SET_TRACE_TAG = skippy_set_trace_tag,
}
