"""Bind performance series to benchmark behavior and explicitly audited history."""

import ast
import hashlib
import json
import re
from pathlib import Path

AUDITS = Path(__file__).resolve().parents[2] / "ci/inference-comparability.json"


def benchmark_tree(node):
    """Keep semantic AST fields stable across Python's added optional fields."""
    if isinstance(node, ast.AST):
        return {"node": type(node).__name__, **{
            field: benchmark_tree(value) for field, value in ast.iter_fields(node)
            if value is not None and value != []
        }}
    if isinstance(node, list):
        return [benchmark_tree(value) for value in node]
    if isinstance(node, bytes):
        return {"bytes": node.hex()}
    return node


def benchmark_functions(path, names, *, source=None):
    tree = ast.parse(Path(path).read_text(encoding="utf-8") if source is None else source)
    selected = {node.name: benchmark_tree(node)
                for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names}
    if set(selected) != set(names):
        raise ValueError(f"benchmark functions missing from {path}")
    referenced = {node.id for function in tree.body
                  if isinstance(function, ast.FunctionDef) and function.name in names
                  for node in ast.walk(function) if isinstance(node, ast.Name)}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in referenced for target in node.targets
        ):
            selected["constant:" + json.dumps(benchmark_tree(node.targets[0]), sort_keys=True)] = benchmark_tree(node)
    encoded = json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def canonical_contract(configuration, workload, fingerprint):
    if not fingerprint:
        return None
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise ValueError("invalid inference recipe fingerprint")
    audit = json.loads(AUDITS.read_text(encoding="utf-8"))
    if audit.get("schema_version") != 1:
        raise ValueError("unsupported inference comparison audit")
    matches = {entry["recipe_sha256"] for entry in audit["series"]
               if entry["configuration"] == configuration and entry["workload"] == workload
               and fingerprint in (entry["legacy_sha256"], entry["recipe_sha256"])}
    if len(matches) > 1:
        raise ValueError("ambiguous inference comparison audit")
    return next(iter(matches), fingerprint)


def contracts_match(configuration, workload, left, right):
    return bool(left and right and canonical_contract(configuration, workload, left)
                == canonical_contract(configuration, workload, right))
