use super::*;
use crate::execution;
use serde_json::Value;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Corpus {
    repo: String,
    revision: String,
    pub alias: String,
}
pub fn load() -> DynResult<Corpus> {
    Ok(serde_json::from_str(include_str!(
        "../../../../ci/inference-corpus.json"
    ))?)
}
pub fn verify(output: &Path, phase: &str) -> DynResult<()> {
    let corpus = load()?;
    let response = execution::capture(
        "curl",
        &[
            "-fsS".into(),
            "--retry".into(),
            "3".into(),
            "--max-time".into(),
            "60".into(),
            format!(
                "https://huggingface.co/api/datasets/{}/revision/main",
                corpus.repo
            ),
        ],
        &[],
    )?;
    let value: Value = serde_json::from_str(&response)?;
    write_json_file(&output.join(format!("corpus-{phase}.json")), &value)?;
    check(&value, &corpus)
}
fn check(value: &Value, corpus: &Corpus) -> DynResult<()> {
    if corpus.revision.len() != 40 || value["sha"] != corpus.revision {
        return Err("AgentX corpus revision differs from the admitted pin; qualification requires a reviewed corpus update".into());
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn corpus_updates_fail_instead_of_silently_changing_the_workload() {
        let corpus = load().unwrap();
        assert!(check(&serde_json::json!({"sha":corpus.revision}), &corpus).is_ok());
        assert!(check(&serde_json::json!({"sha":"f".repeat(40)}), &corpus).is_err());
        assert!(check(&serde_json::json!({}), &corpus).is_err());
    }
}
