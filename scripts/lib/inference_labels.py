"""Display labels for qualification records and charts."""

from functools import cache

from .ci_artifacts import read_json
from .inference_ladder import LADDER


@cache
def model_names():
    return {
        model["id"]: model["model"].rsplit("/", 1)[-1].removesuffix("-Instruct")
        for model in read_json(LADDER)["models"]
    }


def configuration_label(configuration):
    if configuration.startswith("metal-"):
        model = configuration.removeprefix("metal-").removesuffix("-standalone")
        name = model_names().get(model, model)
        return f"Metal - {name} (M3 Ultra)"
    model, _, placement = configuration.rpartition("-")
    count = {"standalone": "1 Spark", "split": "2 Sparks"}.get(placement)
    name = model_names().get(model)
    return f"{name} ({count})" if name and count else configuration


def workload_label(workload):
    return {
        "DGX Spark AgentX nightly v1": "Nightly AgentX (v1)",
        "DGX Spark smoke v1": "Smoke test (v1)",
        "DGX Spark warm probe v2": "Warm throughput probe (v2)",
        "DGX Spark smoke v1 (experimental split)": "Smoke test (v1, experimental split)",
        "Apple M3 Ultra AgentX nightly v1": "Nightly AgentX (v1)",
        "Apple M3 Ultra smoke v1": "Smoke test (v1)",
        "DGX Spark AgentX release v1": "Release AgentX (v1)",
        "Apple M3 Ultra AgentX release v1": "Release AgentX (v1)",
    }.get(workload, workload)
