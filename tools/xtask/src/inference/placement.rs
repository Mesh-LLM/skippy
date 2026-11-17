use super::*;
use serde_json::Value;
pub fn pair(agents: &Value, runner: &str, attempt: usize) -> DynResult<(String, String)> {
    let mut candidates: Vec<String> = agents["agents"]
        .as_array()
        .ok_or("agent inventory missing")?
        .iter()
        .filter(|a| {
            a["online"] == true
                && a["enabled"] == true
                && a["drain"] == false
                && a["network"] == "james-lab"
                && a["host"]["gpu"] == "NVIDIA GB10"
                && a["active_runs"]
                    .as_array()
                    .is_some_and(|runs| runs.is_empty())
                && a["id"].as_str().is_some_and(|id| !runner.contains(id))
        })
        .filter_map(|a| a["node"].as_str().map(str::to_owned))
        .collect();
    candidates.sort();
    candidates.dedup();
    if candidates.len() < 2 {
        return Err("fewer than two idle Sparks available".into());
    }
    let count = candidates.len();
    candidates.rotate_left(attempt % count);
    Ok((candidates[0].clone(), candidates[1].clone()))
}
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn agent(id: &str, node: &str) -> Value {
        json!({"id":id,"node":node,"online":true,"enabled":true,"drain":false,"network":"james-lab","host":{"gpu":"NVIDIA GB10"},"active_runs":[]})
    }
    #[test]
    fn placement_excludes_controller_and_active_work() {
        let mut busy = agent("agt_busy", "spark-000265");
        busy["active_runs"] = json!([{"id":"other-work"}]);
        let inventory = json!({"agents":[agent("agt_controller","spark-000137"),agent("agt_a","spark-000280"),agent("agt_b","spark-000281"),busy]});
        assert_eq!(
            pair(&inventory, "jy-agt_controller-linux", 0).unwrap(),
            ("spark-000280".into(), "spark-000281".into())
        );
        assert_ne!(
            pair(&inventory, "jy-agt_controller-linux", 1).unwrap().0,
            "spark-000280"
        );
        assert!(pair(&json!({"agents":[agent("agt_a","spark-000280")]}), "", 0).is_err());
    }
}
