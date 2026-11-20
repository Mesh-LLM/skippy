use std::{
    collections::{BTreeMap, BTreeSet},
    net::SocketAddr,
    path::{Path, PathBuf},
};

use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use skippy_api::serving::multi_model::{LocalModelDefinition, MultiModelServer};
use skippy_inference_api::ModelId;

use crate::cli::Command;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    version: u32,
    bind_addr: Option<SocketAddr>,
    #[serde(default)]
    defaults: BTreeMap<String, toml::Value>,
    models: Vec<Model>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Model {
    id: String,
    path: PathBuf,
    #[serde(default)]
    settings: BTreeMap<String, toml::Value>,
}

pub async fn run(path: PathBuf) -> Result<()> {
    let (bind, definitions) = tokio::task::spawn_blocking(move || prepare(&path)).await??;
    let server =
        tokio::task::spawn_blocking(move || MultiModelServer::load(definitions, None)).await??;
    let listener = tokio::net::TcpListener::bind(bind).await?;
    skippy_commands::console::status(&format!(
        "Serving local models at {}",
        listener.local_addr()?
    ))?;
    server.serve(listener, crate::shutdown_signal()?).await
}

fn prepare(path: &Path) -> Result<(SocketAddr, Vec<LocalModelDefinition>)> {
    let content =
        std::fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    let config: Config =
        toml::from_str(&content).with_context(|| format!("parse {}", path.display()))?;
    ensure!(config.version == 1, "multi-model config version must be 1");
    ensure!(!config.models.is_empty(), "provide at least one model");
    let mut ids = BTreeSet::new();
    for model in &config.models {
        ModelId::new(&model.id)?;
        ensure!(
            ids.insert(&model.id),
            "duplicate served model ID {:?}",
            model.id
        );
    }
    let directory = path.parent().unwrap_or(Path::new("."));
    let definitions = config
        .models
        .into_iter()
        .map(|model| {
            let mut settings = config.defaults.clone();
            settings.extend(model.settings);
            let file = directory.join(&model.path);
            let command =
                crate::serve_settings::parse_model_entry(directory, &file, &model.id, settings)
                    .with_context(|| format!("resolve serving settings for {}", model.id))?;
            let Command::Serve(args) = command.command else {
                unreachable!("constructed serve command")
            };
            args.public.settings.validate_mode(&args)?;
            let (_, request) =
                crate::conversion::local_openai_options(args.public)?.into_request()?;
            Ok(LocalModelDefinition {
                id: model.id,
                request,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    Ok((
        config
            .bind_addr
            .unwrap_or_else(crate::serve::default_public_bind_addr),
        definitions,
    ))
}
