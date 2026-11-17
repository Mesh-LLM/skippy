pub(crate) mod artifacts;
pub(crate) use crate::execution;
mod contracts;
pub(crate) mod matrix;
mod producers;
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
        _ => Err("usage: ci {plan|contracts|model-fetch|host ID|runtime ID|package ID HOST_DIR RUNTIME_DIR|smoke ID PRODUCT_DIR|summary}".into()),
    }
}
fn plan() -> DynResult<()> {
    let matrix = matrix::Matrix::load()?;
    let (hosts, runtimes) = project_matrix(&matrix)?;
    println!(
        "{}",
        serde_json::json!({"hosts": hosts, "runtimes": runtimes})
    );
    if let Some(path) = std::env::var_os("GITHUB_OUTPUT") {
        let mut file = std::fs::OpenOptions::new().append(true).open(path)?;
        writeln!(file, "hosts={hosts}\nruntimes={runtimes}")?;
    }
    Ok(())
}
fn project_matrix(matrix: &matrix::Matrix) -> DynResult<(serde_json::Value, serde_json::Value)> {
    let hosts = serde_json::json!({"include": matrix.hosts});
    let mut runtime_rows = Vec::new();
    for runtime in &matrix.runtimes {
        let mut value = serde_json::to_value(runtime)?;
        value["host_runner"] = serde_json::to_value(&matrix.host(&runtime.host)?.runner)?;
        runtime_rows.push(value);
    }
    let runtimes = serde_json::json!({"include": runtime_rows});
    Ok((hosts, runtimes))
}
fn summary() -> DynResult<()> {
    let needs: serde_json::Value = serde_json::from_str(&std::env::var("SKIPPY_CI_NEEDS")?)?;
    validate_summary(&needs)
}
fn validate_summary(needs: &serde_json::Value) -> DynResult<()> {
    let jobs = needs.as_object().ok_or("needs must be an object")?;
    let required = ["plan", "quality", "host", "runtime", "product", "smoke"];
    if jobs.len() != required.len()
        || required.iter().any(|job| {
            jobs.get(*job)
                .is_none_or(|value| value["result"] != "success")
        })
    {
        return Err(
            "CI requires successful plan, quality, all hosts, runtimes, and product smokes".into(),
        );
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn skipped_and_missing_jobs_fail_summary() {
        let mut jobs = serde_json::json!({"plan":{"result":"success"},"quality":{"result":"success"},"host":{"result":"success"},"runtime":{"result":"success"},"product":{"result":"success"},"smoke":{"result":"success"}});
        validate_summary(&jobs).unwrap();
        jobs["product"]["result"] = "skipped".into();
        assert!(validate_summary(&jobs).is_err());
        jobs.as_object_mut().unwrap().remove("product");
        assert!(validate_summary(&jobs).is_err());
    }
    #[test]
    fn every_product_uses_its_hosted_target_runner() {
        let matrix: matrix::Matrix =
            serde_json::from_str(include_str!("../../../../ci/targets.json")).unwrap();
        let (_, runtimes) = project_matrix(&matrix).unwrap();
        let rows = runtimes["include"].as_array().unwrap();
        assert_eq!(rows.len(), 11);
        for row in rows {
            let host = matrix.host(row["host"].as_str().unwrap()).unwrap();
            assert_eq!(
                row["host_runner"],
                serde_json::to_value(&host.runner).unwrap()
            );
            if row["backend"] != "cpu" {
                assert_ne!(row["host_runner"], row["runner"]);
            }
        }
    }
}
