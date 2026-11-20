use anyhow::{Context, Result};
use serde_json::json;
use skippy_api::serving::multi_model::{LocalModelDefinition, MultiModelServer};
use skippy_commands::console;

use crate::{
    cli::{ServeCommandArgs, ServeOpenAiArgs},
    conversion::local_openai_options,
    serve::default_public_bind_addr,
};

pub async fn run(args: ServeCommandArgs) -> Result<()> {
    let bind = args
        .public
        .bind_addr
        .unwrap_or_else(default_public_bind_addr);
    if args.print_effective_config {
        return print_effective_config(bind, args.local_models);
    }
    let server = tokio::task::spawn_blocking(move || {
        let definitions = args
            .local_models
            .into_iter()
            .map(|model| {
                let id = model.model_id.clone().context("missing served model ID")?;
                let (_, request) = local_openai_options(model)
                    .and_then(|options| options.into_request())
                    .with_context(|| format!("prepare model {id}"))?;
                Ok(LocalModelDefinition { id, request })
            })
            .collect::<Result<Vec<_>>>()?;
        MultiModelServer::load(definitions, None)
    })
    .await??;
    let listener = tokio::net::TcpListener::bind(bind).await?;
    console::status(&format!(
        "Serving local models at {}",
        listener.local_addr()?
    ))?;
    server.serve(listener, crate::shutdown_signal()?).await
}

fn print_effective_config(bind: std::net::SocketAddr, models: Vec<ServeOpenAiArgs>) -> Result<()> {
    let models = models
        .into_iter()
        .map(|model| {
            let settings = model.settings.clone();
            let options = local_openai_options(model)
                .with_context(|| format!("prepare model {:?}", settings.text("model-id")))?;
            let frontend = options.resolved_openai_options()?;
            Ok(json!({
                "id": options.model_id,
                "config": settings.report(&options.config, Some(&frontend), &options.tuning),
            }))
        })
        .collect::<Result<Vec<_>>>()?;
    console::write_json(&json!({
        "schema_version": 1,
        "resolution_phase": "before-model-load",
        "bind_addr": bind,
        "models": models,
    }))
}
