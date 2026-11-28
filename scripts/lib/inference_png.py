"""Export the same static, measured health charts for Hugging Face and Actions."""

from datetime import timedelta, timezone
from math import ceil
from pathlib import Path

from .inference_baselines import TOLERANCE, classify
from .inference_charts import COLORS, image_path

INK, MUTED = "#192c38", "#526571"
MARKERS = {None: "s", "passed": "o", "improved": "^", "regressed": "v"}
LABELS = {None: "New series", "passed": "within tolerance", "improved": "improved", "regressed": "regressed"}


def panel_axes(ax, panel, chart, mdates):
    color = COLORS[panel.verdict]
    ax.set_title(panel.label, fontsize=11, color=INK, loc="left", pad=38, weight="medium")
    current = "Measurement unavailable" if panel.current is None else f"{panel.current * chart.scale:,.2f} {chart.unit}"
    ax.text(0, 1.12, current, transform=ax.transAxes,
            fontsize=12, color=INK, weight="bold")
    change = "" if panel.change is None else f"{panel.change:+.2f}% · "
    ax.text(1, 1.12, change + ("Missing current sample" if panel.current is None else LABELS[panel.verdict]), transform=ax.transAxes, fontsize=8.5,
            color=color, ha="right")
    dates = [date for date, _ in panel.points]
    pad = max((dates[-1] - dates[0]) / 8, timedelta(minutes=1))
    ax.set_xlim(dates[0] - pad, dates[-1] + pad)
    ax.tick_params(axis="both", labelsize=8, colors=MUTED, length=0, pad=5)
    for spine in ax.spines.values():
        spine.set_visible(False)
    if panel.reference is None:
        ax.set_yticks([])
        ax.scatter([dates[-1]], [0], marker="s", color=color, s=42)
        ax.text(.03, .5, "First measurement", transform=ax.transAxes, color=MUTED, fontsize=10)
    else:
        values = [float("nan") if value is None else (value / panel.reference - 1) * 100
                  for _, value in panel.points]
        limit = max(4.5, max(abs(value) for value in values if value == value) * 1.25)
        ax.set_ylim(-limit, limit)
        tolerance = float(TOLERANCE * 100)
        ax.axhspan(-tolerance, tolerance, color="#edf3fa")
        ax.axhline(0, color="#7d8b94", linewidth=1, linestyle=(0, (4, 3)))
        ax.plot(dates, values, color="#536574", linewidth=1.5)
        for (date, measured), value in zip(panel.points, values):
            if measured is not None:
                verdict = classify(chart.metric, measured, panel.reference)
                ax.scatter([date], [value], marker=MARKERS[verdict], color=COLORS[verdict],
                           s=38, edgecolor="white", linewidth=.7, zorder=3)
        ax.set_yticks([-tolerance, 0, tolerance], [f"−{tolerance:g}%", "0%", f"+{tolerance:g}%"])
    distinct = tuple(dict.fromkeys(dates))
    if len(distinct) <= 3:
        ax.set_xticks(distinct)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d · %H:%M", tz=timezone.utc))
    else:
        locator = mdates.AutoDateLocator(minticks=2, maxticks=3, tz=timezone.utc)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator, tz=timezone.utc))


def export_charts(charts, directory):
    if not charts:
        return
    try:
        from matplotlib import dates as mdates
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ModuleNotFoundError as error:
        raise ValueError("dataset PNG export requires ci/inference-report-requirements.txt") from error
    directory = Path(directory)
    (directory / "charts").mkdir(parents=True, exist_ok=True)
    for index, chart in enumerate(charts, start=1):
        columns = min(3, len(chart.panels))
        rows = ceil(len(chart.panels) / columns)
        figure = Figure(figsize=(4.5 * columns, max(3.8, 2.4 * rows)), dpi=155, facecolor="white")
        FigureCanvasAgg(figure)
        axes = figure.subplots(rows, columns, squeeze=False)
        figure.subplots_adjust(left=.065, right=.975, top=.78 if rows == 1 else .88, bottom=.085, hspace=.95, wspace=.25)
        for ax, panel in zip(axes.flat, chart.panels):
            panel_axes(ax, panel, chart, mdates)
        for ax in list(axes.flat)[len(chart.panels):]:
            ax.set_visible(False)
        figure.savefig(directory / image_path(index))
