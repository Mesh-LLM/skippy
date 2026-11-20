use std::ffi::OsString;

use super::*;
use crate::{cli::Cli, serve_settings::parse_with_environment};

fn parse(
    contents: &str,
    overrides: &[&str],
    environment: impl Fn(&str) -> Option<String>,
) -> Result<(tempfile::TempDir, Cli)> {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("serve.toml");
    std::fs::write(&path, contents)?;
    let args = [
        OsString::from("skippy"),
        OsString::from("serve"),
        OsString::from("--settings"),
        path.into_os_string(),
    ]
    .into_iter()
    .chain(overrides.iter().map(OsString::from));
    Ok((directory, parse_with_environment(args, environment)?))
}

#[test]
fn serve_resolves_multiple_local_models_from_one_settings_file() {
    let (directory, cli) = parse(
        r#"
version = 1
bind_addr = "127.0.0.1:8080"

[runtime]
runtime_bundle = ["runtime"]

[defaults]
ctx_size = 4096
threads = 2
temperature = 0.2

[[models]]
id = "small"
path = "models/small.gguf"

[models.settings]
ctx_size = 2048
temperature = 0.5

[[models]]
id = "large"
path = "models/large.gguf"
"#,
        &["--temperature", "0.8", "--print-effective-config"],
        |name| match name {
            "SKIPPY_SERVE_BIND_ADDR" => Some("127.0.0.1:0".into()),
            "SKIPPY_SERVE_THREADS" => Some("4".into()),
            _ => None,
        },
    )
    .unwrap();

    assert_eq!(
        cli.native_runtime.bundle_dirs,
        [directory.path().join("runtime")]
    );
    let Command::Serve(args) = cli.command else {
        panic!("expected serve");
    };
    assert!(args.print_effective_config);
    assert_eq!(args.public.bind_addr.unwrap().port(), 0);
    assert_eq!(args.local_models.len(), 2);
    for (model, id, ctx_size) in [
        (&args.local_models[0], "small", 2048),
        (&args.local_models[1], "large", 4096),
    ] {
        assert_eq!(model.model_id.as_deref(), Some(id));
        assert_eq!(
            model.model_path,
            Some(directory.path().join(format!("models/{id}.gguf")))
        );
        assert_eq!(model.ctx_size, Some(ctx_size));
        assert_eq!(model.settings.values["threads"], 4);
        assert_eq!(model.settings.values["temperature"], 0.8);
        assert_eq!(model.settings.sources["temperature"], "cli");
    }
}

#[test]
fn serve_rejects_duplicate_ids_and_conflicting_launch_modes() {
    let file = "version = 1\n[[models]]\nid = 'small'\npath = 'small.gguf'\n";
    let duplicate = format!("{file}[[models]]\nid = 'small'\npath = 'other.gguf'\n");
    let error = parse(&duplicate, &[], |_| None).err().unwrap();
    assert!(error.to_string().contains("duplicate served model ID"));

    for flags in [
        &["--model", "other.gguf"][..],
        &["--model-id", "other"],
        &["--config", "stage.json"],
        &["--prompt"],
    ] {
        let error = parse(file, flags, |_| None).err().unwrap();
        assert!(
            error
                .to_string()
                .contains("cannot be used with a multi-model settings file")
        );
    }
}
