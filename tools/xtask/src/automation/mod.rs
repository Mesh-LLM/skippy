mod family_model_identity;
mod local_ports;
mod native_contracts;
mod native_generator;
mod rewriter_patch;
mod rewriter_report;
pub(crate) mod workload_smoke;
use crate::command::DynResult;
use process_supervision::interrupt as command_interrupt;

pub(crate) fn run(args: &[String]) -> DynResult<()> {
    match args {
        [command, rest @ ..] if command == "native-generator" => native_generator::run(rest),
        [command, rest @ ..] if command == "rewriter-report" => rewriter_report::run(rest),
        [command, rest @ ..] if command == "local-ports" => local_ports::run(rest),
        [command, rest @ ..] if command == "family-model-identity" => {
            family_model_identity::run(rest)
        }
        [command, rest @ ..] if command == "workload-smoke" => workload_smoke::run(rest),
        [command, rest @ ..] if command == "workload-monolithic-oracle" => {
            workload_smoke::comparison::run(rest)
        }
        [command, rest @ ..] if command == "workload-media-oracle" => {
            workload_smoke::media_comparison::run(rest)
        }
        [command, rest @ ..] if command == "workload-tts-oracle" => {
            workload_smoke::tts_oracle::run(rest)
        }
        _ => Err("automation {native-generator|rewriter-report} [OPTIONS]".into()),
    }
}
