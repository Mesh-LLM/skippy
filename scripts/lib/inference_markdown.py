"""Render the same qualification evidence for GitHub Actions and Hugging Face.

The input contains one measurement per run, configuration, workload, and metric.
A reference value comes from the pinned Hugging Face snapshot. Qualification
supplies the verdict after applying the configured tolerance.
"""

from html import escape
from math import isfinite
import re

from .inference_charts import METRICS as CHART_METRICS, dataset_charts, health_charts, image_path
from .inference_baselines import TOLERANCE, DIRECTION
from .inference_health import health_view, ordered_runs, utc
from .inference_labels import configuration_label, workload_label


HF_DATASET = "https://huggingface.co/datasets/meshllm/skippy-inference-qualification"
METRICS = {
    "prefill_tok_s": ("Prefill throughput", "tok/s"),
    "decode_tok_s": ("Decode throughput", "tok/s"),
    "output_tok_s": ("Output throughput", "tok/s"),
    "ttft_p95_ms": ("Time to first token (p95)", "ms"),
    "itl_p95_ms": ("Time between tokens (p95)", "ms"),
    "requests": ("Requests", ""),
    "request_error_pct": ("Request error rate", "%"),
}
OUTCOMES = {
    "passed": "Passed",
    "failed": "Failed",
    "skipped": "Skipped",
    "cancelled": "Cancelled",
    "incomplete": "Incomplete",
}
VERDICTS = {
    "passed": "Within tolerance",
    "regressed": "Regression",
    "improved": "Improvement",
    "inconclusive": "Inconclusive",
}
SHA = re.compile(r"[0-9a-f]{40}")
HTTPS = re.compile(r"https://[a-zA-Z0-9.-]+[/a-zA-Z0-9_.~%?&=#:+-]*")
UNAVAILABLE = "—"


def plain(value):
    if not isinstance(value, str):
        raise ValueError("expected report text")
    return re.sub(r"([\\\x60*_[\]~|])", r"\\\1", escape(" ".join(value.split()), quote=False))


def link(label, url):
    if not url:
        return label
    if not HTTPS.fullmatch(url):
        raise ValueError(f"expected an HTTPS report link, got {url!r}")
    return f"[{label}]({url})"


def sha(value):
    if not SHA.fullmatch(value):
        raise ValueError(f"expected a full commit SHA, got {value!r}")
    return f"\x60{value[:12]}\x60"


def table(headers, rows):
    return ["| " + " | ".join(row) + " |" for row in (headers, ["---"] * len(headers), *rows)]


def number(value):
    if type(value) not in (int, float) or not isfinite(value) or value < 0:
        raise ValueError(f"expected a finite, nonnegative measurement, got {value!r}")
    return value


def measurement(metric, value):
    if value is None:
        return UNAVAILABLE
    unit = METRICS[metric][1]
    formatted = f"{number(value):,.2f}".rstrip("0").rstrip(".")
    if unit == "%":
        return formatted + unit
    return " ".join(part for part in (formatted, unit) if part)


def delta(metric, current, reference):
    difference = current - reference
    if metric == "request_error_pct":
        return f"{difference:+.2f} pp"
    if reference == 0:
        unit = METRICS[metric][1]
        return f"{difference:+,.2f}" + (f" {unit}" if unit else "")
    return f"{(difference / reference) * 100:+.2f}%"


def comparison(row):
    metric, measured = row["metric"], row["measured"]
    reference = row.get("reference")
    verdict = row.get("verdict")
    if verdict is not None:
        if verdict not in VERDICTS:
            raise ValueError(f"unknown baseline verdict: {verdict!r}")
        if reference is None or measured is None:
            raise ValueError("a verdict requires a measurement and a saved baseline")
    update = row.get("baseline_update")
    if update == "seeded" and (measured is None or reference is not None):
        raise ValueError("seeding a baseline requires a first measurement")
    if update == "promoted" and verdict != "improved":
        raise ValueError("promoting a baseline requires an improvement")
    if update not in (None, "seeded", "promoted"):
        raise ValueError(f"unknown baseline update: {update!r}")

    current = measurement(metric, measured)
    expected = reference_label = change = UNAVAILABLE
    if reference is not None:
        expected = measurement(metric, number(reference["value"]))
        reference_label = sha(reference["source_sha"])
        if measured is not None:
            change = delta(metric, measured, reference["value"])
    if measured is None:
        outcome = "Measurement unavailable"
    elif reference is None:
        outcome = "No matching baseline"
    else:
        outcome = VERDICTS.get(verdict, "Change only")
    if row.get("note"):
        outcome += f' · {plain(row["note"])}'
    return [METRICS[metric][0], current, expected, reference_label, change, outcome]



HEALTH_LABELS = {None: "New series", "passed": "Within tolerance", "improved": "Improved", "regressed": "Regressed"}


def health_measurement(metric, value):
    if value is None:
        return UNAVAILABLE
    _, unit, scale = CHART_METRICS[metric]
    return f"{number(value) * scale:,.2f} {unit}"


def regression_table(view):
    rows = []
    for config in view["configurations"]:
        for metric, row in config["metrics"].items():
            if row["health_verdict"] == "regressed":
                rows.append((abs(row["change_pct"]), [
                    plain(configuration_label(config["id"])), CHART_METRICS[metric][0],
                    health_measurement(metric, row["measured"]),
                    health_measurement(metric, row["reference_value"]), f'{row["change_pct"]:+.2f}%',
                ]))
    if not rows:
        return []
    rows.sort(key=lambda entry: entry[0], reverse=True)
    return ["", "## What regressed", "", *table(
        ["Configuration", "Metric", "Latest", "Reference", "Change"], [row for _, row in rows],
    )]


def latest_tables(view):
    lines = ["", "## Latest numbers", "",
             "Current values and signed changes against the saved reference, "
             "or the previous comparable measurement when no saved reference exists. "
             "Higher throughput and lower latency are better.", ""]
    for split, heading in ((False, "One Spark"), (True, "Two Sparks")):
        configs = [config for config in view["configurations"] if config["id"].endswith("-split") == split]
        if not configs:
            continue
        if any("experimental" in config["workload"] for config in configs):
            heading += " · experimental split"
        headers = ["Model"] + [f"{CHART_METRICS[metric][0]} ({CHART_METRICS[metric][1]})"
                                for metric in view["metrics"]]
        rows = []
        for config in configs:
            cells = [plain(configuration_label(config["id"]))]
            for metric in view["metrics"]:
                row = config["metrics"].get(metric)
                if row is None:
                    cells.append(UNAVAILABLE)
                    continue
                value = health_measurement(metric, row["measured"]).rsplit(" ", 1)[0]
                verdict = row["health_verdict"]
                annotation = HEALTH_LABELS[verdict]
                if row["change_pct"] is not None:
                    annotation = f'{row["change_pct"]:+.2f}% · {annotation}'
                cells.append(f"{value} ({annotation})")
            rows.append(cells)
        lines.extend(["### " + heading, "", *table(headers, rows), ""])
    return lines


def health_sections(view, charts, image_base_url=None, chart_format="png"):
    run, counts = view["run"], view["counts"]
    tolerance = f"{TOLERANCE * 100:.0f}%"
    lines = [
        f'**{counts.get("regressed", 0)} regressions · {counts.get("improved", 0)} improvements · '
        f'{counts.get("passed", 0)} within tolerance · {counts.get("new", 0)} first measurements**', "",
        f'{utc(run["started_at"]):%d %b %Y %H:%M UTC} · Engine {sha(run["source_sha"])} · '
        f'{plain(run["profile"])} · **Tolerance: ±{tolerance}**', "",
        f'{len(view["configurations"])} measured configurations. Counts refer to performance metrics. '
        'First measurements establish new series and have no trend verdict.',
    ]
    if view["configurations"] and all(config["workload"].startswith("DGX Spark")
                                      for config in view["configurations"]):
        lines[2] += " · NVIDIA GB10 DGX Spark"
    if not view["configurations"]:
        lines.extend(["", "No performance measurements available."])
        if run["outcome"] == "skipped":
            lines.extend(["", "Qualification intentionally skipped."])
    if view["issues"]:
        lines.extend(["", "## Execution failures", "", *table(["Configuration", "Outcome"], [
            [plain(configuration_label(item["id"])), OUTCOMES[item["outcome"]]] for item in view["issues"]
        ])])
    if view["errors"]:
        lines.extend(["", "**Request errors were measured.** " + ", ".join(f"{value:g}%" for value in view["errors"]) + "."])
    if run.get("comparison_error"):
        lines.extend(["", "**Comparison unavailable.** " + plain(run["comparison_error"])])
    elif run.get("comparison_notice"):
        lines.extend(["", plain(run["comparison_notice"])])
    lines.extend(regression_table(view))
    if charts:
        explanation = ("Each panel compares the measured value with its saved reference. "
                       "The comparison colour indicates its 1% verdict: green improved, blue within tolerance, "
                       "red regressed, and grey a new series. No unmeasured samples are filled in."
                       if chart_format == "mermaid" else
                      f'Each panel shows one measured model and Spark configuration. The dashed line is '
                      f'the current comparison reference; the shaded band is ±{tolerance}. '
                      'Blue circles are within tolerance, green triangles indicate improvements, '
                      'red triangles indicate regressions, and grey squares mark first measurements. '
                      'Missing measurements leave gaps. Dates are UTC.')
        lines.extend(["", "## Measured comparisons" if chart_format == "mermaid" else "## Performance over time", "", explanation])
        for index, chart in enumerate(charts, start=1):
            if chart_format == "mermaid":
                from .inference_mermaid import measured_panels
                lines.extend(["", "### " + chart.title, *measured_panels(chart)])
                continue
            path = image_path(index)
            url = f"{image_base_url.rstrip('/')}/{path}" if image_base_url else path
            lines.extend(["", "### " + chart.title, "",
                          ("Higher" if DIRECTION[chart.metric] > 0 else "Lower") + " is better.", "",
                          f"![{chart.title} over time for every measured configuration]({url})"])
    if view["configurations"]:
        lines.extend(latest_tables(view))
    lines.extend(["", "## Measurement notes", ""])
    if any(metric in view["metrics"] for metric in ("output_tok_s", "ttft_p95_ms", "itl_p95_ms")):
        lines.extend([
            'Two trials per configuration. Throughput and latency values are means of trial summaries. '
            'Latency is the mean of trial p95 values, not a pooled p95. '
            f'{view["requests"]:,.0f} requests were recorded in this run.', "",
        ])
        if view["error_samples"] and not view["errors"]:
            lines.extend(["No request errors were recorded.", ""])
    else:
        lines.extend(['Smoke throughput uses scored request token totals divided by measured phase time. '
                      'It is a different workload from nightly AgentX.', ""])
    lines.extend([f'The ±{tolerance} rule expresses tolerance, not statistical significance. '
                  'Comparisons preserve workload identity; a changed benchmark starts a new series.'])
    run_url = run.get("url")
    if not run_url and re.fullmatch(r"[1-9][0-9]*-[1-9][0-9]*", run["id"]):
        run_url = "https://github.com/Mesh-LLM/skippy/actions/runs/" + run["id"].split("-")[0]
    links = [link("Source run", run_url)] if run_url else []
    if run.get("published"):
        links.append(f"[Full results and history]({HF_DATASET}/tree/main/runs)")
        if not view["preview"]:
            links.append(f"[Historical comparison audit]({HF_DATASET}/blob/main/comparability.json)")
    if links:
        lines.extend(["", " · ".join(links)])
    if run.get("baseline_revision"):
        sha(run["baseline_revision"])
        lines.extend(["", link("Saved baseline snapshot", f"{HF_DATASET}/blob/{run['baseline_revision']}/baselines.json")])
    return lines


def run_summary(report, run_id, image_base_url=None, chart_format="png"):
    if chart_format not in {"png", "mermaid"}:
        raise ValueError("unsupported health chart format")
    view = health_view(report, run_id)
    charts = health_charts(view)
    return "\n".join(["# Inference qualification", "", *health_sections(view, charts, image_base_url, chart_format), ""])


def dataset_card(report, charts=None):
    view = health_view(report)
    preview = report.get("preview") is True
    lines = ["---", 'pretty_name: "Skippy inference health"', "tags:", "- benchmark", "- tabular"]
    if view is not None and not preview:
        lines.extend(["configs:", "  - config_name: default", "    data_files:",
                      "      - split: runs", '        path: "runs/*/*.parquet"'])
    lines.extend(["---", "", "# Skippy inference health", ""])
    if preview:
        lines.extend(["**Preview.** Values, dates and commit IDs on this card are illustrative.", ""])
    if view is None:
        lines.append("Awaiting the first nightly publication.")
    else:
        lines.extend(health_sections(view, health_charts(view) if charts is None else charts))
    return "\n".join([*lines, ""])
