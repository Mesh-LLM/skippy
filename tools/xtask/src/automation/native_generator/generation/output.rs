use super::{Error, Generated};
pub(super) fn encode(generated: &Generated) -> Result<String, Error> {
    Ok(serde_json::to_string(&serde_json::json!({
        "first_summary": generated.first_summary,
        "output": generated.output,
        "second_summary": generated.second_summary,
        "shards": generated.shards,
    }))?)
}
