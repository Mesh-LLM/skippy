"""Admission for public nightly reports from the protected inference controller."""

SOURCE_REPO = "Mesh-LLM/skippy"


def publication_allowed(environment, profile):
    expected = {
        "GITHUB_REPOSITORY": SOURCE_REPO,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": f"{SOURCE_REPO}/.github/workflows/inference.yml@refs/heads/main",
    }
    return (
        profile == "nightly"
        and environment.get("GITHUB_EVENT_NAME") in {"schedule", "workflow_dispatch"}
        and all(environment.get(key) == value for key, value in expected.items())
        and bool(environment.get("GITHUB_SHA"))
        and environment.get("GITHUB_WORKFLOW_SHA") == environment.get("GITHUB_SHA")
    )
