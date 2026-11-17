//! Rebuild derived model annotations from the ungenerated core/model-support tree.
use crate::{command::DynResult, execution};
use std::path::{Path, PathBuf};
pub(super) fn run(checkout: &Path) -> DynResult<()> {
    let scratch = checkout.join("target/llama-regeneration");
    std::fs::create_dir_all(&scratch)?;
    let patches = scratch.join("core-patches");
    copy_tree(&checkout.join("llama_cpp/patches"), &patches, true)?;
    let source = checkout.join(".deps/llama.cpp");
    let native = scratch.join("native");
    let mut env = vec![
        ("LLAMA_WORKDIR", source.display().to_string()),
        (
            "LLAMA_PIN_FILE",
            checkout
                .join("llama_cpp/upstream.txt")
                .display()
                .to_string(),
        ),
        ("LLAMA_PATCH_DIR", patches.display().to_string()),
        ("LLAMA_STAGE_BUILD_DIR", native.display().to_string()),
        ("LLAMA_BUILD_DIR", native.display().to_string()),
        ("LLAMA_STAGE_BACKEND", "cpu".into()),
        ("LLAMA_STAGE_LINK_MODE", "static".into()),
        (
            "SKIPPY_AUTOMATION_BIN",
            std::env::current_exe()?.display().to_string(),
        ),
    ];
    let just = checkout.join("Justfile").display().to_string();
    execution::run(
        "just",
        &execution::strings(&["--justfile", &just, "llama-build"]),
        &env,
        7200,
    )?;
    let base = execution::output(
        "git",
        &execution::strings(&["-C", &source.display().to_string(), "rev-parse", "HEAD"]),
    )?
    .trim()
    .to_owned();
    let llvm = PathBuf::from(
        std::env::var_os("SKIPPY_REWRITER_LLVM_PREFIX")
            .ok_or("SKIPPY_REWRITER_LLVM_PREFIX must select the pinned LLVM/Clang installation")?,
    )
    .canonicalize()?;
    let tool = scratch.join("rewriter");
    execution::run(
        "cmake",
        &execution::strings(&[
            "-S",
            &checkout
                .join("scripts/tools/skippy-stage-rewriter")
                .display()
                .to_string(),
            "-B",
            &tool.display().to_string(),
            "-G",
            "Ninja",
            &format!("-DLLVM_DIR={}", llvm.join("lib/cmake/llvm").display()),
            &format!("-DClang_DIR={}", llvm.join("lib/cmake/clang").display()),
        ]),
        &[],
        300,
    )?;
    execution::run(
        "cmake",
        &execution::strings(&["--build", &tool.display().to_string(), "--parallel"]),
        &[],
        3600,
    )?;
    execution::run(
        "ctest",
        &execution::strings(&[
            "--test-dir",
            &tool.display().to_string(),
            "--output-on-failure",
            "--no-tests=error",
        ]),
        &[],
        600,
    )?;
    generate(checkout, &scratch, &source, &native, &tool, &llvm, &base)?;
    env.retain(|(key, _)| *key != "LLAMA_PATCH_DIR");
    execution::run(
        "just",
        &execution::strings(&["--justfile", &just, "llama-build"]),
        &env,
        7200,
    )?;
    env.push(("SKIPPY_FFI_BINDGEN", "regen".into()));
    execution::run(
        "just",
        &execution::strings(&[
            "--justfile",
            &just,
            "with-lld",
            "cargo",
            "check",
            "--locked",
            "-p",
            "skippy-ffi",
        ]),
        &env,
        3600,
    )?;
    crate::automation::run(&execution::strings(&[
        "native-generator",
        "contracts",
        "api-doc",
        "--include-dir",
        &source.join("include/skippy").display().to_string(),
        "--output",
        &checkout.join("docs/native-api.md").display().to_string(),
    ]))
}
fn generate(
    checkout: &Path,
    scratch: &Path,
    source: &Path,
    native: &Path,
    tool: &Path,
    llvm: &Path,
    base: &str,
) -> DynResult<()> {
    let shard = scratch.join("generated");
    let report = scratch.join("report.json");
    let resource = execution::output(
        &llvm.join("bin/clang").display().to_string(),
        &execution::strings(&["-print-resource-dir"]),
    )?;
    let mut args = execution::strings(&[
        "native-generator",
        "generate",
        "--git",
        &execution::executable("git")?.display().to_string(),
        "--source-root",
        &source.display().to_string(),
        "--build-dir",
        &native.display().to_string(),
        "--rewriter",
        &tool.join("skippy-stage-rewriter").display().to_string(),
        "--report",
        &report.display().to_string(),
        "--diff-base",
        base,
        "--output",
        &scratch.join("generated.patch").display().to_string(),
        "--shard-output-dir",
        &shard.display().to_string(),
        "--family-source-map",
        &checkout
            .join("ci/llama-canary/generated-family-map.json")
            .display()
            .to_string(),
        "--family-manifest",
        &checkout
            .join("ci/llama-canary/family-certified.json")
            .display()
            .to_string(),
        "--max-diff-bytes",
        "16777216",
        "--extra-arg",
        "-resource-dir",
        "--extra-arg",
        resource.trim(),
    ]);
    if cfg!(target_os = "macos") {
        let sdk = execution::output("xcrun", &execution::strings(&["--show-sdk-path"]))?;
        args.extend(execution::strings(&[
            "--extra-arg",
            "-isysroot",
            "--extra-arg",
            sdk.trim(),
        ]));
    }
    crate::automation::run(&args)?;
    crate::automation::run(&execution::strings(&[
        "native-generator",
        "contracts",
        "coverage",
        "--manifest",
        &checkout
            .join("ci/llama-canary/family-certified.json")
            .display()
            .to_string(),
        "--family-map",
        &checkout
            .join("ci/llama-canary/generated-family-map.json")
            .display()
            .to_string(),
        "--report",
        &report.display().to_string(),
    ]))?;
    let destination = checkout.join("llama_cpp/patches/generated");
    std::fs::remove_dir_all(&destination)?;
    copy_tree(&shard, &destination, false)
}
fn copy_tree(source: &Path, destination: &Path, without_generated: bool) -> DynResult<()> {
    std::fs::create_dir_all(destination)?;
    for entry in std::fs::read_dir(source)? {
        let entry = entry?;
        if without_generated && entry.file_name() == "generated" {
            continue;
        }
        let kind = entry.file_type()?;
        let dest = destination.join(entry.file_name());
        if kind.is_dir() {
            copy_tree(&entry.path(), &dest, false)?;
        } else if kind.is_file() {
            std::fs::copy(entry.path(), dest)?;
        } else {
            return Err("native patch input must contain regular files only".into());
        }
    }
    Ok(())
}
