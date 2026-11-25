"""Reduce per-round samples to per-arm medians and spreads, and render the comparison."""

import json
import statistics

from .inference_baselines import classify
from .inference_health import health_view
from .inference_charts import health_charts
from .inference_markdown import HEALTH_LABELS, health_sections, plain, table
from .inference_nightly_metrics import metric_value

# Higher is better for rates; lower is better for latencies.
HEADLINE = {"output_tok_s": "higher", "ttft_ms_p50": "lower", "itl_ms_p50": "lower"}


def summarize(samples):
    """Group by (model, server, point, metric), then by arm: median, min, max and n over rounds.

    `rejected` counts, by arm, the rounds in which the arm's server rejected the point.
    """
    groups, rejected = {}, {}
    for sample in samples:
        if sample["rejected"] is not None:
            rounds = rejected.setdefault((sample["model"], sample["server"], sample["point"]), {})
            rounds[sample["arm"]] = rounds.get(sample["arm"], 0) + 1
        for metric, value in sample["metrics"].items():
            if value is None:
                continue
            key = (sample["model"], sample["server"], sample["point"], metric)
            groups.setdefault(key, {}).setdefault(sample["arm"], []).append(metric_value(value, positive=True))
    rows = []
    for (model, server, point, metric), by_arm in sorted(groups.items()):
        arms = {arm: {"median": statistics.median(values), "min": min(values), "max": max(values),
                      "n": len(values)} for arm, values in sorted(by_arm.items())}
        rows.append({"model": model, "server": server, "point": point, "metric": metric, "arms": arms,
                     "rejected": rejected.get((model, server, point), {}),
                     "candidate_vs_baseline": change(arms.get("candidate"), arms.get("baseline"))})
    return rows


def change(candidate, baseline):
    if not candidate or not baseline or not baseline["median"]:
        return None
    overlap = candidate["min"] <= baseline["max"] and baseline["min"] <= candidate["max"]
    return {"percent": 100 * (candidate["median"] - baseline["median"]) / baseline["median"],
            "ranges_overlap": overlap}


def cell(stats, rejected):
    """An arm's rounds at one point: what they measured, and how many of them its server rejected."""
    if not stats:
        return f"rejected (n={rejected})" if rejected else "—"
    measured = f'{stats["median"]:.1f} ({stats["min"]:.1f}–{stats["max"]:.1f}, n={stats["n"]})'
    return f"{measured}; rejected n={rejected}" if rejected else measured


def verdict(row):
    delta = row["candidate_vs_baseline"]
    if delta is None:
        return "New series"
    arms = row["arms"]
    result = classify(row["metric"], arms["candidate"]["median"], arms["baseline"]["median"])
    overlap = "; round ranges overlap" if delta["ranges_overlap"] else "; round ranges do not overlap"
    return f'{delta["percent"]:+.2f}% · {HEALTH_LABELS[result]}{overlap}'


def report_view(host, rows, record):
    """Adapt real controlled measurements to the common inference health report."""
    candidate = next(arm for arm in record["arms"] if arm["label"] == "candidate")
    configurations = {}
    for row in rows:
        if row["metric"] not in HEADLINE or "candidate" not in row["arms"]:
            continue
        key = row["model"], row["server"], row["point"]
        configuration = configurations.setdefault(key, {
            "id": "/".join(key), "label": " · ".join(key), "group": host,
            "workload": f'Controlled {((record.get("host") or {}).get("backend") or "metal")} · {host}'
                        f' · {row["server"]} · {row["point"]}',
            "outcome": "passed", "metrics": [],
        })
        arms = row["arms"]
        metric = {"metric": row["metric"], "workload": configuration["workload"],
                  "measured": arms["candidate"]["median"],
                  "comparison_arms": [(label.title(), arms[label]["median"])
                                      for label in ("baseline", "candidate", "reference") if label in arms]}
        if "baseline" in arms:
            metric["reference"] = {"value": arms["baseline"]["median"]}
        configuration["metrics"].append(metric)
    run = {"id": "controlled", "source_sha": candidate["sha"], "started_at": record["started_at"],
           "profile": "controlled performance", "measurement_kind": "controlled", "outcome": "passed",
           "configurations": list(configurations.values())}
    return health_view({"schema_version": 1, "runs": [run]}, run["id"])


def markdown(title, host, rows, record):
    view = report_view(host, rows, record)
    lines = ["# " + plain(title), "", "Host: " + plain(host), "",
             *health_sections(view, health_charts(view), chart_format="mermaid"), "",
             "## Round ranges", "", "Median (min–max, n). Reference is the optional llama.cpp arm. "
             "Its measurements are shown separately from the candidate/baseline verdict. "
             "Rejected is the number of rounds in which its server failed requests at a point "
             "that needs more KV than its pool holds.", ""]
    detail = []
    for row in rows:
        if row["metric"] in HEADLINE:
            arms = row["arms"]
            detail.append([plain(row[field]) for field in ("model", "server", "point", "metric")] +
                          [cell(arms.get(arm), row["rejected"].get(arm, 0))
                           for arm in ("baseline", "candidate", "reference")] + [verdict(row)])
    lines.extend(table(["Model", "Server", "Point", "Metric", "Baseline", "Candidate", "Reference", "Change"], detail))
    return "\n".join(lines) + "\n"


def rows(record, shape, committed_at, job, pull_requests, branches=None):
    """One flat row per measured sample, for a table that graphs runs over time.

    The diagnostic pass is left out. Every row carries what it was measured on and with,
    so rows from many runs can share one table: `committed_at` orders arms by commit,
    `started_at` orders runs by when they ran. An arm that `pull_requests` maps to a pull
    request, or `branches` to a branch, has `on_main` false, so a graph of main's history can
    leave it out; llama.cpp's server has it null. What an arm applied or lacked differs by arm,
    so it is kept as JSON text and every row has the same columns. A point the server rejected
    keeps its row: its metrics are null and `rejected` holds what was rejected, as JSON text.
    """
    arms = {arm["label"]: arm for arm in record["arms"]}
    points = {(server["id"], point["id"]): (server, point)
              for server in shape["servers"] for point in server["points"]}
    host = record["host"]
    for sample in record["samples"]:
        arm = arms[sample["arm"]]
        pull_request = pull_requests.get(sample["arm"])
        branch = (branches or {}).get(sample["arm"])
        server, point = points[(sample["server"], sample["point"])]
        yield {
            "schema_version": 2, "job": job, "started_at": record.get("started_at"),
            "harness": record["harness"], "scenario_sha256": record["scenario_sha256"],
            "backend": host.get("backend"), "gpu": host.get("gpu"), "chip": host.get("chip"),
            "macos": host.get("macos"), "os": host.get("os"), "os_version": host.get("os_version"),
            "kernel": host.get("kernel"), "driver_version": host.get("driver_version"),
            "toolkit_version": host.get("toolkit_version"), "node": host.get("node"),
            "metal_working_set_bytes": host.get("metal_working_set_bytes"),
            "arm": sample["arm"], "kind": arm["kind"], "sha": arm["sha"],
            "committed_at": committed_at.get(arm["sha"]), "binary_sha256": arm["binary_sha256"],
            "on_main": None if arm["kind"] != "skippy" else pull_request is None and branch is None,
            "pull_request": pull_request, "branch": branch,
            "model": sample["model"], "model_revision": record["models"][sample["model"]]["revision"],
            "server": sample["server"], "lanes": server["lanes"], "pool_tokens": server["pool_tokens"],
            "point": sample["point"], "concurrency": point["concurrency"], "depth": point["depth"],
            "input_tokens": point["input_tokens"], "output_tokens": point["output_tokens"],
            "requests": point["requests"], "round": sample["round"],
            "applied": json.dumps(sample["applied"], sort_keys=True),
            "unsupported": json.dumps(sample["unsupported"]),
            "rejected": None if sample["rejected"] is None else json.dumps(sample["rejected"], sort_keys=True),
            **sample["metrics"],
        }
