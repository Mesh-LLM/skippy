"""Private, bounded host operations for the four-model Metal profile."""

import hashlib
import ctypes
import json
import os
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen

URL = "http://127.0.0.1:19437"
IK = ["uvx", "--from", "inferencekit-cli==0.1.1", "inferencekit"]


class HostUnavailable(RuntimeError):
    """Conditions invalidate timing across every model in this attempt."""


def thermal_pressure():
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    library.notify_register_check.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
    library.notify_get_state.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint64)]
    library.notify_cancel.argtypes = [ctypes.c_int]
    registration = ctypes.c_int()
    if library.notify_register_check(b"com.apple.system.thermalpressurelevel", ctypes.byref(registration)):
        raise HostUnavailable("cannot read native thermal-pressure state")
    try:
        level = ctypes.c_uint64()
        if library.notify_get_state(registration, ctypes.byref(level)):
            raise HostUnavailable("cannot read native thermal-pressure level")
        return level.value
    finally:
        library.notify_cancel(registration)


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def clean_env(root):
    return {"PATH": os.environ["PATH"], "HOME": str(Path.home()),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"), "LANG": "en_US.UTF-8",
            "HF_HUB_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
            "INFERENCEKIT_RUNS_DIR": str(root / "evidence")}


def command(args, env, timeout=180, **kwargs):
    return subprocess.run(args, env=env, timeout=timeout, check=True, **kwargs)


def post(route, body, timeout=180):
    request = Request(URL + route, data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def telemetry(path, env):
    commands = [["sysctl", "vm.swapusage"], ["vm_stat"], ["pmset", "-g", "therm"],
                ["ps", "-axo", "pid=,pcpu=,rss=,comm="]]
    result = {"wall_ns": time.time_ns(), "native_thermal_pressure_level": thermal_pressure()}
    for args in commands:
        result[" ".join(args)] = command(args, env, capture_output=True, text=True).stdout
    save(path, result)
    return result


def competing_work(env, allowed_pid=None, *, allow_idle_ollama=False):
    """Reject known build/inference competitors; this does not prove global GPU isolation."""
    rows = command(["ps", "-axo", "pid=,comm="], env, capture_output=True, text=True).stdout
    builds = {"cargo", "rustc", "clang", "clang++", "cc1", "ninja", "make", "cmake", "ctest", "ld", "ld64"}
    inference = {"skippy", "llama-server", "llama-cli", "llama-bench", "ollama"}
    found = []
    for row in rows.splitlines():
        pid, executable = row.strip().split(None, 1)
        name = Path(executable).name
        if int(pid) != allowed_pid and name in builds | inference:
            found.append({"pid": int(pid), "program": name})
    if allow_idle_ollama and any(row["program"] == "ollama" for row in found):
        try:
            with urlopen("http://127.0.0.1:11434/api/ps", timeout=0.5) as response:
                if json.load(response)["models"] == []:
                    return [row for row in found if row["program"] != "ollama"]
        except (OSError, ValueError, KeyError):
            pass
    return found


def require_idle(env, allowed_pid=None, *, max_pressure=0):
    deadline = time.monotonic() + 300
    while True:
        competitors = competing_work(env, allowed_pid)
        pressure = thermal_pressure()
        if not competitors and pressure <= max_pressure:
            return
        if time.monotonic() >= deadline:
            raise HostUnavailable(f"host not ready: competitors={competitors}, thermal_pressure={pressure}")
        time.sleep(2)


def stop(child):
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=10)
            raise RuntimeError("server required forced cleanup")


def launch_args(cache, model, engine, lanes=1):
    model_path = str(Path(model["path"]).resolve(strict=True))
    if engine == "skippy":
        bundle = next((cache / "native-runtimes").glob("*/manifest.json")).parent
        return [str(cache / "skippy"), "--runtime-bundle", str(bundle), "serve",
                "--model-path", model_path, "--model-id", model["key"],
                "--bind-addr", "127.0.0.1:19437", "--ctx-size", "8192",
                "--generation-concurrency", str(lanes), "--n-gpu-layers", "999",
                "--speculative-strategy", "disabled", "--prefix-cache", "off",
                "--cache-idle-slots", "0", "--kv-cache-type-k", "f16", "--kv-cache-type-v", "f16",
                "--flash-attention", "enabled", "--batch-size", "512", "--microbatch-size", "512",
                "--threads", "16", "--threads-batch", "16", "--startup-timeout-secs", "300"]
    return [str(cache / "llama-build/bin/llama-server"), "-m", model_path,
            "--alias", model["key"], "--host", "127.0.0.1", "--port", "19437",
            "-c", str(8192 * lanes), "-np", str(lanes), "-ngl", "999", "-fa", "on",
            "-ctk", "f16", "-ctv", "f16", "-b", "512", "-ub", "512",
            "-t", "16", "-tb", "16", "--poll", "0", "--poll-batch", "0",
            "--cache-ram", "0", "--no-context-shift"]


@contextmanager
def server(cache, model, engine, directory, env, lanes=1, diagnostic=False, argv=None, startup_timeout=360):
    args = launch_args(cache, model, engine, lanes) if argv is None else list(argv)
    if diagnostic:
        args.insert(1, "--debug" if engine == "skippy" else "--verbose")
    save(directory / "launch.json", {"argv": args, "environment_names": sorted(env)})
    telemetry(directory / "before.json", env)
    with (directory / "server.log").open("wb") as stream:
        child = subprocess.Popen(args, env=env, stdout=stream, stderr=stream,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + startup_timeout
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError(f"{engine} startup failed: {child.returncode}")
                try:
                    with urlopen(URL + "/v1/models", timeout=2) as response:
                        if json.load(response).get("data"):
                            break
                except (OSError, ValueError):
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("server readiness timed out")
            yield child
        finally:
            stop(child)
            telemetry(directory / "after.json", env)


def body(model, prompt, count, stream=False):
    return {"model": model["key"], "prompt": prompt, "max_tokens": count,
            "temperature": 0, "seed": 42, "top_p": 1, "top_k": 0, "min_p": 0,
            "repeat_penalty": 1, "ignore_eos": True, "cache_prompt": False,
            "stream": stream, "stream_options": {"include_usage": True}}


def make_prompts(directory):
    prompts = []
    for length, output in ((512, 256), (4096, 64)):
        for index in range(2):
            text = f"Orchard journal sample {index}. Continue the journal.\n" + (
                "The orchard keeper records the rain and checks every tree each morning.\n" * 500)
            ids = post("/tokenize", {"content": text, "add_special": False})["tokens"][:length]
            text = post("/detokenize", {"tokens": ids})["content"]
            actual = post("/tokenize", {"content": text, "add_special": False})["tokens"]
            if actual != ids:
                raise ValueError("prompt does not round-trip through the native tokenizer")
            prompts.append({"id": f"p{length}-{index}", "bucket": f"p{length}",
                            "messages": [{"role": "user", "content": text}],
                            "max_tokens": output, "native_prompt_tokens": length,
                            "token_ids_sha256": hashlib.sha256(json.dumps(ids).encode()).hexdigest()})
    path = directory / "prompts.jsonl"
    path.write_text("".join(json.dumps(p) + "\n" for p in prompts))
    return path, prompts


def audit_prompt_tokens(model, engine, prompts, cell, round_index):
    """Count the exact tagged request text, outside the timing interval."""
    rows = []
    for line in prompts.read_text().splitlines():
        prompt = json.loads(line)
        salt = f"ik-r{round_index}" if round_index else None
        text = (f"[{salt}] " if salt else "") + prompt["messages"][0]["content"]
        if engine == "llama":
            ids = post("/tokenize", {"content": text, "add_special": False})["tokens"]
        else:
            digest = model["files"][0]["sha256"]
            identity = {"model_id": model["key"], "source_model_sha256": digest,
                        "tokenizer_id": "gguf-source-sha256:" + digest, "stage_index": 0}
            ids = post("/v1/tokenize", {"text": text, "add_special": False,
                       "expected_identity": identity})["token_ids"]
        token_hash = hashlib.sha256(json.dumps(ids).encode()).hexdigest()
        if not salt and token_hash != prompt["token_ids_sha256"]:
            raise ValueError("native tokenizer disagrees with the registered prompt token IDs")
        rows.append({"id": prompt["id"], "round": round_index, "salt": salt,
                     "prompt_tokens": len(ids), "token_ids_sha256": token_hash,
                     "prompt_sha256": hashlib.sha256(text.encode()).hexdigest()})
    save(cell / "prompt-token-counts.json", {"method": "untimed native tokenizer, add_special=false", "rows": rows})


def bench(model, engine, prompts, root, cell, env, previous=None, allowed_pid=None, round_index=0, label=None):
    audit_prompt_tokens(model, engine, prompts, cell, round_index)
    args = IK + ["bench", "serve", "--base-url", URL, "--model", model["key"],
                 "--api", "completions", "--engine-kind", "llamacpp", "--prompts", str(prompts),
                 "--rounds", "1", "--warmup", "2", "--seed", "42", "--token-ids", "off",
                 "--gpu-name", "Apple M1 Ultra", "--engine-version", model[engine + "_version"],
                 "--label", label or engine, "--run-dir", str(root), "--extra-body",
                 json.dumps({"seed": 42, "top_p": 1, "top_k": 0, "min_p": 0,
                             "repeat_penalty": 1, "cache_prompt": False}), "--json"]
    if previous:
        args += ["--extend", previous]
    require_idle(env, allowed_pid)
    deadline = time.monotonic() + 900
    with (cell / "bench.json").open("w") as stdout, (cell / "bench.stderr").open("w") as stderr:
        child = subprocess.Popen(args, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            while child.poll() is None:
                competitors = competing_work(env, allowed_pid)
                if competitors:
                    save(cell / "contention.json", {"competitors": competitors, "wall_ns": time.time_ns()})
                    raise HostUnavailable(f"competing work during timing: {competitors}")
                pressure = thermal_pressure()
                if pressure:
                    save(cell / "thermal-failure.json", {"native_thermal_pressure_level": pressure, "wall_ns": time.time_ns()})
                    raise HostUnavailable(f"non-nominal thermal pressure during timing: {pressure}")
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(args, 900)
                time.sleep(1)
            if child.returncode:
                raise subprocess.CalledProcessError(child.returncode, args)
        finally:
            if child.poll() is None:
                stop(child)
    record = json.loads((cell / "bench.json").read_text())
    if "evidence_id" not in record:
        raise ValueError(f"no benchmark evidence: {str(record)[:1000]}")
    return record
