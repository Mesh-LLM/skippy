use super::Engine;
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Serialize)]
pub(super) struct SampledPhases {
    pub(super) ttft_ms: f64,
    pub(super) decode_ms: f64,
}

#[derive(Default)]
pub(super) struct NativeSamples(BTreeMap<u32, NativeSample>);

struct NativeSample {
    prompt_ms: f64,
    predicted_ms: f64,
}

impl NativeSamples {
    pub(super) fn record(&mut self, timings: &Value) {
        if let Some((count, sample)) = NativeSample::parse(timings) {
            self.0.entry(count).or_insert(sample);
        }
    }

    pub(super) fn phases(
        &self,
        engine: Option<Engine>,
        completion_tokens: Option<u32>,
        final_timings: Option<&Value>,
    ) -> Option<SampledPhases> {
        let count = completion_tokens.filter(|count| *count > 0)?;
        let (ttft_ms, decode_ms) = match engine? {
            Engine::Candidate => {
                let timings = final_timings?;
                if timings["predicted_n"].as_u64()? != u64::from(count) {
                    return None;
                }
                (
                    timings["sampled_ttft_ms"].as_f64()?,
                    timings["sampled_decode_ms"].as_f64()?,
                )
            }
            Engine::Baseline => {
                let first = self.0.get(&1)?;
                let last = self.0.get(&count)?;
                (first.prompt_ms, last.predicted_ms - first.predicted_ms)
            }
        };
        if ttft_ms.is_finite()
            && ttft_ms > 0.0
            && decode_ms.is_finite()
            && ((count == 1 && decode_ms == 0.0) || (count > 1 && decode_ms > 0.0))
        {
            Some(SampledPhases { ttft_ms, decode_ms })
        } else {
            None
        }
    }
}

impl NativeSample {
    fn parse(timings: &Value) -> Option<(u32, Self)> {
        let count = u32::try_from(timings["predicted_n"].as_u64()?).ok()?;
        let prompt_ms = timings["prompt_ms"].as_f64()?;
        let predicted_ms = timings["predicted_ms"].as_f64()?;
        (count > 0
            && prompt_ms.is_finite()
            && prompt_ms > 0.0
            && predicted_ms.is_finite()
            && predicted_ms >= 0.0)
            .then_some((
                count,
                Self {
                    prompt_ms,
                    predicted_ms,
                },
            ))
    }
}
