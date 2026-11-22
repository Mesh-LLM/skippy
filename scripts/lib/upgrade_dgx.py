"""Retain strict source-bound evidence for every upgrade Spark configuration."""

from pathlib import Path

from .ci_artifacts import digest, read_json, write_json
from .inference_ladder import plan
from .inference_admission import split_admissions_digest
from .inference_throughput import fetch_artifact, successful_run


def validate(qualification, quality, provenance, row, sha, key, admissions=None):
    expected = {"source_sha": sha, "model": key, "mode": row["mode"],
                "profile": "upgrade", "status": "passed"}
    if any(qualification.get(field) != value for field, value in expected.items()):
        raise ValueError("upgrade DGX qualification identity mismatch")
    if row["mode"] == "split":
        if admissions is None or qualification.get("split_admissions_sha256") != split_admissions_digest(admissions):
            raise ValueError("upgrade requires matching native split admission evidence")
    if (qualification.get("artifact_manifest_sha256") != digest(Path("ci/inference-artifacts.json"))
            or provenance.get("stack_commit") != sha):
        raise ValueError("upgrade DGX fixture or executable provenance mismatch")
    cases = read_json(Path("ci/inference-quality.json"))
    if (not isinstance(quality, list) or len(quality) != len(cases)
            or any(item.get("passed") is not True or item.get("case") != case
                   for item, case in zip(quality, cases))):
        raise ValueError("incomplete upgrade DGX quality evidence")


def collect_upgrade(pilot, job, row, sha, output, key):
    run = successful_run(job, sha)
    payload = {name: fetch_artifact(pilot, run, name + ".json")
               for name in ("qualification", "quality", "provenance")}
    if row["mode"] == "split":
        payload["admissions"] = fetch_artifact(pilot, run, "split-admissions.json")
    validate(**payload, row=row, sha=sha, key=key)
    write_json(output / f'{row["id"]}-qualification.json', dict(payload, job_id=job["id"], run_id=run))


def admit_dgx(directory, sha, upstream=None):
    for row in plan("upgrade", sha)["rows"]:
        key = row["id"].removesuffix("-" + row["mode"])
        payload = read_json(directory / f'{row["id"]}-qualification.json')
        if upstream is not None and payload["provenance"].get("native_upstream") != upstream:
            raise ValueError("DGX native upstream differs from qualified candidate")
        validate(payload["qualification"], payload["quality"], payload["provenance"], row, sha, key, payload.get("admissions"))
