pub(crate) mod artifacts;
pub(crate) use crate::execution;
pub(crate) mod matrix;
pub(crate) mod producers;
mod profile;
mod smoke;
use crate::command::DynResult;
use std::{io::Write, path::Path};

pub fn run(args: &[String]) -> DynResult<()> {
    match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["plan"] => plan(),
        ["host", id] => producers::host(id),
        ["runtime", id] => producers::runtime(id),
        ["package", id, host, runtime] => producers::package(id, Path::new(host), Path::new(runtime)),
        ["smoke", id, product] => smoke::run(id, Path::new(product)),
        ["artifact-smoke", id, product] => smoke::artifact(id, Path::new(product)),
        ["model-fetch"] => smoke::prepare_model(),
        _ => Err("usage: ci {plan|model-fetch|host ID|runtime ID|package ID HOST_DIR RUNTIME_DIR|smoke ID PRODUCT_DIR|artifact-smoke ID PRODUCT_DIR}".into()),
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
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn runtime_matrix_exports_host_and_accelerator_runner_metadata() {
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
