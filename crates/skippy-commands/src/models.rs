use anyhow::Result;

mod acquisition;
mod capabilities;
pub mod cli;
mod details;
mod dispatch;
mod download;
mod formatters;
mod formatters_console;
mod formatters_json;
mod handlers;
mod installed;
pub mod lifecycle;
mod output;
pub mod package;
mod progress;
mod search;
mod stage_cache;
mod storage;
mod transfer;
mod updates;
pub use download::{DownloadedModel, download_model, model_cache_dir};
pub use output::ModelCommandContext;

/// Reject explicit checkpoint references before resolving a model for serving.
pub fn reject_safetensors_model_ref(model_ref: &str) -> Result<()> {
    anyhow::ensure!(
        !skippy_model_ref::is_safetensors_model_ref(model_ref),
        "{model_ref}: {}",
        skippy_model_ref::SAFETENSORS_SERVING_GUIDANCE
    );
    Ok(())
}

/// Execute the shared model-command contract for a product's console.
pub async fn run(command: &cli::ModelsCommand, context: ModelCommandContext) -> Result<()> {
    output::scope(context, dispatch::dispatch_models_command(command)).await
}

pub async fn run_standalone(command: &cli::ModelsCommand) -> Result<()> {
    let mut command = command.clone();
    if crate::console::mode() != crate::console::OutputMode::Human {
        command.set_json();
    }
    run(&command, output::standalone_context()).await
}

pub async fn run_package(
    args: package::ModelPrepareArgs<'_>,
    context: ModelCommandContext,
) -> Result<()> {
    output::scope(context, package::dispatch_model_package(args)).await
}

#[cfg(test)]
mod tests;
