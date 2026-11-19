use super::{config, dependencies::Graph, recipes, rust};
use crate::command::DynResult;

#[derive(Clone, Copy)]
pub(super) enum Input {
    Rust,
    Planner,
    Json,
    Templates,
    Just,
    CargoConfig,
    Bytes,
}

pub(super) fn input(path: &str, graph: &Graph) -> Option<Input> {
    if graph.assets.contains(path) {
        return Some(Input::Bytes);
    }
    if documentation(path) {
        return None;
    }
    if graph.sources.contains(path) {
        return Some(Input::Rust);
    }
    match path {
        "tools/xtask/src/inference.rs" => return Some(Input::Planner),
        "tools/xtask/src/main.rs" => return Some(Input::Rust),
        ".gi/templates.yaml" => return Some(Input::Templates),
        "Justfile" | "justfile" => return Some(Input::Just),
        ".cargo/config.toml" | ".cargo/config" => return Some(Input::CargoConfig),
        "ci/inference-ladder.json"
        | "ci/inference-artifacts.json"
        | "ci/inference-quality.json" => return Some(Input::Json),
        "rust-toolchain"
        | "rust-toolchain.toml"
        | "scripts/build-llama.sh"
        | "scripts/prepare-llama.sh"
        | "scripts/prepare-whisper.sh"
        | "scripts/cargo-linker"
        | "scripts/cargo-linker-linux-aarch64"
        | "scripts/lib/cuda-toolkit.sh"
        | "scripts/lib/lld.sh"
        | "scripts/lib/macos-deployment-target.sh"
        | "scripts/lib/macos-deployment-target.txt" => return Some(Input::Bytes),
        _ => {}
    }
    if path.starts_with("llama_cpp/")
        || path.starts_with("skippy_api/src/")
        || path.starts_with("skippy_api/include/")
        || path == "skippy_api/CMakeLists.txt"
    {
        return Some(Input::Bytes);
    }
    for directory in &graph.directories {
        let Some(suffix) = path.strip_prefix(&format!("{directory}/")) else {
            continue;
        };
        if suffix
            .split('/')
            .any(|part| matches!(part, "tests" | "benches" | "examples" | "fuzz"))
            || suffix.ends_with(".rs")
            || suffix == "Cargo.toml"
        {
            return None;
        }
        if suffix.starts_with("src/")
            || suffix.starts_with("proto/")
            || suffix.starts_with("bindgen/wrapper/")
        {
            return Some(Input::Bytes);
        }
        return None;
    }
    None
}

pub(super) fn documentation(path: &str) -> bool {
    let name = path.rsplit('/').next().unwrap_or(path).to_ascii_lowercase();
    path.split('/').any(|part| matches!(part, "docs" | "doc"))
        || name.starts_with("readme")
        || [".md", ".mdx", ".rst", ".adoc"]
            .iter()
            .any(|ext| name.ends_with(ext))
}

pub(super) fn projection(kind: Input, text: &str) -> DynResult<String> {
    match kind {
        Input::Rust => rust::projection(text),
        Input::Planner => rust::planner_projection(text),
        Input::Json => config::json(text),
        Input::Templates => config::templates(text),
        Input::Just => recipes::projection(text),
        Input::CargoConfig => config::cargo_config(text),
        Input::Bytes => Ok(text.to_owned()),
    }
}
