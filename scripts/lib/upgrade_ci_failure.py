"""Classify failed upgrade CI jobs before spending another source-repair attempt."""

import re

SOURCE_STEPS = {"Run just test", "Run just lint", "Run just llama-check",
                "Run just skippy-ffi-bindgen-check", "Run ./.github/actions/ci-package"}
ENVIRONMENT_ERRORS = (
    r"fatal error(?: C[0-9]+)?:.*nvml[.]h.*No such file or directory",
    r"fatal error:.*['\"]nvml[.]h['\"] file not found",
    r"No space left on device",
    r"Cannot connect to the Docker daemon",
    r"Error response from daemon:",
)


def classify(jobs, logs):
    failures = [job for job in jobs if not job["name"].endswith("/ Full CI gate")]
    if not failures:
        return {"repairable": False, "failure_class": "unknown", "reasons": ["no failed producer jobs found"]}
    reasons = []
    for job in failures:
        log = logs.get(job["id"], "")
        failed_steps = {step["name"] for step in job.get("steps", []) if step.get("conclusion") == "failure"}
        if any(re.search(pattern, log) for pattern in ENVIRONMENT_ERRORS):
            reasons.append(f"{job['name']}: missing build dependency or runner/image failure; repair the environment")
        elif not failed_steps or not failed_steps.issubset(SOURCE_STEPS):
            reasons.append(f"{job['name']}: failure outside candidate source checks: {', '.join(sorted(failed_steps))}")
    return {"repairable": not reasons, "failure_class": "infrastructure" if reasons else "candidate",
            "reasons": reasons}
