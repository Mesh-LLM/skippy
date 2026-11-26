#!/usr/bin/env python3
"""Generate and score the registered scheduler task-quality fixture offline."""

import argparse
import hashlib
import json
import math
import random
import re
from pathlib import Path


SEED = 42
ANSWER_LINE = re.compile(r"^\s*(?:ANSWER:\s*)?(-?\d+)\s*$", re.I)


def canonical_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def generate():
    rng = random.Random(SEED)
    items = []
    for i in range(64):
        a, b = rng.randrange(11, 98), rng.randrange(11, 98)
        operation = ("+", "-", "*")[i % 3]
        answer = {"+": a + b, "-": a - b, "*": a * b}[operation]
        items.append({"id": f"arithmetic-{i:03d}", "stratum": "arithmetic",
                      "task": f"Compute {a} {operation} {b}.", "answer": answer})

    names = ["amber", "birch", "cobalt", "delta", "ember", "finch", "grove", "harbor"]
    for i in range(64):
        keys = rng.sample(names, 5)
        values = rng.sample(range(-99, 100), 5)
        records = list(zip(keys, values))
        rng.shuffle(records)
        target = keys[i % 5]
        answer = dict(records)[target]
        entries = "; ".join(f"{key}={value}" for key, value in records)
        items.append({"id": f"extraction-{i:03d}", "stratum": "extraction",
                      "task": f"Records: {entries}. What integer is recorded for {target}?",
                      "answer": answer})

    for i in range(64):
        values = rng.sample(range(-150, 151), 7)
        rank = i % 5 + 2
        answer = sorted(values)[rank - 1]
        listed = ", ".join(map(str, values))
        items.append({"id": f"ordering-{i:03d}", "stratum": "ordering",
                      "task": f"Sort these distinct integers ascending: {listed}. What is item number {rank} (counting from 1)?",
                      "answer": answer})

    for i in range(64):
        x, y = rng.randrange(-20, 21), rng.randrange(-20, 21)
        add, subtract = rng.randrange(1, 14), rng.randrange(1, 14)
        if i % 2:
            lines = [f"x = {x}", f"y = {y}", f"x = x + {add}", "x, y = y, x", f"x = x - {subtract}"]
            answer = y - subtract
        else:
            lines = [f"x = {x}", f"y = {y}", f"y = y + {add}", "x, y = y, x", f"x = x - {subtract}"]
            answer = y + add - subtract
        items.append({"id": f"assignment-{i:03d}", "stratum": "assignment",
                      "task": "After these Python statements, what integer is x?\n" + "\n".join(lines),
                      "answer": answer})
    return {"schema": "scheduler-task-quality-v1", "seed": SEED,
            "instructions": "Solve the task. Reply with only the final integer on its own line, without an explanation.",
            "items": items}


def parse_answer(response):
    """Accept an integer, optionally prefixed by ANSWER:, on the final line."""
    lines = [line for line in response.splitlines() if line.strip()]
    match = ANSWER_LINE.fullmatch(lines[-1]) if lines else None
    return int(match.group(1)) if match else None


def score(manifest, response_rows):
    expected = {item["id"]: item for item in manifest["items"]}
    if len(expected) != 256 or len(response_rows) != 256:
        raise ValueError("expected exactly 256 manifest items and 256 responses")
    seen = set()
    outcomes = []
    for row in response_rows:
        item_id = row.get("id")
        if item_id not in expected or item_id in seen:
            raise ValueError(f"missing, duplicate, or unknown item ID: {item_id}")
        seen.add(item_id)
        if row.get("status") != "complete":
            raise ValueError(f"incomplete response: {item_id}")
        text = row.get("text")
        if not isinstance(text, str):
            raise ValueError(f"missing raw text: {item_id}")
        if row.get("input_tokens") != 512 or row.get("output_tokens", 0) > 32:
            raise ValueError(f"token shape mismatch: {item_id}")
        parsed = parse_answer(text)
        outcomes.append({"id": item_id, "stratum": expected[item_id]["stratum"],
                         "expected": expected[item_id]["answer"], "parsed": parsed,
                         "correct": parsed == expected[item_id]["answer"],
                         "raw_sha256": hashlib.sha256(text.encode()).hexdigest()})
    return {"schema": "scheduler-task-quality-score-v1", "manifest_sha256": digest(manifest),
            "correct": sum(item["correct"] for item in outcomes),
            "outcomes": sorted(outcomes, key=lambda item: item["id"])}


def binomial_cdf(k, n, probability):
    if probability == 1:
        return 1.0 if k == n else 0.0
    if probability == 0:
        return 1.0
    term = (1 - probability) ** n
    total = term
    for i in range(1, k + 1):
        term *= (n - i + 1) * probability / (i * (1 - probability))
        total += term
    return total


def one_sided_upper(k, n):
    if not 0 <= k <= n or n == 0:
        raise ValueError("invalid binomial count")
    if k == n:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(72):
        mid = (low + high) / 2
        if binomial_cdf(k, n, mid) > 0.05:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def compare(baseline, candidate):
    base = {row["id"]: row for row in baseline["outcomes"]}
    cand = {row["id"]: row for row in candidate["outcomes"]}
    if baseline["manifest_sha256"] != candidate["manifest_sha256"] or base.keys() != cand.keys():
        raise ValueError("score manifest or item mismatch")
    eligible = [item_id for item_id, row in base.items() if row["correct"]]
    adverse = sorted(item_id for item_id in eligible if not cand[item_id]["correct"])
    upper = one_sided_upper(len(adverse), len(eligible)) if eligible else None
    lower = 1 - one_sided_upper(len(eligible) - len(adverse), len(eligible)) if eligible else None
    verdict = "UNPROVEN"
    if len(eligible) >= 200:
        if upper < 0.02:
            verdict = "PASS"
        elif lower > 0.02:
            verdict = "FAIL"
    return {"baseline_correct": len(eligible), "adverse": len(adverse),
            "adverse_ids": adverse, "adverse_lower_95": lower, "adverse_upper_95": upper,
            "verdict": verdict}


def selftest():
    manifest = generate()
    assert len(manifest["items"]) == len({item["id"] for item in manifest["items"]}) == 256
    for item in manifest["items"]:
        if item["stratum"] == "arithmetic":
            match = re.fullmatch(r"Compute (\d+) ([+*-]) (\d+)\.", item["task"])
            assert match is not None
            a, op, b = match.groups()
            observed = {"+": lambda: int(a) + int(b),
                        "-": lambda: int(a) - int(b),
                        "*": lambda: int(a) * int(b)}[op]()
        elif item["stratum"] == "extraction":
            records, target = item["task"].split(". What integer is recorded for ")
            target = target.rstrip("?")
            observed = dict(entry.split("=") for entry in records.removeprefix("Records: ").split("; "))[target]
            observed = int(observed)
        elif item["stratum"] == "ordering":
            values, rank = item["task"].split(". What is item number ")
            integers = sorted(map(int, values.removeprefix("Sort these distinct integers ascending: ").split(", ")))
            observed = integers[int(rank.split()[0]) - 1]
        else:
            namespace = {}
            exec(item["task"].split("\n", 1)[1], {"__builtins__": {}}, namespace)
            observed = namespace["x"]
        assert observed == item["answer"], item["id"]
    assert parse_answer("reasoning 123\nANSWER: -7\n") == -7
    assert parse_answer("no numeric answer") is None
    assert parse_answer("42\nI cannot answer") is None
    assert math.isclose(one_sided_upper(0, 200), 1 - 0.05 ** (1 / 200), abs_tol=1e-12)
    assert one_sided_upper(1, 200) > 0.02
    rows = [{"id": item["id"], "status": "complete", "text": str(item["answer"]),
             "input_tokens": 512, "output_tokens": 2} for item in manifest["items"]]
    perfect = score(manifest, rows)
    assert perfect["correct"] == 256
    assert compare(perfect, perfect)["verdict"] == "PASS"
    rows[0]["text"] = "incorrect"
    degraded = score(manifest, rows)
    assert compare(perfect, degraded)["adverse"] == 1
    for row in rows[:10]:
        row["text"] = "incorrect"
    assert compare(perfect, score(manifest, rows))["verdict"] == "FAIL"
    empty = score(manifest, [{**row, "text": "incorrect"} for row in rows])
    assert compare(empty, perfect)["verdict"] == "UNPROVEN"
    print(digest(manifest))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    generate_cmd = sub.add_parser("generate")
    generate_cmd.add_argument("--output", type=Path, required=True)
    score_cmd = sub.add_parser("score")
    score_cmd.add_argument("--manifest", type=Path, required=True)
    score_cmd.add_argument("--responses", type=Path, required=True)
    score_cmd.add_argument("--output", type=Path, required=True)
    compare_cmd = sub.add_parser("compare")
    compare_cmd.add_argument("--baseline", type=Path, required=True)
    compare_cmd.add_argument("--candidate", type=Path, required=True)
    sub.add_parser("selftest")
    args = parser.parse_args()
    if args.command == "selftest":
        selftest()
    elif args.command == "generate":
        args.output.write_bytes(canonical_bytes(generate()))
        print(hashlib.sha256(args.output.read_bytes()).hexdigest())
    elif args.command == "score":
        manifest = json.loads(args.manifest.read_text())
        responses = json.loads(args.responses.read_text())
        args.output.write_bytes(canonical_bytes(score(manifest, responses)))
        print(hashlib.sha256(args.output.read_bytes()).hexdigest())
    else:
        baseline = json.loads(args.baseline.read_text())
        candidate = json.loads(args.candidate.read_text())
        print(json.dumps(compare(baseline, candidate), sort_keys=True))


if __name__ == "__main__":
    main()
