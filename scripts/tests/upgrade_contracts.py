"""Offline regressions for native recipe identity and bounded upgrade repair."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.ci_artifacts import read_json
from lib.upgrade_source import git
from lib.upgrade_repair import repair

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40


class Repair(unittest.TestCase):
    def test_packaged_recipe_has_source_identity_without_an_architecture_roster(self):
        from lib.native_recipe import write_native_recipe
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {"llama_cpp/upstream.txt": SHA,
                     "llama_cpp/patches/0001.patch": "fixture patch",
                     "llama_cpp/patches/model_support/series": "0001.patch\n",
                     "llama_cpp/patches/model_support/0001.patch": "model patch",
                     "llama_cpp/patches/generated/series": "0001.patch\n",
                     "llama_cpp/patches/generated/0001.patch": "generated patch"}
            for name, text in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            (root / "crates/skippy-api/src").mkdir(parents=True)
            write_native_recipe(root)
            recipe = read_json(root / "crates/skippy-api/src/native-recipe.json")
            self.assertEqual(set(recipe), {"llama_upstream_sha", "patch_queue_sha256"})
            self.assertEqual(recipe["llama_upstream_sha"], SHA)
            before = recipe["patch_queue_sha256"]
            (root / "llama_cpp/patches/0001.patch").write_text("changed patch")
            write_native_recipe(root)
            self.assertNotEqual(read_json(root / "crates/skippy-api/src/native-recipe.json")["patch_queue_sha256"], before)

    def checkout(self, root):
        git(root, "init", "--initial-branch=main")
        (root / "llama_cpp").mkdir()
        (root / "llama_cpp/upstream.txt").write_text(SHA + "\n")
        git(root, "add", ".")
        git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@skippy.local",
            "-c", "commit.gpgsign=false", "commit", "-m", "base")

    def test_agent_cannot_change_qualification_or_selected_upstream(self):
        for path in ("scripts/upgrade-publish.py", "llama_cpp/upstream.txt"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.checkout(root)
                def agent(*_args, **_kwargs):
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("changed\n")
                with patch.dict(os.environ, SKIPPY_UPGRADE_REPAIR_COMMAND='["agent"]'), \
                     patch("lib.upgrade_repair.command", side_effect=agent):
                    with self.assertRaisesRegex(ValueError, "allowlist|upstream pin"):
                        repair(root, root)

    def test_missing_agent_command_is_an_explicit_failure(self):
        with patch.dict(os.environ, SKIPPY_UPGRADE_REPAIR_COMMAND="[]"):
            with self.assertRaisesRegex(ValueError, "installed agent"):
                repair(ROOT, ROOT)


if __name__ == "__main__":
    unittest.main()
