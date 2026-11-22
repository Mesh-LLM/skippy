"""Build a complete figure per performance metric, including split configurations."""

from __future__ import annotations

from dataclasses import dataclass

from .inference_health import health_view
from .inference_labels import configuration_label

COLORS = {None: "#536574", "passed": "#2457a5", "improved": "#14704e", "regressed": "#ad3535"}
METRICS = {
    "ttft_ms_p50": ("Time to first token (p50)", "ms", 1),
    "itl_ms_p50": ("Time between tokens (p50)", "ms", 1),
    "output_tok_s": ("Output throughput", "tok/s", 1),
    "ttft_p95_ms": ("Time to first token", "seconds", 0.001),
    "itl_p95_ms": ("Time between tokens", "ms", 1),
    "prefill_tok_s": ("Prefill throughput", "tok/s", 1),
    "decode_tok_s": ("Decode throughput", "tok/s", 1),
}


@dataclass(frozen=True)
class HealthPanel:
    configuration: str
    workload: str
    label: str
    current: float
    reference: float | None
    change: float | None
    verdict: str | None
    points: tuple
    arms: tuple = ()


@dataclass(frozen=True)
class HealthFigure:
    metric: str
    title: str
    unit: str
    scale: float
    panels: tuple


def health_charts(view):
    if view is None:
        return []
    figures = []
    for metric in view["metrics"]:
        panels = []
        for config in view["configurations"]:
            row = config["metrics"].get(metric)
            if row is None:
                continue
            panels.append(HealthPanel(
                config["id"], config["workload"], config.get("label", configuration_label(config["id"])), row["measured"],
                row["reference_value"], row["change_pct"], row["health_verdict"], tuple(row["points"]), tuple(row.get("comparison_arms", ())),
            ))
        if panels:
            title, unit, scale = METRICS[metric]
            figures.append(HealthFigure(metric, title, unit, scale, tuple(panels)))
    return figures


def dataset_charts(report):
    return health_charts(health_view(report))


def image_path(index):
    return f"charts/qualification-{index}.png"
