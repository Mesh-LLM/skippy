use super::{
    DynResult,
    contract::{Host, Hosts},
    evaluate,
    identity::Checkout,
};
use crate::cache_qualification::{
    Backend, MANIFEST, read_manifest,
    verify::{self, VerifiedMatrix},
};
use serde::Serialize;
use std::{fs, io::Write, path::Path};

#[derive(Serialize)]
struct VerifiedInputs<'a> {
    schema_version: u32,
    source: &'a str,
    llama_pin: &'a str,
    patch_queue_sha256: &'a str,
    abi_version: &'a str,
    cache_format_version: u32,
    phase_one_manifest_sha256: &'a str,
    parity_plan_sha256: &'a str,
    parity_evidence_sha256: &'a str,
    metal: VerifiedBackend<'a>,
    cuda: VerifiedBackend<'a>,
}

#[derive(Serialize)]
struct VerifiedBackend<'a> {
    parity_host: &'a Host,
    correctness: VerifiedMatrix,
}

pub(super) fn run(
    plan: &Path,
    evidence: &Path,
    raw: &Path,
    metal: &Path,
    cuda: &Path,
    output: &Path,
) -> DynResult<()> {
    let verification = evaluate(plan, evidence, raw, true)?;
    verification.require_pass()?;
    let source = &verification.plan.source_sha;
    let Hosts {
        metal: metal_host,
        cuda: cuda_host,
    } = &verification.plan.backends;
    let inputs = VerifiedInputs {
        schema_version: 1,
        source,
        llama_pin: &verification.plan.llama_pin,
        patch_queue_sha256: &verification.plan.patch_queue_sha256,
        abi_version: &verification.plan.abi_version,
        cache_format_version: verification.plan.cache_format_version,
        phase_one_manifest_sha256: &verification.plan.model_manifest_sha256,
        parity_plan_sha256: &verification.plan_sha256,
        parity_evidence_sha256: &verification.evidence_sha256,
        metal: verify_backend(Backend::Metal, metal, metal_host, source)?,
        cuda: verify_backend(Backend::Cuda, cuda, cuda_host, source)?,
    };
    let manifest = fs::read_to_string(MANIFEST)?;
    let checkout = Checkout::load(&manifest)?;
    verification
        .plan
        .validate(&read_manifest(&manifest)?, &checkout)?;
    if !checkout.source_tree_clean {
        return Err("source checkout changed while verifying cache qualification inputs".into());
    }
    let parent = output
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let mut staged = tempfile::NamedTempFile::new_in(parent)?;
    serde_json::to_writer_pretty(&mut staged, &inputs)?;
    writeln!(staged)?;
    staged.persist_noclobber(output)?;
    Ok(())
}

fn verify_backend<'a>(
    backend: Backend,
    directory: &Path,
    host: &'a Host,
    source: &str,
) -> DynResult<VerifiedBackend<'a>> {
    let correctness = verify::assess(
        backend,
        source,
        directory,
        Some(&host.baseline_binary_sha256),
    )?;
    Ok(VerifiedBackend {
        parity_host: host,
        correctness,
    })
}
