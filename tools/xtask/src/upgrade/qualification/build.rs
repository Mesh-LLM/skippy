use crate::{
    artifact,
    command::{DynResult, write_json_file},
    execution,
};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};
pub(super) struct Binaries {
    pub root: PathBuf,
    pub native: PathBuf,
    pub bin: PathBuf,
    pub test: PathBuf,
    pub backend: String,
}
impl Binaries {
    pub(super) fn environment(&self) -> Vec<(&'static str, String)> {
        vec![
            ("LLAMA_STAGE_BUILD_DIR", self.native.display().to_string()),
            ("LLAMA_STAGE_BACKEND", self.backend.clone()),
            ("LLAMA_STAGE_LINK_MODE", "static".into()),
            (
                "SKIPPY_AUTOMATION_BIN",
                std::env::current_exe()
                    .expect("running executable")
                    .display()
                    .to_string(),
            ),
            ("HF_HUB_OFFLINE", "1".into()),
        ]
    }
}
pub(super) fn prepare(root: &Path, backend: &str, output: &Path) -> DynResult<Binaries> {
    let native = root.join("target/llama-upgrade/native").join(backend);
    let cargo = root.join("target/llama-upgrade/cargo").join(backend);
    if native.exists() || cargo.exists() {
        return Err("qualification native and Cargo directories must be absent; every pass rebuilds independently".into());
    }
    let mut environment = vec![
        (
            "LLAMA_WORKDIR",
            root.join(".deps/llama.cpp").display().to_string(),
        ),
        (
            "LLAMA_PIN_FILE",
            root.join("llama_cpp/upstream.txt").display().to_string(),
        ),
        (
            "LLAMA_PATCH_DIR",
            root.join("llama_cpp/patches").display().to_string(),
        ),
        ("LLAMA_STAGE_BACKEND", backend.into()),
        ("LLAMA_STAGE_LINK_MODE", "static".into()),
        ("LLAMA_STAGE_BUILD_DIR", native.display().to_string()),
        ("LLAMA_BUILD_DIR", native.display().to_string()),
        ("LLAMA_STAGE_BUILD_TESTS", "ON".into()),
        ("LLAMA_STAGE_FULL_REPLAY", "ON".into()),
        (
            "LLAMA_STAGE_WORKLOAD_ORACLE",
            if backend == "cpu" { "ON" } else { "OFF" }.into(),
        ),
        ("CARGO_TARGET_DIR", cargo.display().to_string()),
        (
            "SKIPPY_AUTOMATION_BIN",
            std::env::current_exe()?.display().to_string(),
        ),
    ];
    super::logged(
        "just",
        &execution::strings(&["llama-build"]),
        &environment,
        &output.join(format!("native-build-{backend}")),
        7200,
    )?;
    crate::upgrade::source::prepared_head(root)?;
    super::logged(
        "bash",
        &execution::strings(&[
            "scripts/check-skippy-generated-family-patch.sh",
            "--full-native-suite",
        ]),
        &environment,
        &output.join(format!("native-check-{backend}")),
        7200,
    )?;
    super::logged(
        "just",
        &execution::strings(&[
            "with-lld",
            "cargo",
            "build",
            "--locked",
            "-p",
            "skippy-cli",
            "-p",
            "skippy-correctness",
            "-p",
            "skippy-package-builder",
            "-p",
            "skippy-topology",
            "--bins",
        ]),
        &environment,
        &output.join(format!("rust-build-{backend}")),
        7200,
    )?;
    let messages = execution::capture_for(
        "just",
        &execution::strings(&[
            "with-lld",
            "cargo",
            "test",
            "--locked",
            "-p",
            "skippy-serving",
            "--lib",
            "--no-run",
            "--message-format=json",
        ]),
        &environment,
        7200,
    )?;
    let mut tests = std::collections::BTreeSet::new();
    for line in messages.lines() {
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(line)
            && value["reason"] == "compiler-artifact"
            && value["profile"]["test"] == true
            && value["target"]["name"] == "skippy_serving"
            && let Some(path) = value["executable"].as_str()
        {
            tests.insert(PathBuf::from(path));
        }
    }
    if tests.len() != 1 {
        return Err("expected exactly one source-built serving test executable".into());
    }
    let test = tests.into_iter().next().ok_or("missing serving test")?;
    let bin = cargo.join("debug");
    let mut identities = BTreeMap::new();
    for name in [
        "skippy",
        "skippy-correctness",
        "skippy-package-builder",
        "skippy-topology-plan",
    ] {
        identities.insert(name.to_owned(), artifact::sha256(&bin.join(name))?);
    }
    identities.insert("skippy-serving-tests".into(), artifact::sha256(&test)?);
    if backend == "cpu" {
        for name in ["llama-server", "llama-completion", "llama-tts"] {
            identities.insert(
                name.into(),
                artifact::sha256(&native.join("bin").join(name))?,
            );
        }
    }
    write_json_file(
        &output.join(format!("executables-{backend}.json")),
        &identities,
    )?;
    environment.clear();
    Ok(Binaries {
        root: root.into(),
        native,
        bin,
        test,
        backend: backend.into(),
    })
}
