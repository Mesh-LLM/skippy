use super::*;

fn device(stable_id: &str, selector: &str, capacity: u64) -> PlacementDevice {
    PlacementDevice {
        stable_id: stable_id.into(),
        selector: selector.into(),
        selectable: true,
        capacity_bytes: Some(capacity),
    }
}

#[test]
fn tracks_shared_contention_and_independent_gpu_budgets() {
    let allocator = DeviceAllocator::new(vec![
        device("gpu1", "CUDA0", 100),
        device("gpu2", "CUDA1", 200),
    ]);
    let first = PlacementRequest::Pinned("gpu1".into());
    let loaded = allocator.reserve(&first, 80).unwrap();
    assert_eq!(loaded.device().backend_device, "CUDA0");
    assert!(matches!(
        allocator.reserve(&first, 21),
        Err(PlacementError::Exhausted { available: 20, .. })
    ));
    let other = allocator
        .reserve(&PlacementRequest::Automatic, 180)
        .unwrap();
    assert_eq!(other.device().backend_device, "CUDA1");
    drop(loaded);
    assert!(allocator.reserve(&first, 100).is_ok());
}

#[test]
fn invalid_explicit_pins_do_not_fall_back() {
    let mut invisible = device("invisible", "", 500);
    invisible.selectable = false;
    let allocator = DeviceAllocator::new(vec![
        device("duplicate", "CUDA0", 100),
        device("duplicate", "CUDA1", 100),
        invisible,
        device("usable", "CUDA2", 100),
    ]);
    for (id, expected) in [
        ("duplicate", PlacementError::Ambiguous("duplicate".into())),
        (
            "invisible",
            PlacementError::Incompatible("invisible".into()),
        ),
        ("missing", PlacementError::Missing("missing".into())),
    ] {
        assert!(
            matches!(allocator.reserve(&PlacementRequest::Pinned(id.into()), 1), Err(error) if error == expected)
        );
    }
}

#[test]
fn reservation_rolls_back_when_a_load_fails() {
    let allocator = DeviceAllocator::new(vec![device("gpu", "CUDA0", 100)]);
    let pin = PlacementRequest::Pinned("gpu".into());
    let failed = || -> anyhow::Result<()> {
        let _reservation = allocator.reserve(&pin, 100)?;
        anyhow::bail!("simulated load failure")
    };
    assert!(failed().is_err());
    assert!(allocator.reserve(&pin, 100).is_ok());
}
