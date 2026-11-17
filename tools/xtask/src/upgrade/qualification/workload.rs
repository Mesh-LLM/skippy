use super::build::Binaries;
use crate::{command::DynResult, execution, upgrade::evidence::Model};
use std::path::Path;
pub(super) fn run(
    model: &Model,
    target: &Path,
    projector: Option<&Path>,
    bins: &Binaries,
    out: &Path,
) -> DynResult<()> {
    if bins.backend != "cpu" {
        return Err("independent workload oracle requires the CPU source build".into());
    }
    let lane = format!("{}-smoke", model.class.replace('_', "-"));
    let model_id = format!("{}:{}", model.artifact.repo, model.artifact.selector);
    let mut args = execution::strings(&[
        "scripts/skippy-workload-certify.sh",
        "--class",
        &model.class,
        "--lane",
        &lane,
        "--model-path",
        &target.display().to_string(),
        "--model-id",
        &model_id,
        "--work-dir",
        &out.display().to_string(),
        "--require-oracle",
        "--startup-timeout-secs",
        "900",
    ]);
    if let Some(projector) = projector {
        args.extend(execution::strings(&[
            "--projector-path",
            &projector.display().to_string(),
        ]));
    }
    let (option, binary) = match model.class.as_str() {
        "encoder_decoder" => ("--oracle-completion", "llama-completion"),
        "speech_synthesis" => ("--oracle-tts", "llama-tts"),
        _ => ("--oracle-server", "llama-server"),
    };
    args.extend(execution::strings(&[
        option,
        &bins.native.join("bin").join(binary).display().to_string(),
    ]));
    let mut env = bins.environment();
    env.extend([
        (
            "SKIPPY_WORKLOAD_NATIVE_BUILD_DIR",
            bins.native.display().to_string(),
        ),
        (
            "SKIPPY_WORKLOAD_CANDIDATE_BIN_DIR",
            bins.bin.display().to_string(),
        ),
        (
            "SKIPPY_UPGRADE_SERVING_TEST_BIN",
            bins.test.display().to_string(),
        ),
        (
            "SKIPPY_WORKLOAD_LAYER_END",
            model.execution.trunk_layers.to_string(),
        ),
        ("SKIPPY_WORKLOAD_N_GPU_LAYERS", "0".into()),
    ]);
    super::logged("bash", &args, &env, &out.join("workload"), 7200)?;
    let log = std::fs::read_to_string(out.join("workload.stdout.log"))?;
    if !log.contains("1 passed") || !log.contains("local-monolithic oracle passed") {
        return Err(
            "workload qualification did not execute both fixture smoke and independent oracle"
                .into(),
        );
    }
    Ok(())
}
