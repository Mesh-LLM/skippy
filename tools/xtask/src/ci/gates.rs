//! Static runner routing still requires every active manifest row to succeed.
use super::matrix::Matrix;
use crate::command::DynResult;
use serde_json::Value;

pub fn run(stage: &str) -> DynResult<()> {
    let matrix = Matrix::load()?;
    let needs = serde_json::from_str(&std::env::var("SKIPPY_CI_NEEDS")?)?;
    validate(&matrix, stage, &needs)
}

fn validate(matrix: &Matrix, stage: &str, needs: &Value) -> DynResult<()> {
    let jobs = needs.as_object().ok_or("needs must be an object")?;
    if jobs.len() != matrix.runtimes.len() + 1
        || jobs
            .get("plan")
            .is_none_or(|value| value["result"] != "success")
    {
        return Err(
            format!("{stage} gate requires every active runtime from ci/targets.json").into(),
        );
    }
    for runtime in &matrix.runtimes {
        let job = format!("{stage}_{}", runtime.id.replace('-', "_"));
        if jobs
            .get(&job)
            .is_none_or(|value| value["result"] != "success")
        {
            return Err(format!("{stage} gate requires successful {job}").into());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn every_manifest_runtime_is_required_for_both_stages() {
        let matrix = Matrix::from_json(include_str!("../../../../ci/targets.json")).unwrap();
        for stage in ["runtime", "smoke"] {
            let mut jobs = serde_json::Map::new();
            jobs.insert("plan".into(), json!({"result":"success"}));
            for runtime in &matrix.runtimes {
                jobs.insert(
                    format!("{stage}_{}", runtime.id.replace('-', "_")),
                    json!({"result":"success"}),
                );
            }
            let all = Value::Object(jobs);
            validate(&matrix, stage, &all).unwrap();
            for key in all.as_object().unwrap().keys() {
                for status in ["skipped", "failure", "cancelled"] {
                    let mut changed = all.clone();
                    changed[key]["result"] = status.into();
                    assert!(validate(&matrix, stage, &changed).is_err());
                }
                let mut missing = all.clone();
                missing.as_object_mut().unwrap().remove(key);
                assert!(validate(&matrix, stage, &missing).is_err());
                missing["unexpected_job"] = json!({"result":"success"});
                assert!(validate(&matrix, stage, &missing).is_err());
            }
        }
    }
}
