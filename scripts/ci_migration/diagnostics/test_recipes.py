import json
from pathlib import Path
import tempfile
import unittest

from run import ARCH_REF, RECIPES, execute

class RecipesTest(unittest.TestCase):
    def test_preserved_coverage(self):
        self.assertEqual(len(RECIPES), 7)
        for name, recipe in RECIPES.items():
            runs = [step["run"] for step in recipe["steps"]]
            self.assertEqual(runs[0], "python -m pip install -r requirements-dev.txt")
            self.assertFalse(any("upload-artifact" in run for run in runs))
            self.assertEqual(recipe["env"]["QT_QPA_PLATFORM"],
                             "windows" if name in {"display-session-contract", "runtime-credit-diagnostics"}
                             else "offscreen")
        display = RECIPES["display-session-contract"]["steps"][-1]["run"]
        self.assertIn("test_stop_all_dispatch.py", display)
        self.assertIn("--junitxml=display-evidence/junit.xml", display)
        self.assertEqual(RECIPES["runtime-a18-diagnostics"]["steps"][-1]["timeout-minutes"], "2")

    def test_credit_job_skips_without_touching_evidence(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            r = execute(RECIPES["runtime-credit-diagnostics"], "refs/heads/main", root, root)
            self.assertEqual(r, {"status": "skipped", "steps": []})
            self.assertEqual(list(root.iterdir()), [])

    def test_failure_does_not_prevent_validators_or_clear_outcome(self):
        sql = RECIPES["runtime-sqlite-failure-diagnostics"]
        validator = next(step["if"] for step in sql["steps"] if "!cancelled()" in step.get("if", ""))
        recipe = {"job_if": None, "env": {}, "steps": [
            {"ordinal": 1, "id": "observed_tests", "shell": "python", "run": "print('private'); raise SystemExit(2)"},
            {"ordinal": 2, "shell": "python", "run": "print('must skip')"},
            {"ordinal": 3, "shell": "python", "if": validator,
             "env": {"PRIOR": "${{ steps.observed_tests.outcome }}"},
             "run": "import os; assert os.environ['PRIOR'] == 'failure'"},
        ]}
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            r = execute(recipe, ARCH_REF, root, root)
            self.assertEqual(r["status"], "failure")
            self.assertEqual([step["status"] for step in r["steps"]], ["failure", "skipped", "success"])
            self.assertNotIn("private", json.dumps(r))
            self.assertIn("private", (root / "step-01.log").read_text())

if __name__ == "__main__":
    unittest.main()
