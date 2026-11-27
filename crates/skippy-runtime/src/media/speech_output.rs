//! Native speech output admission and the public mono PCM encoding.

use std::num::NonZeroU32;

use anyhow::{Context, Result, anyhow};

use super::SpeechOutputFormat;

pub(super) struct NativeSpeechMetadata {
    pub sample_rate: i32,
    pub sample_count: i64,
    pub channels: i32,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct SpeechMetadata {
    pub sample_rate: u32,
    pub sample_count: u64,
    pub channels: NonZeroU32,
}

pub(super) fn supports_one_shot_speech(kind: skippy_ffi::MtmdGenAudioType) -> bool {
    !matches!(
        kind,
        skippy_ffi::MtmdGenAudioType::None | skippy_ffi::MtmdGenAudioType::Moshi
    )
}

/// Parse native frame counts once, accounting for every interleaved channel.
pub(super) fn validated_speech_metadata(
    format: SpeechOutputFormat,
    native: NativeSpeechMetadata,
    data_len: usize,
) -> Result<SpeechMetadata> {
    let sample_rate = u32::try_from(native.sample_rate).context("invalid speech sample rate")?;
    if sample_rate == 0 {
        return Err(anyhow!("speech synthesis reported a zero sample rate"));
    }
    let sample_count = u64::try_from(native.sample_count).context("invalid speech sample count")?;
    if sample_count == 0 {
        return Err(anyhow!("speech synthesis reported zero samples"));
    }
    let channels =
        NonZeroU32::new(u32::try_from(native.channels).context("invalid speech channel count")?)
            .context("speech synthesis reported zero channels")?;
    let sample_bytes = match format {
        SpeechOutputFormat::Wav => 2,
        SpeechOutputFormat::PcmS16Le => std::mem::size_of::<f32>(),
    };
    let required_bytes = usize::try_from(sample_count)
        .ok()
        .and_then(|count| count.checked_mul(usize::try_from(channels.get()).ok()?))
        .and_then(|count| count.checked_mul(sample_bytes));
    let valid = match format {
        SpeechOutputFormat::Wav => required_bytes.is_some_and(|bytes| data_len >= bytes),
        SpeechOutputFormat::PcmS16Le => required_bytes == Some(data_len),
    };
    if !valid {
        return Err(anyhow!(
            "speech synthesis reported {sample_count} frames across {channels} channels for a {data_len} byte payload"
        ));
    }
    Ok(SpeechMetadata {
        sample_rate,
        sample_count,
        channels,
    })
}

/// Downmix interleaved native PCM to mono and preserve mono quantization.
pub(super) fn pcm_f32_to_s16le(bytes: &[u8], channels: NonZeroU32) -> Result<Vec<u8>> {
    let frame_bytes = usize::try_from(channels.get())
        .context("speech channel count exceeds usize")?
        .checked_mul(4)
        .context("speech frame width exceeds usize")?;
    if !bytes.len().is_multiple_of(frame_bytes) {
        return Err(anyhow!(
            "native PCM payload is not aligned to complete interleaved frames"
        ));
    }
    let mut output = Vec::with_capacity(bytes.len() / frame_bytes * 2);
    for frame in bytes.chunks_exact(frame_bytes) {
        let sample = if channels.get() == 1 {
            f32::from_ne_bytes([frame[0], frame[1], frame[2], frame[3]])
        } else {
            let sum = frame
                .as_chunks::<4>()
                .0
                .iter()
                .map(|value| f64::from(f32::from_ne_bytes(*value)))
                .sum::<f64>();
            (sum / f64::from(channels.get())) as f32
        };
        let quantized = (sample.clamp(-1.0, 1.0) * f32::from(i16::MAX)).round() as i16;
        output.extend_from_slice(&quantized.to_le_bytes());
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn metadata(
        format: SpeechOutputFormat,
        rate: i32,
        count: i64,
        channels: i32,
        bytes: usize,
    ) -> Result<SpeechMetadata> {
        validated_speech_metadata(
            format,
            NativeSpeechMetadata {
                sample_rate: rate,
                sample_count: count,
                channels,
            },
            bytes,
        )
    }

    #[test]
    fn one_shot_capability_excludes_duplex_moshi() {
        use skippy_ffi::MtmdGenAudioType as Audio;
        assert!(!supports_one_shot_speech(Audio::Moshi));
        assert!(!supports_one_shot_speech(Audio::None));
        for kind in [
            Audio::Qwen3Tts,
            Audio::PocketTts,
            Audio::Soprano,
            Audio::LFM3A,
            Audio::Chatterbox,
            Audio::DRAMA3,
            Audio::ZONOS3,
            Audio::YUE2,
            Audio::MinimaxMUSIC3,
            Audio::FishS2,
            Audio::Kokoro,
            Audio::TinyVoca,
            Audio::VOXCPM2,
        ] {
            assert!(supports_one_shot_speech(kind));
        }
    }

    #[test]
    fn native_metadata_counts_frames_per_channel() {
        use SpeechOutputFormat::{PcmS16Le as Pcm, Wav};
        let mono = metadata(Pcm, 24_000, 4, 1, 16).expect("mono PCM");
        assert_eq!(
            (mono.sample_rate, mono.sample_count, mono.channels.get()),
            (24_000, 4, 1)
        );
        assert!(metadata(Wav, 24_000, 4, 1, 52).is_ok());
        assert!(metadata(Pcm, 24_000, 1_000, 2, 8_000).is_ok());
        assert!(metadata(Wav, 24_000, 1_000, 2, 4_044).is_ok());
        for (rate, count, channels, bytes) in [
            (0, 4, 1, 16),
            (24_000, 0, 1, 16),
            (24_000, -1, 1, 16),
            (24_000, 5, 1, 16),
            (24_000, 4, 1, 20),
            (24_000, 4, 0, 16),
            (24_000, 4, -1, 16),
            (24_000, 1_000, 2, 4_000),
            (24_000, i64::MAX, i32::MAX, 16),
        ] {
            assert!(metadata(Pcm, rate, count, channels, bytes).is_err());
        }
        assert!(metadata(Wav, 24_000, 9, 1, 16).is_err());
    }

    #[test]
    fn mono_pcm_keeps_clipping_and_quantization() {
        let samples = [-2.0_f32, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0];
        let bytes = samples
            .iter()
            .flat_map(|value| value.to_ne_bytes())
            .collect::<Vec<_>>();
        let output = pcm_f32_to_s16le(&bytes, NonZeroU32::new(1).expect("mono")).expect("PCM");
        let actual = output
            .as_chunks::<2>()
            .0
            .iter()
            .map(|v| i16::from_le_bytes(*v))
            .collect::<Vec<_>>();
        assert_eq!(
            actual,
            [-32_767, -32_767, -16_384, 0, 16_384, 32_767, 32_767]
        );
        assert!(pcm_f32_to_s16le(&[0, 1, 2], NonZeroU32::new(1).expect("mono")).is_err());
    }

    #[test]
    fn stereo_pcm_is_explicitly_downmixed_to_mono() {
        let samples = [1.0_f32, -1.0, 1.0, 0.0, -1.0, 0.0];
        let bytes = samples
            .iter()
            .flat_map(|value| value.to_ne_bytes())
            .collect::<Vec<_>>();
        let channels = NonZeroU32::new(2).expect("stereo");
        let output = pcm_f32_to_s16le(&bytes, channels).expect("stereo PCM");
        let actual = output
            .as_chunks::<2>()
            .0
            .iter()
            .map(|v| i16::from_le_bytes(*v))
            .collect::<Vec<_>>();
        assert_eq!(actual, [0, 16_384, -16_384]);
        assert!(pcm_f32_to_s16le(&bytes[..12], channels).is_err());
    }
}
