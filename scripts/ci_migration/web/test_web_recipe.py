import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from run_web_recipe import RECIPE, execute, select


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.recipe = json.loads(RECIPE.read_text())

    def test_exact_trigger_contract(self):
        triggers = self.recipe["triggers"]
        self.assertEqual(triggers["push"]["branches"], ["main", "codex/logging-and-designer-improvements"])
        self.assertEqual(triggers["pull_request"]["branches"], ["main"])
        self.assertEqual(triggers["push"]["paths"], triggers["pull_request"]["paths"])
        self.assertIn("workflow_dispatch", triggers)

    def test_platform_and_source_event_select_correct_web_checks(self):
        job = "web-panel"
        _, linux = select(self.recipe, job, "ubuntu-latest", "pull_request")
        _, windows_manual = select(self.recipe, job, "windows-latest", "workflow_dispatch")
        _, windows_auto = select(self.recipe, job, "windows-latest", "push")
        self.assertEqual(linux, windows_manual)
        self.assertEqual({step["name"] for step in linux if step not in windows_auto},
                         {"Frontend logic regressions", "Current-target catalog refresh browser checks",
                          "Editor draft recovery browser checks", "Editor Dispatch capture isolation browser checks"})
        self.assertEqual([step["name"] for step in windows_auto if step not in linux],
                         ["Frontend platform regressions"])
        with self.assertRaises(ValueError):
            select(self.recipe, job, "windows-latest", "repository_dispatch")

    def test_owning_runtime_is_windows_with_all_checks(self):
        with self.assertRaises(ValueError):
            select(self.recipe, "product-runtime", "ubuntu-latest", "push")
        job, steps = select(self.recipe, "product-runtime", "windows-latest", "pull_request")
        self.assertEqual(job["env"]["EMO_WEB_PRODUCT_RUNTIME"], "1")
        self.assertIn("product_runtime_e2e.py", steps[-1]["run"])

    def test_raw_output_stays_in_local_evidence_and_failure_blocks(self):
        probe = {"jobs": {"web-panel": {"platforms": ["ubuntu-latest"], "cwd": ".",
                 "env": {}, "steps": [{"id": 1, "full_only": False, "subset_only": False,
                                       "shell": "bash", "run": "echo private; echo secret >&2; exit 7"},
                                      {"id": 2, "full_only": False, "subset_only": False,
                                       "shell": "bash", "run": "exit 0"}]}}}
        with tempfile.TemporaryDirectory() as root:
            evidence = Path(root) / "evidence"
            output = io.StringIO()
            with patch.dict(os.environ, {"RUNNER_OS": "Linux"}), redirect_stdout(output):
                result = execute(probe, "web-panel", "ubuntu-latest", "push", Path(root), evidence)
            self.assertEqual(result, 1)
            self.assertNotIn("private", output.getvalue())
            self.assertNotIn("secret", output.getvalue())
            self.assertIn("secret", (evidence / "step-01.log").read_text())
            self.assertFalse((evidence / "step-02.log").exists())


if __name__ == "__main__":
    unittest.main()
