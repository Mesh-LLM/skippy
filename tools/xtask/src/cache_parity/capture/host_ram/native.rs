use super::{BASELINE_RESTORE_MARKER, DynResult};
use std::{fs, path::Path};

pub(in crate::cache_parity) fn slot_restores(output: &Path, slots: usize) -> DynResult<Vec<f64>> {
    let mut found = None;
    for name in ["server.stdout.log", "server.stderr.log"] {
        let log = fs::read_to_string(output.join(name))?;
        if let Some(times) = parse(&log, slots)?
            && found.replace(times).is_some()
        {
            return Err("native L2 restore evidence appeared in multiple log streams".into());
        }
    }
    found.ok_or_else(|| "missing native L2 slot restore log".into())
}

fn parse(log: &str, slots: usize) -> DynResult<Option<Vec<f64>>> {
    let mut observed = vec![(0, None); slots];
    let mut active = None;
    let mut awaiting_elapsed = false;
    for line in log.lines() {
        if let Some(slot) = slot_selected(line)? {
            if awaiting_elapsed {
                return Err("native L2 restore had no elapsed time before the next slot".into());
            }
            let (count, _) = observed
                .get_mut(slot)
                .ok_or("native L2 log selected an unplanned slot")?;
            *count += 1;
            active = Some(slot);
        } else if line.contains(" load: ") && line.ends_with(BASELINE_RESTORE_MARKER) {
            let slot = active.ok_or("native L2 restore did not identify its slot")?;
            if awaiting_elapsed || observed[slot].0 != 3 || observed[slot].1.is_some() {
                return Err(
                    "native L2 restored outside the designated measured slot request".into(),
                );
            }
            awaiting_elapsed = true;
        } else if awaiting_elapsed
            && let Some((_, duration)) = line.split_once("get_availabl: prompt cache update took ")
        {
            let elapsed: f64 = duration
                .trim()
                .strip_suffix(" ms")
                .ok_or("native L2 restore time did not use milliseconds")?
                .parse()?;
            if !elapsed.is_finite() || elapsed < 0.0 {
                return Err("invalid native L2 restore time".into());
            }
            let slot = active.ok_or("native L2 restore timing did not identify its slot")?;
            observed[slot].1 = Some(elapsed);
            awaiting_elapsed = false;
        }
    }
    if active.is_none() {
        return Ok(None);
    }
    let times = observed
        .into_iter()
        .enumerate()
        .map(|(slot, (requests, elapsed))| {
            elapsed.filter(|_| requests == 3).ok_or_else(|| {
                format!(
                    "native L2 slot {slot} did not prove one restore after its seed and eviction"
                )
                .into()
            })
        })
        .collect::<DynResult<_>>()?;
    Ok(Some(times))
}

fn slot_selected(line: &str) -> DynResult<Option<usize>> {
    if let Some((header, slot)) = line.split_once(" | selected slot by id (")
        && header.contains("slot get_availabl: id ")
    {
        return Ok(Some(
            slot.strip_suffix(')')
                .ok_or("invalid native slot assignment")?
                .parse()?,
        ));
    }
    Ok(None)
}

#[test]
fn restores_are_only_accepted_in_the_third_request_for_each_slot() {
    let select = |slot| {
        format!("0.03 I slot get_availabl: id {slot} | task -1 | selected slot by id ({slot})\n")
    };
    let finish = |ms| {
        format!(
            "0.04 D srv          load: {BASELINE_RESTORE_MARKER}\n0.04 I srv  get_availabl: prompt cache update took {ms} ms\n"
        )
    };
    let prepared = [select(0), select(0), select(1), select(1)].concat();
    let capture = |last| [prepared.clone(), select(1), finish(21), select(0), last].concat();
    assert_eq!(
        parse(&capture(finish(31)), 2).unwrap(),
        Some(vec![31.0, 21.0])
    );
    assert!(parse(&capture(String::new()), 2).is_err());
    assert!(parse(&[select(0), finish(9), capture(finish(31))].concat(), 2).is_err());
}
