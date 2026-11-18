//! Verify paired cache benchmark evidence against an independently supplied plan.

use crate::{
    artifact,
    cache_qualification::{MANIFEST, read_manifest},
    command::DynResult,
};
use decision::Outcome;
use std::{fs, path::Path};

mod admission;
mod capture;
mod contract;
mod decision;
mod identity;
mod raw;
#[cfg(test)]
mod tests;
mod workload;

pub(crate) fn run(args: &[String]) -> DynResult<()> {
    match args {
        [command] if command == "plan-identity" => identity::Checkout::print(),
        [command, plan, evidence, raw]
            if matches!(command.as_str(), "verify" | "verify-diagnostic") =>
        {
            verify(plan, evidence, raw, command == "verify")
        }
        [command, plan, evidence, raw, metal, cuda, output] if command == "verify-tier-a-inputs" => {
            admission::run(Path::new(plan), Path::new(evidence), Path::new(raw), Path::new(metal), Path::new(cuda), Path::new(output))
        }
        [command, address, request, output] if command == "capture-request" => {
            capture::run(address.parse()?, Path::new(request), Path::new(output))
        }
        [command, config, request, output] if command == "capture-pairs" => {
            capture::pair::run(Path::new(config), Path::new(request), Path::new(output))
        }
        [command, config, workload, output] if command == "capture-trace" => {
            capture::pair::run_trace(Path::new(config), Path::new(workload), Path::new(output))
        }
        [command, model, state, scenario, width, output] if command == "make-workload" => {
            workload::run(model, state, scenario, width.parse()?, Path::new(output))
        }
        [command, plan, cell, capture, output] if command == "export-trace" => {
            raw::export::run(Path::new(plan), Path::new(cell), Path::new(capture), Path::new(output))
        }
        [command, path] if command == "summarize-trial" => {
            println!(
                "{}",
                serde_json::to_string_pretty(&raw::summarize(Path::new(path))?)?
            );
            Ok(())
        }
        _ => Err(
            "cache-parity {plan-identity|verify|verify-diagnostic|verify-tier-a-inputs|summarize-trial|capture-request|capture-pairs|capture-trace|make-workload|export-trace} [ARGUMENTS]"
                .into(),
        ),
    }
}

fn verify(
    plan_path: &str,
    evidence_path: &str,
    raw_dir: &str,
    require_clean: bool,
) -> DynResult<()> {
    let verified = evaluate(
        Path::new(plan_path),
        Path::new(evidence_path),
        Path::new(raw_dir),
        require_clean,
    )?;
    println!("{}", serde_json::to_string_pretty(&verified.summary)?);
    verified.require_pass()
}

struct Evaluation {
    plan: contract::Plan,
    plan_sha256: String,
    evidence_sha256: String,
    summary: decision::Summary,
}

impl Evaluation {
    fn require_pass(&self) -> DynResult<()> {
        if self.summary.outcome != Outcome::Pass {
            return Err(
                format!("cache parity evidence outcome: {:?}", self.summary.outcome).into(),
            );
        }
        Ok(())
    }
}

fn evaluate(
    plan_path: &Path,
    evidence_path: &Path,
    raw_dir: &Path,
    require_clean: bool,
) -> DynResult<Evaluation> {
    let manifest = fs::read_to_string(MANIFEST)?;
    let checkout = identity::Checkout::load(&manifest)?;
    let plan_bytes = fs::read(plan_path)?;
    let plan: contract::Plan = serde_json::from_slice(&plan_bytes)?;
    plan.validate(&read_manifest(&manifest)?, &checkout)?;
    if !checkout.source_tree_clean {
        if require_clean {
            return Err("cache parity verification requires a clean source checkout".into());
        }
        eprintln!("Replaying diagnostic parity evidence against an uncommitted checkout");
    }
    let evidence_bytes = fs::read(evidence_path)?;
    let evidence = serde_json::from_slice(&evidence_bytes)?;
    let plan_sha = artifact::digest(plan_bytes.as_slice())?;
    let source = raw::ArtifactSource {
        directory: raw_dir,
        plan_sha: &plan_sha,
    };
    let summary = decision::verify(&plan, &plan_sha, &evidence, |row, pair, trial| {
        source.check_trial(row, pair, trial)
    })?;
    Ok(Evaluation {
        plan,
        plan_sha256: plan_sha,
        evidence_sha256: artifact::digest(&evidence_bytes[..])?,
        summary,
    })
}
