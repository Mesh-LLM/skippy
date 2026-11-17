//! Native ABI versions, binary imports and runtime packaging contracts.
mod dependencies;
mod imports;
mod library_aliases;
mod manifest_identity;
mod package;
mod package_version;
mod runtime_manifest_writer;
use crate::command::DynResult;

pub(crate) fn run(args: &[String]) -> DynResult<()> {
    let (command, args) = args.split_first().ok_or("missing native command")?;
    match command.as_str() {
        "runtime-manifest-write" => runtime_manifest_writer::run(args),
        "package-source-version" => source_version(args),
        "verify-host-dependencies" => imports::verify_host(args),
        "linux-runtime-deps" => dependencies::run(args, "elf"),
        "windows-runtime-deps" => dependencies::run(args, "pe"),
        "deduplicate-linux-libraries" => library_aliases::run(args),
        "verify-runtime-package" => package::run(args),
        "skippy-cli-input" => imports::verify_cli_input(args),
        "cargo-target-directory" => cargo_target_directory(args),
        _ => Err(format!("unknown native command: {command}").into()),
    }
}

fn cargo_target_directory(args: &[String]) -> DynResult<()> {
    if !args.is_empty() {
        return Err("cargo-target-directory reads Cargo metadata from stdin".into());
    }
    #[derive(serde::Deserialize)]
    struct Metadata {
        target_directory: std::path::PathBuf,
    }
    let value: Metadata = serde_json::from_reader(std::io::stdin().lock())?;
    if !value.target_directory.is_absolute() {
        return Err("Cargo target directory must be absolute".into());
    }
    println!("{}", value.target_directory.display());
    Ok(())
}

fn source_version(args: &[String]) -> DynResult<()> {
    let [kind, path] = args else {
        return Err("package-source-version {workspace|runtime|abi} FILE".into());
    };
    let bytes = package_version::read_source(std::path::Path::new(path))?;
    let version = match kind.as_str() {
        "workspace" => package_version::workspace_version(&bytes)?.to_string(),
        "runtime" => package_version::runtime_version(&bytes)?,
        "abi" => package_version::abi_version(&bytes)?.to_string(),
        _ => return Err(format!("unknown source version: {kind}").into()),
    };
    println!("{version}");
    Ok(())
}
