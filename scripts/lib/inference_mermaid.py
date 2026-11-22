"""Render measured comparison panels inline in read-only GitHub jobs."""

import json
import re

from .inference_charts import COLORS

LABELS = {None: "New series", "passed": "Within tolerance", "improved": "Improved", "regressed": "Regressed"}


def quoted(value):
    return json.dumps(re.sub(r"[^a-zA-Z0-9 ./(),%:+_-]", " ", value))


def measured_panels(chart):
    lines = []
    for panel in chart.panels:
        labels = ["This run"]
        values = [panel.current * chart.scale]
        if panel.reference is not None:
            labels.insert(0, "Saved reference")
            values.insert(0, panel.reference * chart.scale)
        if panel.arms:
            labels = [label for label, _ in panel.arms]
            values = [value * chart.scale for _, value in panel.arms]
        delta = "" if panel.change is None else f"{panel.change:+.2f}% · "
        title = re.sub(r"[\n\r\x60|<>]", " ", panel.label)
        lines.extend(["", "#### " + title, "",
                      f"**{panel.current * chart.scale:,.2f} {chart.unit} · {delta}{LABELS[panel.verdict]}**", "",
                      "```mermaid", "---", "config:", "  themeVariables:", "    xyChart:",
                      f'      plotColorPalette: "{COLORS[panel.verdict]}"', "---", "xychart-beta",
                      "    x-axis [" + ", ".join(quoted(label) for label in labels) + "]",
                      "    y-axis " + quoted(chart.unit) + " 0 --> " + str(max(values) * 1.2 or 1),
                      "    bar [" + ", ".join(f"{value:.8f}" for value in values) + "]", "```"])
    return lines
