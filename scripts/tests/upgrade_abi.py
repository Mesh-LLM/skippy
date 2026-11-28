"""Exercise ABI admission and the repair permission through offline fixtures."""

import json
import os
import unittest
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from lib.upgrade_abi import BINDINGS, FACADE, declarations, verify_abi
from lib.upgrade_lifecycle import verify_or_repair
from lib.upgrade_repair import repair
from lib.upgrade_source import check_changes
from lib.upgrade_verification import verify_offline
from upgrade_lifecycle import fixture, commit


ROOT = Path(__file__).resolve().parents[2]


def seed_abi_fixture(checkout):
    facade = checkout / FACADE
    facade.parent.mkdir(parents=True, exist_ok=True)
    facade.write_text("pub const ABI_VERSION_MAJOR: u32 = 0;\n"
                      "pub const ABI_VERSION_MINOR: u32 = 1;\n"
                      "pub const ABI_VERSION_PATCH: u32 = 87;\n"
                      "pub fn existing_export() {}\n")
    for path in BINDINGS:
        target = checkout / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('#[repr(C)]\npub struct Params { pub count: i32 }\n')


class AbiAdmission(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.checkout = fixture(self.root)
        seed_abi_fixture(self.checkout)
        self.base = commit(self.checkout, "baseline ABI")
        self.facade = self.checkout / FACADE
        self.binding = self.checkout / BINDINGS[1]

    def change_layout(self):
        self.binding.write_text(self.binding.read_text().replace("pub count: i32", "pub count: i32, pub tail: u64"))

    def bump(self):
        self.facade.write_text(self.facade.read_text().replace("PATCH: u32 = 87", "PATCH: u32 = 88"))

    def report(self):
        return json.loads((self.checkout / "target/llama-regeneration/abi-compatibility.json").read_text())

    def test_appended_struct_field_without_bump_is_rejected_before_offline_commands(self):
        self.change_layout()
        with patch("lib.upgrade_verification.command") as commands:
            with self.assertRaisesRegex(ValueError, "consumed FFI declarations changed: bump"):
                verify_offline(self.checkout)
        commands.assert_not_called()
        self.assertEqual(self.report()["changed_bindings"], [BINDINGS[1]])
        self.assertEqual(self.report()["status"], "fail")

    def test_changed_function_signature_and_constants_require_bump(self):
        for declaration in ('unsafe extern "C" { pub fn defaults() -> Params; }',
                            'pub const MODEL_FLAG: u32 = 16;'):
            with self.subTest(declaration=declaration):
                self.binding.write_text(declaration)
                with self.assertRaisesRegex(ValueError, "bump ABI_VERSION_PATCH"):
                    verify_abi(self.checkout)

    def test_changed_layout_with_bump_passes_repeated_verification(self):
        self.change_layout()
        self.bump()
        for _ in range(2):
            verify_abi(self.checkout)
        self.assertEqual(self.report()["candidate_abi"], [0, 1, 88])
        self.assertEqual(self.report()["status"], "pass")

    def test_docs_comments_and_whitespace_do_not_need_bump(self):
        self.binding.write_text('/* generator comment */\n#[doc = "new docs with \\\"quotes\\\" and // text"]\n'
                                '#[repr(C)] pub struct Params {\n pub count : i32\n}\n// tail comment\n')
        verify_abi(self.checkout)
        self.assertEqual(self.report()["changed_bindings"], [])

    def test_link_names_and_string_literal_contents_are_not_erased(self):
        self.assertNotEqual(declarations('#[link_name = "symbol // first"] pub fn a();'),
                            declarations('#[link_name = "symbol // second"] pub fn a();'))

    def test_version_only_generated_binding_change_does_not_claim_layout_change(self):
        binding = self.checkout / BINDINGS[0]
        binding.write_text(binding.read_text() + 'pub const SKIPPY_ABI_VERSION_PATCH: u32 = 88;\n')
        self.bump()
        verify_abi(self.checkout)
        self.assertEqual(self.report()["changed_bindings"], [])

    def test_version_rollback_is_rejected_even_without_layout_change(self):
        self.facade.write_text(self.facade.read_text().replace("PATCH: u32 = 87", "PATCH: u32 = 86"))
        with self.assertRaisesRegex(ValueError, "must not decrease"):
            verify_abi(self.checkout)

    def test_missing_generated_binding_fails_instead_of_skipping_admission(self):
        self.binding.unlink()
        with self.assertRaises(FileNotFoundError):
            verify_abi(self.checkout)

    def test_version_literal_and_abi_readme_can_be_repaired(self):
        self.bump()
        (self.checkout / "crates/skippy-ffi/README.md").write_text("ABI migration\n")
        check_changes(self.checkout, "HEAD")

    def test_other_rust_edits_fail_for_dirty_and_frozen_candidates(self):
        for replacement in ("pub fn substituted_export() {}", "pub fn existing_export() { panic!() }"):
            with self.subTest(replacement=replacement):
                self.facade.write_text(self.facade.read_text().replace("pub fn existing_export() {}", replacement))
                with self.assertRaisesRegex(ValueError, "only change the ABI_VERSION_PATCH literal"):
                    check_changes(self.checkout, "HEAD")
                commit(self.checkout, "forbidden Rust edit")
                with self.assertRaisesRegex(ValueError, "only change the ABI_VERSION_PATCH literal"):
                    check_changes(self.checkout, self.base, "HEAD")
                # Reset this fixture to test another independent substitution.
                self.facade.write_text(self.facade.read_text().replace(replacement, "pub fn existing_export() {}"))
                commit(self.checkout, "restore baseline")

    def test_major_minor_and_nonliteral_changes_are_rejected(self):
        original = self.facade.read_text()
        for text in (original.replace("MINOR: u32 = 1", "MINOR: u32 = 2"),
                     original.replace("PATCH: u32 = 87", "PATCH: u32 = 87 + 1")):
            self.facade.write_text(text)
            with self.assertRaises(ValueError):
                check_changes(self.checkout, "HEAD")

    def test_missing_bump_returns_to_agent_with_permission_and_rechecks_before_gates(self):
        self.change_layout()
        diagnostics = self.root / "diagnostics"
        commands = Mock()
        def agent(argv, **_kwargs):
            prompt = argv[-1]
            for instruction in ("by-value returns", "ABI_VERSION_PATCH literal", "Recipe pinning is not a substitute"):
                self.assertIn(instruction, prompt)
            feedback = json.loads((diagnostics / "last-check.json").read_text())
            self.assertIn("bump ABI_VERSION_PATCH", feedback["error"])
            commands.assert_not_called()
            self.bump()
        with patch.dict(os.environ, SKIPPY_UPGRADE_REPAIR_COMMAND='["agent"]'), \
             patch("lib.upgrade_repair.command", side_effect=agent) as invoked, \
             patch("lib.upgrade_verification.command", commands):
            verify_or_repair(self.checkout, diagnostics, lambda: verify_offline(self.checkout),
                             repair, lambda *_: None)
        invoked.assert_called_once()
        self.assertEqual(commands.call_count, 3)
        self.assertEqual(self.report()["status"], "pass")

    def test_abi_report_survives_preparation_failure_without_native_checkout(self):
        self.change_layout()
        with self.assertRaises(ValueError):
            verify_abi(self.checkout)
        save = run_path(str(ROOT / "scripts/upgrade-source.py"))["save_preparation_diagnostics"]
        save(self.checkout, self.root / "saved")
        self.assertEqual(json.loads((self.root / "saved/abi-compatibility.json").read_text()), self.report())


if __name__ == "__main__":
    unittest.main()
