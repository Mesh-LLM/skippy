#!/usr/bin/env python3
"""Publish aggregate nightly inference evidence and saved baselines to Hugging Face."""

import argparse
import os
from pathlib import Path
import re
import shutil
import sys
from tempfile import TemporaryDirectory

from lib.ci_artifacts import read_json, write_json
from lib.inference_baselines import DATASET as REPO, HISTORY_LIMIT, RUN_PATH, SHA, apply_baselines, prior_state
from lib.inference_markdown import dataset_card, dataset_charts, run_summary, utc
from lib.inference_nightly_results import nightly_run
from lib.inference_parquet import read_run, write_run
from lib.inference_png import export_charts
from lib.inference_publication_policy import publication_allowed
from lib.inference_health import health_view
from lib.inference_charts import health_charts, image_path
from lib.inference_comparability import AUDITS


CHART_PATH = re.compile(r"charts/(?:qualification-[1-4]|health-(?:output_tok_s|ttft_p95_ms|itl_p95_ms))\.png")


def admit(environment):
    if not publication_allowed(environment, environment.get("QUALIFICATION_PROFILE")):
        raise ValueError("only nightly qualification on protected main can publish")
    source = environment["GITHUB_SHA"]
    if not SHA.fullmatch(source) or environment.get("SOURCE_SHA") != source:
        raise ValueError("nightly publication source must match the workflow SHA")
    run_id, attempt, number = (environment[key] for key in (
        "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_RUN_NUMBER",
    ))
    if any(not re.fullmatch(r"[1-9][0-9]*", value) for value in (run_id, attempt, number)):
        raise ValueError("invalid nightly run identity")
    started_at = environment["RUN_STARTED_AT"]
    utc(started_at)
    result = environment["MATRIX_RESULT"]
    if result not in {"success", "failure", "cancelled"}:
        raise ValueError("invalid nightly matrix result")
    return source, run_id, attempt, int(number), started_at, result == "success"


def load_history(paths, fetch):
    history = []
    for path in paths:
        run = read_run(fetch(path))
        expected = "-".join(RUN_PATH.fullmatch(path).groups())
        if run["id"] != expected or run["profile"] != "nightly" or not SHA.fullmatch(run["source_sha"]):
            raise ValueError("published inference history identity mismatch")
        history.append(run)
    return history


def summary_report(api, add, files, revision, report, run, destination):
    """Pin Actions to this run's own charts, including historical reruns."""
    root = f"reports/{run['id']}"
    charts = health_charts(health_view(report, run["id"]))
    expected = {f"{root}/{image_path(index)}" for index in range(1, len(charts) + 1)}
    if expected - files:
        with TemporaryDirectory(prefix="skippy-health-replay-") as temporary:
            output = Path(temporary)
            export_charts(charts, output)
            operations = [add(path_in_repo=f"{root}/{image_path(index)}",
                              path_or_fileobj=output / image_path(index))
                          for index in range(1, len(charts) + 1)]
            receipt = api.create_commit(
                repo_id=REPO, repo_type="dataset", parent_commit=revision,
                commit_message=f"Backfill immutable health charts for {run['id']}", operations=operations,
            )
            revision = receipt.oid
    report["chart_base_url"] = f"https://huggingface.co/datasets/{REPO}/resolve/{revision}/{root}"
    write_json(destination, report)


def publish(api, fetch_file, add, delete, evidence, identity, report_output=None):
    source, run_id, attempt, number, started_at, matrix_passed = identity
    info = api.dataset_info(REPO, token=False)
    if info.id != REPO or info.private is not False or info.gated is not False or not SHA.fullmatch(info.sha):
        raise ValueError("expected the public, ungated qualification dataset")
    files = {entry.rfilename for entry in info.siblings}
    fetch = lambda path: Path(fetch_file(REPO, path, repo_type="dataset", revision=info.sha, token=False))
    state = prior_state(files, lambda path: read_json(fetch(path)))
    path = f"runs/{run_id}/{attempt}.parquet"
    if path in files:
        if path not in state["history"]:
            raise ValueError("run already exists outside the active publication history")
        run = load_history([path], fetch)[0]
        if run["source_sha"] != source:
            raise ValueError("published run source mismatch")
        if report_output is not None:
            report = {
                "schema_version": 1, "runs": load_history(state["history"], fetch),
                "active_baselines": state["baselines"],
            }
            summary_report(api, add, files, info.sha, report, run, report_output)
        return run
    if state["history"]:
        previous_id, previous_attempt = map(int, RUN_PATH.fullmatch(state["history"][0]).groups())
        if (number < state["last_run_number"]
                or (number == state["last_run_number"]
                    and (int(run_id) != previous_id or int(attempt) <= previous_attempt))):
            raise ValueError("older nightly qualification cannot replace newer publication")

    run = nightly_run(evidence, source, run_id, attempt, started_at, matrix_passed)
    if state["history"]:
        run["baseline_revision"] = info.sha
    run, baselines = apply_baselines(run, state["baselines"])
    history_paths = state["history"][:HISTORY_LIMIT - 1]
    history = load_history(history_paths, fetch)
    report = {"schema_version": 1, "runs": [run, *history], "active_baselines": baselines}
    manifest = {"schema_version": 1, "last_run_number": number,
                "history": [path, *history_paths], "baselines": baselines}
    with TemporaryDirectory(prefix="skippy-qualification-") as staging:
        output = Path(staging)
        parquet = output / path
        parquet.parent.mkdir(parents=True)
        write_run(parquet, run)
        write_json(output / "baselines.json", manifest)
        shutil.copyfile(AUDITS, output / "comparability.json")
        charts = dataset_charts(report)
        export_charts(charts, output)
        export_charts(health_charts(health_view(report, run["id"])), output / f"reports/{run_id}-{attempt}")
        (output / "README.md").write_text(dataset_card(report, charts), encoding="utf-8")
        staged = {file.relative_to(output).as_posix(): file for file in output.rglob("*") if file.is_file()}
        operations = [add(path_in_repo=name, path_or_fileobj=file) for name, file in sorted(staged.items())]
        operations.extend(delete(path_in_repo=name) for name in sorted(files - staged.keys())
                          if CHART_PATH.fullmatch(name))
        receipt = api.create_commit(
            repo_id=REPO, repo_type="dataset", operations=operations, parent_commit=info.sha,
            commit_message=f"Publish Skippy nightly inference qualification {run['id']}",
        )
        print(f"Published {receipt.commit_url}", flush=True)
        if report_output is not None:
            summary_report(api, add, staged.keys(), receipt.oid, report, run, report_output)
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    identity = admit(os.environ)
    if not os.environ.get("HF_TOKEN"):
        raise ValueError("HF_TOKEN is required for nightly publication")
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi, hf_hub_download

    report_output = args.evidence / "publication-report.json"
    run = publish(HfApi(), hf_hub_download, CommitOperationAdd, CommitOperationDelete,
                  args.evidence, identity, report_output=report_output)
    report = read_json(report_output)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
        stream.write(run_summary(report, run["id"], image_base_url=report["chart_base_url"]))
    view = health_view(report, run["id"])
    passed = (run["outcome"] == "passed" and not view["counts"].get("regressed")
              and not view["errors"] and not view["issues"])
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"Nightly inference publication failed: {error}")
