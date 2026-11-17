pub(crate) mod artifacts;
pub(crate) use crate::execution;
mod contracts;
mod gates;
pub(crate) mod matrix;
mod producers;
mod profile;
mod smoke;
use crate::command::DynResult;
use std::{io::Write, path::Path};

pub fn run(args: &[String]) -> DynResult<()> {
    match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["plan"] => plan(),
        ["contracts"] => contracts::run(),
        ["host", id] => producers::host(id),
        ["runtime", id] => producers::runtime(id),
        ["package", id, host, runtime] => producers::package(id, Path::new(host), Path::new(runtime)),
        ["smoke", id, product] => smoke::run(id, Path::new(product)),
        ["model-fetch"] => smoke::prepare_model(),
        ["summary"] => summary(),
        ["runtime-summary"] => gates::run("runtime"),
        ["smoke-summary"] => gates::run("smoke"),
        _ => Err("usage: ci {plan|contracts|model-fetch|host ID|runtime ID|package ID HOST_DIR RUNTIME_DIR|smoke ID PRODUCT_DIR|summary|runtime-summary|smoke-summary}".into()),
    }
}
fn plan() -> DynResult<()> {
    let profile = profile::Profile::from_environment()?;
    let matrix = matrix::Matrix::load()?;
    let (hosts, runtimes) = project_matrix(&matrix)?;
    println!(
        "{}",
        serde_json::json!({"profile":profile.name(),"hosts": hosts, "runtimes": runtimes})
    );
    if let Some(path) = std::env::var_os("GITHUB_OUTPUT") {
        let mut file = std::fs::OpenOptions::new().append(true).open(path)?;
        writeln!(
            file,
            "profile={}\nhosts={hosts}\nruntimes={runtimes}",
            profile.name()
        )?;
    }
    Ok(())
}
fn project_matrix(matrix: &matrix::Matrix) -> DynResult<(serde_json::Value, serde_json::Value)> {
    let hosts = serde_json::json!({"include": matrix.hosts});
    let mut runtime_rows = Vec::new();
    for runtime in &matrix.runtimes {
        let mut value = serde_json::to_value(runtime)?;
        if let Some(group) = &runtime.runner_group {
            value["runner"] = serde_json::json!({"group":group,"labels":runtime.runner});
        }
        value["host_runner"] = serde_json::to_value(&matrix.host(&runtime.host)?.runner)?;
        runtime_rows.push(value);
    }
    let runtimes = serde_json::json!({"include": runtime_rows});
    Ok((hosts, runtimes))
}
fn summary() -> DynResult<()> {
    let needs: serde_json::Value = serde_json::from_str(&std::env::var("SKIPPY_CI_NEEDS")?)?;
    validate_summary(&needs, profile::Profile::from_environment()?)
}
fn validate_summary(needs: &serde_json::Value, profile: profile::Profile) -> DynResult<()> {
    let jobs = needs.as_object().ok_or("needs must be an object")?;
    let required = [
        "plan",
        "inference_contracts",
        "quality",
        "host",
        "runtime",
        "product",
        "smoke",
    ];
    if jobs.len() != required.len()
        || required.iter().any(|job| {
            let expected = if profile == profile::Profile::Pr
                && matches!(*job, "host" | "runtime" | "product" | "smoke")
            {
                "skipped"
            } else {
                "success"
            };
            jobs.get(*job)
                .is_none_or(|value| value["result"] != expected)
        })
    {
        return Err(format!("CI results do not satisfy the {} profile", profile.name()).into());
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn skipped_and_missing_jobs_fail_summary() {
        let mut jobs = serde_json::json!({"plan":{"result":"success"},"inference_contracts":{"result":"success"},"quality":{"result":"success"},"host":{"result":"success"},"runtime":{"result":"success"},"product":{"result":"success"},"smoke":{"result":"success"}});
        validate_summary(&jobs, profile::Profile::Full).unwrap();
        jobs["product"]["result"] = "skipped".into();
        assert!(validate_summary(&jobs, profile::Profile::Full).is_err());
        jobs.as_object_mut().unwrap().remove("product");
        assert!(validate_summary(&jobs, profile::Profile::Full).is_err());
    }
    #[test]
    fn short_profile_requires_quality_and_declared_extended_skips() {
        let jobs = serde_json::json!({"plan":{"result":"success"},"inference_contracts":{"result":"success"},"quality":{"result":"success"},"host":{"result":"skipped"},"runtime":{"result":"skipped"},"product":{"result":"skipped"},"smoke":{"result":"skipped"}});
        validate_summary(&jobs, profile::Profile::Pr).unwrap();
        assert!(validate_summary(&jobs, profile::Profile::Full).is_err());
        for job in ["plan", "inference_contracts", "quality"] {
            for result in ["skipped", "failure", "cancelled"] {
                let mut changed = jobs.clone();
                changed[job]["result"] = result.into();
                assert!(validate_summary(&changed, profile::Profile::Pr).is_err());
            }
        }
        let mut missing = jobs.clone();
        missing.as_object_mut().unwrap().remove("runtime");
        assert!(validate_summary(&missing, profile::Profile::Pr).is_err());
        let mut unexpected = jobs;
        unexpected["runtime"]["result"] = "success".into();
        assert!(validate_summary(&unexpected, profile::Profile::Pr).is_err());
    }
    #[test]
    fn every_product_uses_its_hosted_target_runner() {
        let matrix =
            matrix::Matrix::from_json(include_str!("../../../../ci/targets.json")).unwrap();
        let (_, runtimes) = project_matrix(&matrix).unwrap();
        let rows = runtimes["include"].as_array().unwrap();
        assert_eq!(rows.len(), 8);
        for row in rows {
            let host = matrix.host(row["host"].as_str().unwrap()).unwrap();
            assert_eq!(
                row["host_runner"],
                serde_json::to_value(&host.runner).unwrap()
            );
            if row["backend"] != "cpu" {
                assert_ne!(row["host_runner"], row["runner"]);
                let runtime = matrix.runtime(row["id"].as_str().unwrap()).unwrap();
                assert_eq!(
                    row["runner"]["group"],
                    runtime.runner_group.as_ref().unwrap().as_str()
                );
                assert_eq!(row["runner"]["labels"], serde_json::json!(runtime.runner));
            }
        }
    }
}
