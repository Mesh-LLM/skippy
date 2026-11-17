use super::*;
use crate::execution;
pub fn run(model: &Path, key: &str, layers: u32, width: u32, output: &Path) -> DynResult<()> {
    for command in ["chain", "state-handoff"] {
        let mut args = vec![
            command.into(),
            "--model".into(),
            model.display().to_string(),
            "--model-id".into(),
            key.into(),
            "--layer-end".into(),
            layers.to_string(),
            "--n-gpu-layers".into(),
            "0".into(),
            "--ctx-size".into(),
            "512".into(),
            "--report-out".into(),
            output.join(format!("{command}.json")).display().to_string(),
        ];
        if command == "chain" {
            args.extend([
                "--splits".into(),
                format!("{},{}", layers / 3, 2 * layers / 3),
            ]);
        } else {
            args.extend([
                "--activation-width".into(),
                width.to_string(),
                "--state-layer-start".into(),
                "0".into(),
                "--state-layer-end".into(),
                (layers / 2).to_string(),
                "--prefix-token-count".into(),
                "32".into(),
            ]);
        }
        execution::run("target/debug/skippy-correctness", &args, &[], 1800)?;
    }
    Ok(())
}
pub fn capacity(bytes: u64) -> DynResult<()> {
    let mem = std::fs::read_to_string("/proc/meminfo")?;
    let available = mem
        .lines()
        .find(|line| line.starts_with("MemAvailable:"))
        .and_then(|l| l.split_whitespace().nth(1))
        .ok_or("MemAvailable missing")?
        .parse::<u64>()?
        * 1024;
    // Reserve 16 GiB beyond weights for cache, runtime and the OS. This is an
    // admission floor; actual allocations remain bounded by the JY host quota.
    if available < bytes + 16 * 1024 * 1024 * 1024 {
        return Err("insufficient free unified memory for weights and runtime reserve".into());
    }
    #[cfg(unix)]
    if unsafe { libc::geteuid() } == 0 {
        return Err("native inference must not execute as root".into());
    }
    Ok(())
}
