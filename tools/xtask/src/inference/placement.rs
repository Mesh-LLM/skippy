use super::*;
use serde_json::Value;
pub fn pair(agents: &Value, runner: &str, attempt: usize) -> DynResult<(String, String)> {
    let candidates: Vec<(String, String)> = agents["agents"]
        .as_array()
        .ok_or("agent inventory missing")?
        .iter()
        .filter(|a| {
            a["online"] == true
                && a["enabled"] == true
                && a["drain"] == false
                && matches!(a["network"].as_str(), Some("james-lab" | "dan-lab"))
                && a["host"]["gpu"] == "NVIDIA GB10"
                && a["active_runs"]
                    .as_array()
                    .is_some_and(|runs| runs.is_empty())
                && a["id"].as_str().is_some_and(|id| !runner.contains(id))
        })
        .filter_map(|a| {
            Some((
                a["network"].as_str()?.to_owned(),
                a["node"].as_str()?.to_owned(),
            ))
        })
        .collect();
    let mut networks = std::collections::BTreeMap::<String, Vec<String>>::new();
    for (network, node) in candidates {
        networks.entry(network).or_default().push(node);
    }
    let mut groups: Vec<Vec<String>> = networks
        .into_values()
        .map(|mut nodes| {
            nodes.sort();
            nodes.dedup();
            nodes
        })
        .filter(|nodes| nodes.len() >= 2)
        .collect();
    if groups.is_empty() {
        return Err("fewer than two idle Sparks on one network available".into());
    }
    let count = groups.len();
    let nodes = &mut groups[attempt % count];
    let size = nodes.len();
    nodes.rotate_left((attempt / count) % size);
    Ok((nodes[0].clone(), nodes[1].clone()))
}
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn agent(id: &str, node: &str) -> Value {
        json!({"id":id,"node":node,"online":true,"enabled":true,"drain":false,"network":"james-lab","host":{"gpu":"NVIDIA GB10"},"active_runs":[]})
    }
    #[test]
    fn placement_uses_either_lab_but_never_crosses_networks() {
        let mut dan_a = agent("agt_d1", "spark-000280");
        let mut dan_b = agent("agt_d2", "spark-000468");
        dan_a["network"] = "dan-lab".into();
        dan_b["network"] = "dan-lab".into();
        let james = agent("agt_j1", "spark-000137");
        assert!(pair(&json!({"agents":[dan_a.clone(),james.clone()]}), "", 0).is_err());
        assert_eq!(
            pair(&json!({"agents":[dan_a,dan_b,james]}), "", 0).unwrap(),
            ("spark-000280".into(), "spark-000468".into())
        );
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
