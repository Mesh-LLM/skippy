mod artifact;
mod automation;
mod ci;
mod command;
mod execution;
mod fixture_paths;
mod inference;
mod native;
mod release;
mod upgrade;
use process_supervision as process;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Err(error) = run(&args) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

fn run(args: &[String]) -> command::DynResult<()> {
    let Some((domain, args)) = args.split_first() else {
        return help();
    };
    match domain.as_str() {
        "native" => native::run(args),
        "automation" => automation::run(args),
        "artifact" => artifact::run(args),
        "ci" => ci::run(args),
        "inference" => inference::run(args),
        "release" => release::run(args),
        "upgrade" => upgrade::run(args),
        "--help" | "-h" => help(),
        _ => Err(format!("unknown automation domain: {domain}").into()),
    }
}

fn help() -> command::DynResult<()> {
    println!("cargo xtool {{native|automation|artifact|ci|inference|release}} COMMAND [OPTIONS]");
    Ok(())
}
