//! Hardware-captured values, with no Metal dependency in the offline tests.
//! See evals/receipts-metal/README.md for provenance and the remaining gates.
use super::column_check::CheckedRows;
use crate::activation_rounding::Profile;
use crate::rounding_profiles::{BackendFamily, profiles_for};

const WIDTH: usize = 256;
const ROWS: usize = 16;
const POSITIONS: usize = 17;

fn capture(name: &str, saved: &[u8]) -> Vec<f32> {
    // An explicit hardware run supplies fresh captures; default tests are offline.
    let fresh = std::env::var_os("SKIPPY_METAL_CAPTURE_DIR").map(|dir| {
        std::fs::read(std::path::PathBuf::from(dir).join(name)).expect("read fresh Metal capture")
    });
    let bytes = fresh.as_deref().unwrap_or(saved);
    assert_eq!(
        bytes.len(),
        4 * (WIDTH * ROWS + WIDTH * POSITIONS + ROWS * (1 + POSITIONS))
    );
    bytes
        .as_chunks::<4>()
        .0
        .iter()
        .map(|v| f32::from_le_bytes(*v))
        .collect()
}

#[test]
fn captured_metal_decode_and_both_prefill_paths_match() {
    for (name, bytes, profile) in [
        (
            "tensor.f32le",
            include_bytes!("../../tests/fixtures/metal/tensor.f32le").as_slice(),
            Profile::MetalTensor,
        ),
        (
            "legacy.f32le",
            include_bytes!("../../tests/fixtures/metal/legacy.f32le").as_slice(),
            Profile::F16Weights,
        ),
    ] {
        let values = capture(name, bytes);
        assert!(values.iter().all(|v| v.is_finite()));
        let (weights, rest) = values.split_at(WIDTH * ROWS);
        let (x, rest) = rest.split_at(WIDTH * POSITIONS);
        let (decode, prefill) = rest.split_at(ROWS);
        let w: Vec<f64> = weights.iter().map(|&v| f64::from(v)).collect();
        let rows = CheckedRows {
            rows: w
                .as_chunks::<WIDTH>()
                .0
                .iter()
                .enumerate()
                .map(|(r, w)| (r, w.as_slice()))
                .collect(),
            blocks: Vec::new(),
        };
        assert!(rows.check(Profile::Raw, &x[..WIDTH], decode).error < 2e-6);
        let mut worst = 0.0f64;
        let mut raw_worst = 0.0f64;
        for (x, y) in x
            .as_chunks::<WIDTH>()
            .0
            .iter()
            .zip(prefill.as_chunks::<ROWS>().0)
        {
            let check = rows.check(profile, x, y);
            assert!(check.error < 2e-6, "{name}: {check:?}");
            assert_eq!(check.flips, 0, "{name}: no repair should be necessary");
            worst = worst.max(check.error);
            raw_worst = raw_worst.max(rows.check(Profile::Raw, x, y).error);
            // A wrong output must still fail the unchanged production tolerance.
            let other: Vec<f32> = y.iter().map(|v| v * 1.05).collect();
            assert!(rows.check(profile, x, &other).error > super::TOLERANCE);
        }
        assert!(
            raw_worst > 1e-4,
            "fixture must distinguish this path from raw: {raw_worst}"
        );
        eprintln!("{name}: matched={worst:e}, raw={raw_worst:e}");
    }
}

#[test]
fn tensor_fraction_is_truncated_not_rounded_or_reexponented() {
    let x = [1.0009, -1.0009, 100_000.0, -0.0, f32::from_bits(0x1fff)];
    let rounded = Profile::MetalTensor.apply(&x);
    assert_eq!(&rounded[..2], &[1.0, -1.0]);
    assert_eq!(rounded[2], 99_968.0);
    assert_eq!(rounded[3].to_bits(), (-0.0f64).to_bits());
    assert_eq!(rounded[4], 0.0);
    assert_ne!(rounded[0], Profile::F16.apply(&x)[0]);
    let special = Profile::MetalTensor.apply(&[f32::from_bits(0x7f80_0001), f32::INFINITY]);
    assert!(special[0].is_nan());
    assert_eq!(special[1], f64::INFINITY);
}

#[test]
fn matrix_candidates_do_not_enable_unqualified_metal_receipts() {
    for backend in ["MTL0", "MTL1", "Metal"] {
        assert_eq!(BackendFamily::of(backend), None);
    }
    for family in [BackendFamily::Cpu, BackendFamily::Cuda] {
        for weight_type in 0..=40 {
            if let Some(profiles) = profiles_for(family, weight_type) {
                assert!(!profiles.contains(&Profile::MetalTensor));
                assert!(!profiles.contains(&Profile::F16Weights));
            }
        }
    }
}
