"""Pull a hosted product and report its run-bound lab receipt; never fetch source."""

import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import ci_artifacts as artifacts
from .ci_process import capture

REPOSITORY = "Mesh-LLM/skippy"
WORKFLOW = ".github/workflows/hosted-lab.yml"
VARIANT = "darwin-aarch64-metal"


def api(path, *, body=None, method="GET", paginate=False):
    args = ["gh", "api", f"repos/{REPOSITORY}/{path}", "--method", method]
    if body is not None:
        result = subprocess.run([*args, "--input", "-"], input=json.dumps(body),
                                capture_output=True, text=True, check=True, timeout=60)
        return json.loads(result.stdout)
    return json.loads(capture([*args, *(["--paginate", "--slurp"] if paginate else [])]))


def admitted_run(run, *, rehearsal=None):
    if rehearsal is not None:
        if (not re.fullmatch(r"[0-9a-f]{40}", rehearsal)
                or run.get("event") != "push"
                or not (run.get("head_branch") or "").startswith("lab-rehearsal/")
                or run.get("head_sha") != rehearsal):
            raise ValueError("rehearsal requires an explicitly approved exact SHA on a rehearsal push")
    elif run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main":
        raise ValueError("requires a successful protected-main hosted-lab dispatch")
    if (run.get("path") != WORKFLOW or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or run.get("head_repository", {}).get("full_name") != REPOSITORY
            or not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", ""))
            or type(run.get("id")) is not int or run["id"] <= 0
            or type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0):
        raise ValueError("requires a successful protected-main hosted-lab dispatch")
    return {"run_id": run["id"], "attempt": run["run_attempt"], "source": run["head_sha"],
            "mode": "rehearsal" if rehearsal is not None else "production"}


def main_ancestor(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("expected full commit SHA")
    branch = api("branches/main")
    if branch.get("protected") is not True:
        raise ValueError("main must be protected")
    comparison = api(f'compare/{sha}...{branch["commit"]["sha"]}')
    if (comparison.get("status") not in ("ahead", "identical")
            or comparison.get("merge_base_commit", {}).get("sha") != sha):
        raise ValueError("commit is not on protected main")


def ready(run_id, *, rehearsal=None):
    run = api(f"actions/runs/{run_id}")
    identity = admitted_run(run, rehearsal=rehearsal)
    started = datetime.fromisoformat(run.get("run_started_at") or "")
    if rehearsal is None:
        main_ancestor(identity["source"])
    jobs = api(f'actions/runs/{run_id}/attempts/{identity["attempt"]}/jobs?per_page=100')
    required = {"plan", "quality", "package", "handoff"}
    if (jobs["total_count"] != 4 or {job["name"] for job in jobs["jobs"]} != required
            or any(job.get("conclusion") != "success" for job in jobs["jobs"])):
        raise ValueError("all hosted jobs and the mode-specific handoff must succeed in this attempt")
    # GitHub copies inherited jobs into the new attempt, with new IDs and
    # run_attempt values but their original execution timestamps.
    if any(datetime.fromisoformat(job.get("started_at") or "") < started for job in jobs["jobs"]):
        raise ValueError("all hosted jobs must execute again; partial reruns cannot authorize lab input")
    return identity


def current(receipt, *, rehearsal=None):
    identity = ready(receipt["run_id"], rehearsal=rehearsal)
    if any(receipt.get(key) != value for key, value in identity.items()):
        raise ValueError("run identity changed or attempt superseded")
    return identity


def unpack_download(archive, destination):
    # The Actions artifact contains just a product directory. Bound its size
    # before extracting, disallow links, traversal and duplicate file names.
    with zipfile.ZipFile(archive) as zipped:
        entries = zipped.infolist()
        paths = [artifacts.archive_path(entry.filename) for entry in entries]
        if len(set(paths)) != len(paths) or sum(entry.file_size for entry in entries) > 8 * 1024**3:
            raise ValueError("duplicate or oversized Actions artifact")
        expected = {f"skippy-{VARIANT}.tar.gz", f"skippy-{VARIANT}.tar.gz.sha256"}
        if {str(path) for path in paths} != expected:
            raise ValueError("lab input must contain only the product archive and checksum")
        for entry, path in zip(entries, paths):
            kind = (entry.external_attr >> 16) & 0o170000
            if kind not in (0, 0o100000, 0o040000) or not path.parts:
                raise ValueError("unsupported Actions artifact entry")
            target = destination / path
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)


def fetch(run_id, destination, *, rehearsal=None):
    if destination.exists():
        raise ValueError("download destination must not exist")
    identity = ready(run_id, rehearsal=rehearsal)
    if identity["run_id"] != run_id:
        raise ValueError("unexpected workflow run id")
    name = f'hosted-lab-{run_id}-{identity["attempt"]}-input'
    listing = api(f"actions/runs/{run_id}/artifacts?name={name}&per_page=100")
    matches = [item for item in listing["artifacts"] if item["name"] == name and not item["expired"]]
    if len(matches) != 1 or listing["total_count"] != 1:
        raise ValueError("requires exactly one unexpired run/attempt product")
    item = matches[0]
    if (type(item.get("id")) is not int or item["id"] <= 0
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("digest", ""))):
        raise ValueError("artifact requires GitHub id and SHA-256 digest")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        root = Path(directory)
        archive = root / "artifact.zip"
        with archive.open("wb") as output:
            subprocess.run(["gh", "api", f'repos/{REPOSITORY}/actions/artifacts/{item["id"]}/zip'],
                           stdout=output, check=True, timeout=600)
        if "sha256:" + artifacts.digest(archive) != item["digest"]:
            raise ValueError("Actions artifact download digest mismatch")
        stage = root / "stage"
        stage.mkdir()
        unpack_download(archive, stage)
        product = stage / f"skippy-{VARIANT}.tar.gz"
        artifacts.check_checksum(product)
        identity.update(variant=VARIANT, artifact_id=item["id"], artifact_digest=item["digest"],
                        archive_sha256=artifacts.digest(product))
        # Reruns during download cannot authorize the previous attempt.
        current(identity, rehearsal=rehearsal)
        artifacts.write_json(stage / "lab-input.json", identity)
        stage.rename(destination)
    return identity


def verify_input(directory, *, rehearsal=None):
    receipt = artifacts.read_json(directory / "lab-input.json")
    mode = "production" if rehearsal is None else "rehearsal"
    if (receipt.get("mode") != mode
            or (rehearsal is not None and receipt.get("source") != rehearsal)):
        raise ValueError("receipt mode/source differs from explicit operator approval")
    if (receipt.get("variant") != VARIANT
            or not re.fullmatch(r"[0-9a-f]{40}", receipt.get("source", ""))):
        raise ValueError("invalid lab input identity")
    archive = directory / f"skippy-{VARIANT}.tar.gz"
    artifacts.check_checksum(archive)
    if artifacts.digest(archive) != receipt.get("archive_sha256"):
        raise ValueError("downloaded product changed")
    return receipt


def report(directory, *, rehearsal=None):
    receipt = verify_input(directory, rehearsal=rehearsal)
    current(receipt, rehearsal=rehearsal)
    result = artifacts.read_json(directory / "evidence/lab-result.json")
    if result.get("input") != receipt or result.get("status") not in ("passed", "failed"):
        raise ValueError("qualification receipt differs from admitted artifact")
    if rehearsal is None:
        main_ancestor(result["harness_source"])
    elif result["harness_source"] != rehearsal:
        raise ValueError("rehearsal harness must match the explicitly approved SHA")
    key = f'{receipt["run_id"]}.{receipt["attempt"]}'
    prefix = "lab-rehearsal" if rehearsal is not None else "lab"
    context = f"{prefix}/metal/{key}"
    evidence = artifacts.files(directory / "evidence")
    evidence.pop("lab-result.json", None)
    if result.get("evidence") != evidence:
        raise ValueError("qualification evidence changed")
    pages = api(f'commits/{receipt["source"]}/status?per_page=100', paginate=True)
    statuses = [row for page in pages for row in page["statuses"]]
    status = next((row for row in statuses if row["context"] == context), None)
    if status is None or status["state"] != "pending":
        raise ValueError("requires pending handoff; dispatch a fresh run after a reported result")
    state = "success" if result["status"] == "passed" else "failure"
    label = "REHEARSAL, not certification" if rehearsal is not None else "Metal"
    description = f"{label} {result['status']}; sha256:{receipt['archive_sha256']}"
    # The commit comment makes the structured result remotely readable. Full logs
    # remain on the lab; include device lines and their complete file hashes.
    details = {"lab-result.json": result}
    for name in ("smoke.json", "quality.json", "performance.json"):
        path = directory / "evidence" / name
        if path.exists():
            value = artifacts.read_json(path)
            serialized = json.dumps(value, ensure_ascii=True)
            details[name] = value if len(serialized) < 10000 else {
                "truncated": True, "preview": serialized[:6000], "sha256": artifacts.digest(path),
            }
    for name in ("stdout.log", "stderr.log"):
        path = directory / "evidence" / name
        if path.exists():
            lines = path.read_text(errors="replace").splitlines()
            details[name] = "\n".join(line for line in lines if "backend_device_selected" in line or "offloaded" in line)[-6000:]
    text = f"### {label} evidence / {key}\n\n```json\n" + json.dumps(details) + "\n```"
    if len(text.encode()) > 60000:
        raise ValueError("structured lab evidence exceeds GitHub comment limit")
    current(receipt, rehearsal=rehearsal)
    comment = api(f'commits/{receipt["source"]}/comments', method="POST", body={"body": text})
    current(receipt, rehearsal=rehearsal)
    api(f'statuses/{receipt["source"]}', method="POST", body={
        "context": context, "state": state, "description": description,
        "target_url": comment["html_url"],
    })
