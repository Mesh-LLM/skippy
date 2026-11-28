"""Analyze measured engine health without turning absent workloads into results."""

from collections import Counter
from datetime import datetime, timedelta, timezone

from .inference_baselines import classify
from .inference_comparability import contracts_match
from .inference_nightly_metrics import metric_value

AGENTX = ("output_tok_s", "ttft_p95_ms", "itl_p95_ms")
CONTROLLED = ("output_tok_s", "ttft_ms_p50", "itl_ms_p50")
SMOKE = ("prefill_tok_s", "decode_tok_s")
HISTORY_DAYS = 30


def utc(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("run timestamp needs a time zone")
    return stamp.astimezone(timezone.utc)


def ordered_runs(report):
    if type(report["schema_version"]) is not int or report["schema_version"] != 1:
        raise ValueError("unsupported qualification report schema version")
    if len({run["id"] for run in report["runs"]}) != len(report["runs"]):
        raise ValueError("duplicate qualification run ID")
    return sorted(report["runs"], key=lambda run: utc(run["started_at"]), reverse=True)


def indexed(run):
    result = {}
    for config in run["configurations"]:
        for metric in config["metrics"]:
            key = config["id"], metric["workload"], metric["metric"]
            if key in result:
                raise ValueError("duplicate qualification measurement")
            if metric["measured"] is not None:
                metric_value(metric["measured"], positive=metric["metric"] in (*AGENTX, *SMOKE))
            result[key] = metric
    return result


def history_for(key, current, timeline):
    configuration, workload, _ = key
    points = []
    for run, rows in reversed(timeline):
        row = rows.get(key)
        if row is not None and row["measured"] is not None:
            if run["id"] != timeline[-1][0]["id"] and not contracts_match(
                configuration, workload, current.get("contract_sha256"), row.get("contract_sha256"),
            ):
                break
            points.append((utc(run["started_at"]), row["measured"]))
        else:
            points.append((utc(run["started_at"]), None))
    points.reverse()
    while points and points[0][1] is None:
        points.pop(0)
    return points


def health_view(report, run_id=None):
    runs = ordered_runs(report)
    if run_id is None:
        target = next((run for run in runs if run.get("published") is True), None)
    else:
        target = next((run for run in runs if run["id"] == run_id), None)
    if target is None:
        if run_id is not None:
            raise ValueError(f"unknown qualification run ID: {run_id!r}")
        return None
    end = utc(target["started_at"])
    timeline = [(run, indexed(run)) for run in reversed(runs)
                if end - timedelta(days=HISTORY_DAYS) <= utc(run["started_at"]) <= end
                and (run["id"] == target["id"] or
                     (run.get("published") is True and utc(run["started_at"]) < end))]
    timeline = [(run, rows) for run, rows in timeline if run["id"] != target["id"]]
    timeline.append((target, indexed(target)))
    current = indexed(target)
    metrics = CONTROLLED if target.get("measurement_kind") == "controlled" else (*AGENTX, *SMOKE)
    groups, chart_groups, counts = {}, {}, Counter()
    retained = {key: row for _, rows in timeline for key, row in rows.items()}
    for (configuration, workload, metric), retained_row in retained.items():
        if metric not in metrics:
            continue
        key = configuration, workload, metric
        row = current.get(key, {**retained_row, "measured": None})
        if row["measured"] is None and not row.get("contract_sha256"):
            previous_row = next((rows[key] for _, rows in reversed(timeline)
                                 if key in rows and rows[key]["measured"] is not None), None)
            if previous_row is not None:
                row = {**row, "contract_sha256": previous_row.get("contract_sha256")}
        points = history_for(key, row, timeline)
        if not any(value is not None for _, value in points):
            continue
        previous = next((value for _, value in reversed(points[:-1]) if value is not None), None)
        reference = (metric_value(row["reference"]["value"], positive=True)
                     if row.get("reference") is not None else previous)
        measured = row["measured"]
        verdict = (None if reference is None or measured is None
                   else classify(metric, measured, reference))
        change = None if reference is None or measured is None else (measured / reference - 1) * 100
        analyzed = {**row, "reference_value": reference, "change_pct": change,
                    "health_verdict": verdict, "points": points}
        chart_groups.setdefault((configuration, workload), {})[metric] = analyzed
        if measured is not None:
            groups.setdefault((configuration, workload), {})[metric] = analyzed
            counts[verdict or "new"] += 1
    metadata = {config["id"]: config for config in target["configurations"]}
    configurations = [{"id": config, "workload": workload, "metrics": rows,
                       **{field: metadata[config][field] for field in ("label", "group") if field in metadata[config]}}
                      for (config, workload), rows in groups.items()]
    chart_configurations = [{"id": config, "workload": workload, "metrics": rows,
                             **{field: metadata.get(config, {})[field] for field in ("label", "group")
                                if field in metadata.get(config, {})}}
                            for (config, workload), rows in chart_groups.items()]
    metrics = tuple(metric for metric in metrics
                    if any(metric in rows for rows in chart_groups.values()))
    configurations.sort(key=lambda config: config["id"].endswith("-split"))
    issues = [{"id": config["id"], "outcome": config["outcome"]}
              for config in target["configurations"] if config["outcome"] in {"failed", "cancelled"}]
    errors = [row["measured"] for key, row in current.items()
              if key[2] == "request_error_pct" and row["measured"] is not None and row["measured"] > 0]
    request_counts = [row["measured"] for key, row in current.items()
                      if key[2] == "requests" and row["measured"] is not None]
    requests = sum(request_counts) if request_counts else None
    error_samples = sum(key[2] == "request_error_pct" and row["measured"] is not None
                        for key, row in current.items())
    return {"run": target, "configurations": configurations, "metrics": metrics,
            "chart_configurations": chart_configurations, "counts": dict(counts), "issues": issues, "errors": errors, "requests": requests,
            "error_samples": error_samples, "preview": report.get("preview") is True}
