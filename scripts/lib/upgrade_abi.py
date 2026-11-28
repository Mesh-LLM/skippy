"""Require distinct ABI identities for changed, consumed C declarations."""

import re

from .ci_artifacts import write_json
from .ci_process import command

FACADE = "crates/skippy-ffi/src/lib.rs"
BINDINGS = tuple(f"crates/skippy-ffi/src/bindings/{name}.rs" for name in ("skippy", "llama", "mtmd"))
VERSION = re.compile(r"^pub const ABI_VERSION_(MAJOR|MINOR|PATCH): u32 = ([0-9]+);$", re.MULTILINE)
PATCH = re.compile(r"(^pub const ABI_VERSION_PATCH: u32 = )[0-9]+(;$)", re.MULTILINE)
DOC = re.compile(r'#\[doc\s*=\s*"(?:[^"\\]|\\.)*"\s*\]')
TOKENS = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z_0-9]*|[^\s]', re.DOTALL)


def abi_version(text):
    fields = VERSION.findall(text)
    values = dict(fields)
    if len(fields) != 3 or set(values) != {"MAJOR", "MINOR", "PATCH"}:
        raise ValueError("expected exactly three literal Rust ABI version constants")
    return tuple(int(values[name]) for name in ("MAJOR", "MINOR", "PATCH"))


def check_version_edit(before, after):
    abi_version(before)
    abi_version(after)
    if PATCH.sub(r"\g<1>0\2", before) != PATCH.sub(r"\g<1>0\2", after):
        raise ValueError("upgrade repair may only change the ABI_VERSION_PATCH literal in lib.rs")


def declarations(text):
    # Bindgen emits quoted doc attributes. Keep other attributes and literal
    # contents (including link names); ignore only docs, comments and spacing.
    text = DOC.sub("", text)
    text = re.sub(r"^pub const SKIPPY_ABI_VERSION_(?:MAJOR|MINOR|PATCH): u32 = [0-9]+;\s*$",
                  "", text, flags=re.MULTILINE)
    return tuple(token for token in TOKENS.findall(text) if not token.startswith(("//", "/*")))


def revision_file(checkout, path):
    return command(["git", "-C", str(checkout), "show", f"HEAD:{path}"], capture_stdout=True)


def verify_abi(checkout):
    baseline = abi_version(revision_file(checkout, FACADE))
    candidate = abi_version((checkout / FACADE).read_text())
    changed = [path for path in BINDINGS
               if declarations(revision_file(checkout, path)) != declarations((checkout / path).read_text())]
    reason = ("ABI version must not decrease" if candidate < baseline else
              "consumed FFI declarations changed: bump ABI_VERSION_PATCH and the native header in the ordered patch queue"
              if changed and candidate == baseline else None)
    report = checkout / "target/llama-regeneration/abi-compatibility.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    write_json(report, {"base_abi": baseline, "candidate_abi": candidate, "changed_bindings": changed,
                        "status": "fail" if reason else "pass", "reason": reason})
    if reason:
        raise ValueError(f"{reason}; ABI comparison: {report}")
    print(f"ABI comparison passed: {baseline} -> {candidate}; {len(changed)} changed binding files", flush=True)
