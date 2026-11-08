"""Dependency-free checks for CI selection; also run in the lightweight scope job."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

from scripts.ci_scope import changedPaths, needsFullChecks, selectChecks


class ScopeRulesTests(unittest.TestCase):
    def testPassiveDocsAndIndependentWebDoNotNeedFullSourceChecks(self):
        for paths in (["AGENTS.md"], ["README.md", "docs/plans/ci.md", "docs/screens/view.png"],
                      ["web_panel/frontend/src/main.tsx"],
                      ["web_panel/backend/emo_web_panel/app.py", "docs/plans/web.md"]):
            with self.subTest(paths=paths):
                self.assertFalse(needsFullChecks(paths))

    def testSourceSharedInputsAndUnknownPathsKeepFullCoverage(self):
        for path in ("src/emo_master/app_version.py", "tests/runtime/test_stop.py", "proto/runtime.proto",
                     "requirements-dev.txt", "pyproject.toml", "packages/emo_panel_protocol/pyproject.toml",
                     "scripts/ci_scope.py", ".github/workflows/ci.yml", "new-config.toml",
                     "docs/operator-development-guide.md", "docs/development/panel-contract-v1.md",
                     "docs/evidence/p4/c-accepted-visible/screens/two-pages.json", "docs/example.py"):
            with self.subTest(path=path):
                self.assertTrue(needsFullChecks(["README.md", "web_panel/frontend/src/main.tsx", path]))

    def testManualAndNewBranchEventsKeepFullCoverage(self):
        for name, event in (("workflow_dispatch", {}), ("push", {"before": "0" * 40}),
                            ("pull_request", {})):
            with self.subTest(name=name, event=event):
                full, _, _ = selectChecks(name, event, Path.cwd())
                self.assertTrue(full)


class GitScopeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        self.git("init", "-q")
        self.git("config", "user.name", "CI scope test")
        self.git("config", "user.email", "ci-scope@example.invalid")
        self.write("src/product.py", "value = 1\n")
        self.write("README.md", "Initial documentation\n")
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, stderr=subprocess.PIPE).decode().strip()

    def write(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "-qm", "Scope fixture")
        return self.git("rev-parse", "HEAD")

    def decision(self, name="push"):
        event = ({"pull_request": {"base": {"sha": self.base}}} if name == "pull_request" else
                 {"before": self.base})
        return selectChecks(name, event, self.repo)

    def testCompleteRangeIncludesSourceEvenWhenLastCommitOnlyChangesDocs(self):
        self.write("src/product.py", "value = 2\n")
        self.commit()
        self.write("README.md", "Updated documentation\n")
        self.commit()
        for name in ("push", "pull_request"):
            with self.subTest(event=name):
                full, _, paths = self.decision(name)
                self.assertTrue(full)
                self.assertEqual(set(paths), {"src/product.py", "README.md"})

    def testSourceMovedIntoDocsIsStillASourceChange(self):
        (self.repo / "docs").mkdir()
        (self.repo / "src/product.py").rename(self.repo / "docs/old-product.txt")
        self.commit()
        self.assertEqual(set(changedPaths(self.base, self.repo)), {"src/product.py", "docs/old-product.txt"})
        self.assertTrue(self.decision()[0])

    def testSourceDeletionKeepsFullCoverage(self):
        (self.repo / "src/product.py").unlink()
        self.commit()
        self.assertTrue(self.decision()[0])

    def testWebOnlySkipRequiresTheIndependentWebWorkflow(self):
        self.write("web_panel/frontend/src/main.tsx", "export const title = 'Web';\n")
        self.commit()
        self.assertFalse(self.decision("pull_request")[0])
        for ref, expected in (("refs/heads/main", False),
                              ("refs/heads/agent/runtime-workflow-architecture-v1", True)):
            with self.subTest(ref=ref):
                full, _, _ = selectChecks("push", {"before": self.base, "ref": ref}, self.repo)
                self.assertEqual(full, expected)

    def testUnavailableBaseFallsBackToFullChecks(self):
        full, reason, _ = selectChecks("push", {"before": "f" * 40}, self.repo)
        self.assertTrue(full)
        self.assertIn("unavailable", reason)

    def testCliReportsDocsOnlyWithoutClaimingTestsPassed(self):
        self.write("docs/notes with spaces.md", "Documentation only\n")
        self.commit()
        event = self.repo / "event.json"
        event.write_text(json.dumps({"before": self.base}), encoding="utf-8")
        output = self.repo / "output.txt"
        summary = self.repo / "summary.md"
        script = Path(__file__).resolve().parents[1] / "scripts/ci_scope.py"
        env = dict(os.environ, GITHUB_EVENT_NAME="push", GITHUB_EVENT_PATH=str(event),
                   GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary))
        completed = subprocess.run([sys.executable, str(script)], cwd=self.repo, env=env,
                                   check=True, capture_output=True, text=True)
        self.assertFalse(json.loads(completed.stdout)["run_full"])
        self.assertEqual(output.read_text(encoding="utf-8"), "run_full=false\n")
        self.assertIn("not required", summary.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
