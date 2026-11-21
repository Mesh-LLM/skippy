mod artifact;
mod cache_parity;
mod cache_qualification;
mod command;
mod execution;
mod huggingface;

fn main() -> std::process::ExitCode {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let result = match args.split_first() {
        Some((domain, rest)) if domain == "cache-parity" => cache_parity::run(rest),
        _ => cache_qualification::run(&args),
    };
    match result {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}");
            std::process::ExitCode::FAILURE
        }
    }
}
