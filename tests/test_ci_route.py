from pathlib import Path
import unittest

from scripts.ci_route import ACTIVE, ARCHITECTURE_REF, load_trigger_table, route_verified_event


class LegacyRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.table = load_trigger_table(root / ".github" / "workflows")

    def route(self, **kwargs):
        return route_verified_event(self.table, **kwargs)

    def test_current_trigger_inventory_and_tag_only_release(self):
        self.assertEqual(tuple(self.table), ACTIVE)
        self.assertEqual(len(self.table), 12)
        self.assertEqual(self.route(event="push", ref="refs/tags/v1.0.0"), ("release-windows",))
        self.assertEqual(self.route(event="push", ref="refs/tags/ci-validation"), ())

    def test_pr_base_path_and_root_only_glob(self):
        pr = dict(event="pull_request", ref="refs/heads/main")
        self.assertEqual(self.route(**pr, paths=["docs/requirements-dev.txt"]), ("ci",))
        self.assertEqual(self.route(**pr, paths=["requirements-dev.txt"]), ("ci", "web-panel-ci"))
        self.assertEqual(self.route(**pr, paths=["src/emo_master/app_version.py"]),
                         ("ci", "web-panel-ci", "validate-windows-package"))

    def test_architecture_trigger_preserves_diagnostics_and_manual_stop(self):
        push = dict(event="push", ref=ARCHITECTURE_REF)
        self.assertEqual(self.route(**push, paths=["scripts/r3_sqlite_append_controls.py"]),
                         ("ci", "runtime-sqlite-append-controls"))
        routed = self.route(**push, paths=None)
        self.assertIn("runtime-credit-diagnostics", routed)
        self.assertNotIn("runtime-stop-diagnostics", routed)
        self.assertNotIn("web-panel-ci", routed)

    def test_web_legacy_branch_and_manual_gating(self):
        self.assertEqual(self.route(event="push", ref="refs/heads/codex/logging-and-designer-improvements",
                                    paths=["web_panel/ui/app.js"]), ("web-panel-ci",))
        manual = dict(event="workflow_dispatch", ref="refs/heads/main")
        self.assertEqual(self.route(**manual, requested="runtime-stop-diagnostics"),
                         ("runtime-stop-diagnostics",))
        self.assertEqual(self.route(**manual, requested="runtime-credit-diagnostics"), ())
        with self.assertRaises(ValueError):
            self.route(**manual, requested="release-windows")

    def test_does_not_use_pr_merge_as_base_or_allow_suite_override(self):
        with self.assertRaises(ValueError):
            self.route(event="pull_request", ref="refs/pull/4/merge")
        with self.assertRaises(ValueError):
            self.route(event="push", ref="refs/heads/main", requested="runtime-stop-diagnostics")
