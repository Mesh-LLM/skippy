use std::{
    collections::BTreeSet,
    net::SocketAddr,
    path::{Path, PathBuf},
};

use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::Value;
use skippy_inference_api::ModelId;

use super::{ServeSettings, file_value, known_arguments, resolve, section};
use crate::cli::{Command, ServeOpenAiArgs};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    version: u32,
    bind_addr: Option<SocketAddr>,
    #[serde(default)]
    runtime: toml::Table,
    #[serde(default)]
    defaults: toml::Table,
    models: Vec<Model>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Model {
    id: String,
    path: PathBuf,
    #[serde(default)]
    settings: toml::Table,
}

pub(super) struct SettingsFile {
    path: PathBuf,
    defaults: toml::Table,
    models: Vec<Model>,
}

impl SettingsFile {
    pub(super) fn parse(
        path: &Path,
        table: toml::Table,
        command: &clap::Command,
        listener: &mut ServeSettings,
    ) -> Result<Self> {
        let config: Config = toml::Value::Table(table)
            .try_into()
            .with_context(|| format!("parse multi-model settings {}", path.display()))?;
        ensure!(
            config.version == 1,
            "multi-model settings version must be 1"
        );
        ensure!(!config.models.is_empty(), "provide at least one model");
        let mut ids = BTreeSet::new();
        for model in &config.models {
            ModelId::new(&model.id)?;
            ensure!(
                ids.insert(&model.id),
                "duplicate served model ID {:?}",
                model.id
            );
            ensure!(
                !model.path.as_os_str().is_empty(),
                "model {:?} has an empty path",
                model.id
            );
        }

        let mut shared = toml::Table::new();
        shared.insert("runtime".into(), toml::Value::Table(config.runtime));
        listener.load_file(path, shared, command)?;
        if let Some(bind) = config.bind_addr {
            listener.insert(
                "bind-addr",
                Value::String(bind.to_string()),
                format!("file:{}:bind_addr", path.display()),
            )?;
        }
        Ok(Self {
            path: path.to_path_buf(),
            defaults: config.defaults,
            models: config.models,
        })
    }

    pub(super) fn resolve(
        self,
        command: &clap::Command,
        overrides: &ServeSettings,
    ) -> Result<Vec<ServeOpenAiArgs>> {
        let known = known_arguments(command);
        let directory = self.path.parent().unwrap_or(Path::new("."));
        let mut defaults = ServeSettings::default();
        for name in overrides.values.keys() {
            ensure!(
                process_setting(name) || per_model_setting(name),
                "--{name} cannot be used with a multi-model settings file"
            );
        }

        let apply_file = |settings: &mut ServeSettings, values: toml::Table, group: &str| {
            for (key, value) in values {
                let name = key.replace('_', "-");
                ensure!(known.contains_key(&name), "unknown setting {group}.{key}");
                ensure!(
                    per_model_setting(&name),
                    "{group}.{key} is not a per-model setting"
                );
                let value = file_value(&name, serde_json::to_value(value)?, directory)?;
                settings.insert(
                    &name,
                    value,
                    format!("file:{}:{group}.{key}", self.path.display()),
                )?;
            }
            Ok::<_, anyhow::Error>(())
        };
        apply_file(&mut defaults, self.defaults, "defaults")?;
        self.models
            .into_iter()
            .map(|model| {
                let mut settings = defaults.clone();
                let source = format!("models.{}", model.id);
                apply_file(&mut settings, model.settings, &format!("{source}.settings"))?;
                settings.values.extend(overrides.values.clone());
                settings.sources.extend(overrides.sources.clone());
                let path = directory.join(model.path);
                settings.insert(
                    "model-path",
                    Value::String(path.to_str().context("model path must be UTF-8")?.into()),
                    format!("file:{}:{source}.path", self.path.display()),
                )?;
                settings.insert(
                    "model-id",
                    Value::String(model.id),
                    format!("file:{}:{source}.id", self.path.display()),
                )?;
                let Command::Serve(args) = resolve(command, settings)?.command else {
                    unreachable!("resolved a serve command")
                };
                args.public.settings.validate_mode(&args)?;
                Ok(args.public)
            })
            .collect()
    }
}

fn process_setting(name: &str) -> bool {
    section(name) == "runtime"
        || matches!(
            name,
            "bind-addr" | "debug" | "help" | "output" | "print-effective-config" | "settings"
        )
}

fn per_model_setting(name: &str) -> bool {
    !process_setting(name)
        && !matches!(section(name), "diagnostics" | "distributed" | "network")
        && !matches!(name, "model" | "model-path" | "model-id" | "prompt")
}

#[cfg(test)]
mod tests;
