use super::{
    DynResult,
    contract::{CacheState, REQUESTS, Workload, WorkloadDesign},
};
use crate::{artifact, command::write_json_file};
use serde::Serialize;
use serde_json::{Value, json};
use std::{collections::BTreeSet, fs, path::Path};

mod conversation;
#[cfg(test)]
mod tests;

const LANES: [&str; 32] = [
    "Amber", "Birch", "Cactus", "Delta", "Ember", "Finch", "Granite", "Harbor", "Indigo",
    "Juniper", "Kestrel", "Lagoon", "Marble", "Nectar", "Orchid", "Pine", "Quartz", "Robin",
    "Saffron", "Tundra", "Umber", "Velvet", "Willow", "Xenon", "Yucca", "Zephyr", "Acorn", "Basil",
    "Crane", "Drift", "Elm", "Fjord",
];

#[derive(Serialize)]
struct Input {
    request: String,
    request_sha256: String,
}

#[derive(Serialize)]
struct Entry {
    #[serde(flatten)]
    measured: Input,
    expected_content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    seed: Option<Input>,
    #[serde(skip_serializing_if = "Option::is_none")]
    eviction: Option<Input>,
}

#[derive(Serialize)]
struct Manifest {
    schema_version: u32,
    design: WorkloadDesign,
    bursts: Vec<Vec<Entry>>,
}

pub(super) fn run(
    model: &str,
    state: &str,
    scenario: &str,
    width: usize,
    output: &Path,
) -> DynResult<()> {
    let state = serde_json::from_value(Value::String(state.into()))?;
    let scenario = serde_json::from_value(Value::String(scenario.into()))?;
    write_workload(model, state, scenario, width, output)
}

fn write_workload(
    model: &str,
    state: CacheState,
    scenario: Workload,
    width: usize,
    output: &Path,
) -> DynResult<()> {
    if model.trim().is_empty() || !(1..=LANES.len()).contains(&width) {
        return Err("workload generation requires a model alias and a burst width of 1–32".into());
    }
    fs::create_dir(output)?;
    fs::create_dir(output.join("inputs"))?;
    let mut writer = Writer {
        output,
        inputs: BTreeSet::new(),
        model,
        state,
    };
    let total = REQUESTS as usize;
    let burst_count = total.div_ceil(width);
    let bursts = (0..burst_count)
        .map(|burst| {
            let size = (total - burst * width).min(width);
            (0..size)
                .map(|slot| writer.write_entry(scenario, burst, burst_count, slot))
                .collect::<DynResult<Vec<_>>>()
        })
        .collect::<DynResult<_>>()?;
    write_json_file(
        &output.join("workload.json"),
        &Manifest {
            schema_version: 1,
            design: WorkloadDesign {
                cache: state,
                workload: scenario,
            },
            bursts,
        },
    )
}

struct Writer<'a> {
    output: &'a Path,
    inputs: BTreeSet<String>,
    model: &'a str,
    state: CacheState,
}

impl Writer<'_> {
    fn write_entry(
        &mut self,
        scenario: Workload,
        burst: usize,
        burst_count: usize,
        slot: usize,
    ) -> DynResult<Entry> {
        let lane = LANES[slot];
        let expected_content = format!("{} REVIEW COMPLETE", lane.to_uppercase());
        let conversation =
            conversation::build(scenario, lane, burst, burst_count, &expected_content);
        let measured = self.write_input(slot, &conversation.replay)?;
        let seed = if self.state == CacheState::ColdPrefill {
            None
        } else {
            Some(self.write_input(slot, &conversation.seed)?)
        };
        let eviction = if self.state == CacheState::L2Restore {
            Some(self.write_input(slot, &conversation::eviction(lane, burst))?)
        } else {
            None
        };
        Ok(Entry {
            measured,
            expected_content,
            seed,
            eviction,
        })
    }

    fn write_input(&mut self, slot: usize, messages: &[Value]) -> DynResult<Input> {
        let body = serde_json::to_vec(&json!({
            "model":self.model,"messages":messages,"temperature":0,"seed":42,
            "max_tokens":32,"stream":true,"stream_options":{"include_usage":true},
            "chat_template_kwargs":{"enable_thinking":false},"reasoning_effort":"none",
            "cache_prompt":self.state != CacheState::ColdPrefill,"id_slot":slot,
            "timings_per_token":true,"verbose":true,
        }))?;
        let request_sha256 = artifact::digest(&body[..])?;
        let request = format!("inputs/{request_sha256}.json");
        if self.inputs.insert(request_sha256.clone()) {
            fs::write(self.output.join(&request), body)?;
        }
        Ok(Input {
            request,
            request_sha256,
        })
    }
}
