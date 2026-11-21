"""Reduce per-round samples to per-arm medians and spreads, and render the comparison."""

import statistics

# Higher is better for rates; lower is better for latencies.
HEADLINE = {"output_tok_s": "higher", "ttft_ms_p50": "lower", "itl_ms_p50": "lower"}


def summarize(samples):
    """Group by (model, server, point, metric), then by arm: median, min, max and n over rounds."""
    groups = {}
    for sample in samples:
        for metric, value in sample["metrics"].items():
            if value is None:
                continue
            key = (sample["model"], sample["server"], sample["point"], metric)
            groups.setdefault(key, {}).setdefault(sample["arm"], []).append(value)
    rows = []
    for (model, server, point, metric), by_arm in sorted(groups.items()):
        arms = {arm: {"median": statistics.median(values), "min": min(values), "max": max(values),
                      "n": len(values)} for arm, values in sorted(by_arm.items())}
        rows.append({"model": model, "server": server, "point": point, "metric": metric, "arms": arms,
                     "candidate_vs_baseline": change(arms.get("candidate"), arms.get("baseline"))})
    return rows


def change(candidate, baseline):
    if not candidate or not baseline or not baseline["median"]:
        return None
    overlap = candidate["min"] <= baseline["max"] and baseline["min"] <= candidate["max"]
    return {"percent": 100 * (candidate["median"] - baseline["median"]) / baseline["median"],
            "ranges_overlap": overlap}


def cell(stats):
    if not stats:
        return "—"
    return f'{stats["median"]:.1f} ({stats["min"]:.1f}–{stats["max"]:.1f}, n={stats["n"]})'


def verdict(row):
    delta = row["candidate_vs_baseline"]
    if delta is None:
        return "—"
    better = (delta["percent"] > 0) == (HEADLINE[row["metric"]] == "higher")
    word = "within spread" if delta["ranges_overlap"] else ("better" if better else "worse")
    return f'{delta["percent"]:+.1f}% {word}'


def markdown(title, host, rows):
    lines = [f"### {title}", "", f"Host: {host}", "",
             "Median over rounds (min–max, n). Δ is candidate against baseline; "
             "\"within spread\" means their round ranges overlap.", "",
             "| model | point | metric | baseline | candidate | reference | Δ |",
             "|---|---|---|---|---|---|---|"]
    for row in rows:
        if row["metric"] in HEADLINE:
            arms = row["arms"]
            lines.append(f'| {row["model"]} | {row["point"]} | {row["metric"]} | {cell(arms.get("baseline"))} '
                         f'| {cell(arms.get("candidate"))} | {cell(arms.get("reference"))} | {verdict(row)} |')
    return "\n".join(lines) + "\n"
