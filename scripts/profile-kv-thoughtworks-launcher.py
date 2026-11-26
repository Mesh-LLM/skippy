#!/usr/bin/env python3
"""Translate the pinned competitive driver's total context for standalone Skippy."""

import argparse
from pathlib import Path


GATE = """
async fn qualification_gate(
    directory: Option<&Path>,
    phase: &str,
    deadline: Instant,
    cancellation: &Cancellation,
) -> DynResult<()> {
    if let Some(directory) = directory {
        std::fs::write(directory.join(format!("{phase}.ready")), [])?;
        let release = directory.join(format!("{phase}.go"));
        while !release.is_file() {
            check(deadline, cancellation)?;
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    }
    Ok(())
}
"""


def replace_once(contents, before, after):
    if contents.count(before) != 1:
        raise ValueError(f"pinned native launcher pattern changed: {before}")
    return contents.replace(before, after)


def add_phase_gates(path):
    contents = path.read_text()
    contents = replace_once(
        contents, "    let mut measured = Vec::new();",
        '    let gates = std::env::var_os("SKIPPY_THOUGHTWORKS_GATES").map(PathBuf::from);\n'
        "    let mut measured = Vec::new();",
    )
    contents = replace_once(
        contents, "                check(deadline, &cancellation)?;\n            }\n        }\n"
        "        let started = Instant::now();",
        """                check(deadline, &cancellation)?;
                if (local + 1) % 32 == 0 {
                    qualification_gate(
                        gates.as_deref(),
                        &format!("warm-{group}-{fraction}-{local}"),
                        deadline,
                        &cancellation,
                    ).await?;
                }
            }
        }
        qualification_gate(
            gates.as_deref(), &format!("measure-{group}"), deadline, &cancellation,
        ).await?;
        let started = Instant::now();""",
    )
    path.write_text(replace_once(contents, "fn prefix(prompt:", GATE + "\nfn prefix(prompt:"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--phase-gates", action="store_true")
    args = parser.parse_args()
    path = args.source / "tools/xtask/src/automation/replay_matrix/competitive_launch.rs"
    contents = path.read_text()
    before = '"ctx_size":capacity.context,"lane_count":capacity.lanes'
    after = ('"ctx_size":capacity.context / capacity.lanes,"kv_pool_tokens":capacity.context,'
             '"lane_count":capacity.lanes,"flash_attn_type":"enabled"')
    path.write_text(replace_once(contents, before, after))
    if args.phase_gates:
        add_phase_gates(path.with_name("competitive_cell.rs"))


if __name__ == "__main__":
    main()
