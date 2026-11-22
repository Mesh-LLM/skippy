#!/usr/bin/env python3
"""Render normalized inference qualification JSON as a dataset card or Actions summary."""

import argparse
from pathlib import Path
import sys

from lib.ci_artifacts import read_json
from lib.inference_markdown import dataset_card, dataset_charts, run_summary
from lib.inference_png import export_charts
from lib.inference_charts import health_charts
from lib.inference_health import health_view


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("format", choices=("dataset-card", "summary"))
    parser.add_argument("input", type=Path, help="Version 1 qualification report JSON")
    parser.add_argument("--run", help="Run ID to render in the Actions summary")
    parser.add_argument("--output", type=Path,
                        help="Write Markdown to this path and export dataset PNGs beside it")
    parser.add_argument("--image-base-url", help="Pinned public URL for chart images in an Actions summary")
    args = parser.parse_args()
    if (args.format == "summary") != (args.run is not None):
        parser.error("--run is required for summary and only applies to that format")
    try:
        report = read_json(args.input)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.run is not None:
            markdown = run_summary(report, args.run, image_base_url=args.image_base_url)
            if args.output and not args.image_base_url:
                export_charts(health_charts(health_view(report, args.run)), args.output.parent)
        else:
            charts = dataset_charts(report)
            markdown = dataset_card(report, charts)
            if args.output:
                export_charts(charts, args.output.parent)
        if args.output:
            args.output.write_text(markdown, encoding="utf-8")
        else:
            sys.stdout.write(markdown)
    except (KeyError, TypeError, ValueError, OSError) as error:
        parser.exit(1, f"Cannot render qualification report: {error}\n")


if __name__ == "__main__":
    main()
