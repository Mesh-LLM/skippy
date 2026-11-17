use super::build::Binaries;
use crate::{
    command::{DynResult, write_json_file},
    execution,
    upgrade::evidence::Model,
};
use serde::Deserialize;
use std::path::Path;
#[derive(Deserialize)]
struct Topology {
    boundaries: Vec<Boundary>,
    two_stage_splits: Vec<u32>,
    three_stage_splits: Vec<u32>,
}
#[derive(Deserialize)]
struct Boundary {
    layer: u32,
    decision: String,
}
pub(super) fn run(
    model: &Model,
    target: &Path,
    projector: Option<&Path>,
    bins: &Binaries,
    out: &Path,
) -> DynResult<()> {
    let model_id = format!("{}:{}", model.artifact.repo, model.artifact.selector);
    let layers = model
        .execution
        .trunk_layers
        .checked_add(model.execution.mtp_layers)
        .ok_or("layer count overflow")?;
    let topology = execution::capture(
        &bins.bin.join("skippy-topology-plan").display().to_string(),
        &execution::strings(&[
            &model_id,
            &layers.to_string(),
            &model.execution.activation_width.to_string(),
        ]),
        &bins.environment(),
    )?;
    std::fs::write(out.join("topology.json"), &topology)?;
    let topology: Topology = serde_json::from_str(&topology)?;
    validate_topology(&topology, layers)?;
    let split = topology.two_stage_splits[0];
    let splits = topology
        .three_stage_splits
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join(",");
    let mut args = execution::strings(&[
        "scripts/family-certify.sh",
        "--family",
        &model.family,
        "--target-model",
        &target.display().to_string(),
        "--model-id",
        &model_id,
        "--layer-end",
        &layers.to_string(),
        "--split-layer",
        &split.to_string(),
        "--splits",
        &splits,
        "--activation-width",
        &model.execution.activation_width.to_string(),
        "--n-gpu-layers",
        if bins.backend == "cpu" { "0" } else { "999" },
        "--startup-timeout-secs",
        "900",
        "--skip-build",
        "--skip-speculative",
        "--require-lanes",
        "--cert-root",
        &out.display().to_string(),
        "--run-id",
        "parity",
    ]);
    if model.execution.mtp_layers > 0 {
        args.push("--require-native-mtp-draft".into());
    }
    let mut environment = bins.environment();
    environment.push(("FAMILY_BATTERY_BIN_DIR", bins.bin.display().to_string()));
    super::logged("bash", &args, &environment, &out.join("parity"), 7200)?;
    if bins.backend != "cpu" && !offloaded(out)? {
        return Err(format!(
            "{} produced no evidence of actual GPU layer offload",
            model.family
        )
        .into());
    }
    if let Some(projector) = projector {
        multimodal(model, target, projector, bins, out, split)?;
    }
    write_json_file(
        &out.join("lanes.json"),
        &crate::upgrade::evidence::lanes(model)?,
    )
}
fn validate_topology(topology: &Topology, layers: u32) -> DynResult<()> {
    let declared = topology
        .boundaries
        .iter()
        .map(|b| b.layer)
        .collect::<std::collections::BTreeSet<_>>();
    if declared != (1..layers).collect()
        || topology.boundaries.len() != declared.len()
        || topology.two_stage_splits.len() != 1
        || topology.three_stage_splits.len() != 2
    {
        return Err(
            "topology does not exhaustively classify boundaries and choose 2/3-stage cuts".into(),
        );
    }
    for cut in topology
        .two_stage_splits
        .iter()
        .chain(&topology.three_stage_splits)
    {
        if !topology
            .boundaries
            .iter()
            .any(|b| b.layer == *cut && b.decision == "accepted")
        {
            return Err("topology selected an unapproved cut".into());
        }
    }
    if topology.three_stage_splits[0] >= topology.three_stage_splits[1]
        || topology
            .boundaries
            .iter()
            .any(|b| !matches!(b.decision.as_str(), "accepted" | "rejected"))
    {
        return Err("invalid topology boundary ordering or decision".into());
    }
    Ok(())
}
fn multimodal(
    model: &Model,
    target: &Path,
    projector: &Path,
    bins: &Binaries,
    out: &Path,
    split: u32,
) -> DynResult<()> {
    let mut env = bins.environment();
    env.extend([
        ("SKIPPY_MM_MODEL", target.display().to_string()),
        ("SKIPPY_MM_PROJECTOR", projector.display().to_string()),
        (
            "SKIPPY_MM_IMAGE",
            bins.root
                .join("ci/llama-canary/fixtures/multimodal-smoke.png")
                .display()
                .to_string(),
        ),
        (
            "SKIPPY_MM_ACTIVATION_WIDTH",
            model.execution.activation_width.to_string(),
        ),
        ("SKIPPY_MM_SPLIT_LAYER", split.to_string()),
        ("SKIPPY_MM_CTX_SIZE", "2048".into()),
        ("SKIPPY_MM_MAX_TOKENS", "256".into()),
        (
            "SKIPPY_MM_N_GPU_LAYERS",
            if bins.backend == "cpu" { "0" } else { "999" }.into(),
        ),
    ]);
    for name in [
        "real_multimodal_local_smoke_when_fixture_is_set",
        "real_multimodal_local_prefill_failure_releases_lane_when_fixture_is_set",
        "real_multimodal_length_limit_then_next_image_uses_clean_lane_when_fixture_is_set",
        "real_multimodal_split_smoke_when_fixture_is_set",
    ] {
        let args = execution::strings(&[
            &format!("frontend::tests::multimodal::{name}"),
            "--exact",
            "--nocapture",
            "--test-threads=1",
        ]);
        let path = out.join(name);
        super::logged(&bins.test.display().to_string(), &args, &env, &path, 7200)?;
        let stdout = std::fs::read_to_string(path.with_extension("stdout.log"))?;
        let stderr = std::fs::read_to_string(path.with_extension("stderr.log"))?;
        if !stdout.contains("1 passed") || stderr.contains("skipping") {
            return Err("multimodal qualification test did not execute".into());
        }
    }
    Ok(())
}
fn offloaded(root: &Path) -> DynResult<bool> {
    for entry in std::fs::read_dir(root)? {
        let entry = entry?;
        let path = entry.path();
        if entry.file_type()?.is_dir() {
            if offloaded(&path)? {
                return Ok(true);
            }
        } else if path.extension().is_some_and(|s| s == "log") {
            let bytes = std::fs::read(&path)?;
            for line in String::from_utf8_lossy(&bytes).lines() {
                if let Some(after) = line.split("offloaded ").nth(1)
                    && after
                        .split('/')
                        .next()
                        .and_then(|n| n.parse::<usize>().ok())
                        .is_some_and(|n| n > 0)
                    && after.contains("GPU")
                {
                    return Ok(true);
                }
            }
        }
    }
    Ok(false)
}
#[cfg(test)]
mod tests {
    #[test]
    fn topology_rejects_missing_and_unapproved_boundaries() {
        let plan = super::Topology {
            boundaries: vec![
                super::Boundary {
                    layer: 1,
                    decision: "accepted".into(),
                },
                super::Boundary {
                    layer: 2,
                    decision: "rejected".into(),
                },
            ],
            two_stage_splits: vec![1],
            three_stage_splits: vec![1, 2],
        };
        assert!(super::validate_topology(&plan, 3).is_err());
    }
}
