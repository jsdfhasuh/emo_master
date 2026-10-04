"""Protect cross-repository packager identity and the nonpublishing gate."""
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowContractTests(unittest.TestCase):
    def test_release_caller_pins_matching_packager_revision(self):
        for filename in ("release-windows.yml", "validate-windows-package.yml"):
            with self.subTest(workflow=filename):
                workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
                package = workflow["jobs"]["package"]
                ref = package["with"]["packager_ref"]
                self.assertRegex(ref, r"^[0-9a-f]{40}$")
                self.assertEqual(package["uses"],
                    "jsdfhasuh/python_build_scripts/.github/workflows/release-windows.yml@" + ref)
                self.assertEqual(package["with"]["target"], "emo-master")
                self.assertIs(package["with"]["publish_release"], False)

    def test_validation_cannot_publish_and_uses_exact_source(self):
        text = (ROOT / ".github/workflows/validate-windows-package.yml").read_text()
        workflow = yaml.safe_load(text)
        events = workflow.get("on", workflow.get(True))
        self.assertEqual(set(events), {"pull_request", "workflow_dispatch"})
        self.assertEqual(set(workflow["jobs"]), {"prepare", "package", "verify"})
        package = workflow["jobs"]["package"]
        self.assertNotIn("secrets", package)
        self.assertEqual(package["with"]["source_ref"], "${{ needs.prepare.outputs.source_sha }}")
        self.assertNotRegex(text, r"gh\s+release|git\s+(?:push|tag)|publish_release:\s*true")
        self.assertIn("--expected-source-sha", text)
        self.assertIn("if: always()", text)


if __name__ == "__main__":
    unittest.main()
