use super::*;
pub fn verify(log: &Path) -> DynResult<()> {
    let text = std::fs::read_to_string(log)?;
    if !text.lines().any(positive_offload) {
        return Err("server log has no positive GPU-layer offload evidence".into());
    }
    Ok(())
}
fn positive_offload(line: &str) -> bool {
    line.split("offloaded ").nth(1).is_some_and(|tail| {
        tail.contains("GPU")
            && tail
                .split('/')
                .next()
                .and_then(|v| v.parse::<u32>().ok())
                .is_some_and(|n| n > 0)
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cuda_presence_is_not_gpu_execution_evidence() {
        assert!(!positive_offload("found NVIDIA GB10"));
        assert!(!positive_offload("offloaded 0/32 layers to GPU"));
        assert!(positive_offload(
            "llama_model_load: offloaded 32/32 layers to GPU"
        ));
    }
}
