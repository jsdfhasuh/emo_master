"""Offline stdlib tests only; no real WPR, trace API, or runtime workload."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import r3_windows_wpr_metadata_preflight as probe
from scripts.diagnostics import windows_wpr_status_capture as status_capture
from scripts.diagnostics.windows_wpr_status_absence_fixtures import StrictStatusAbsenceTests  # noqa: F401
from scripts.diagnostics.windows_wpr_status_capture_fixtures import StatusCaptureFixtures  # noqa: F401

REPO = Path(__file__).resolve().parents[2]
SECRET = "PRIVATE_SESSION_C:\\private\\trace.etl"


class MetadataFixtures(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.wpr = self.root / "wpr.exe"
        self.helper = self.root / "session-count.exe"
        self.wpr.touch()
        self.helper.touch()
        self.profile = REPO / "scripts/windows_commit_trace.wprp"
        self.commands = []
        self.status_body = b"WPR is not recording"
        self.status_exit = 0
        self.status_error = None

    def fake_run(self, argv, **kwargs):
        self.commands.append(argv)
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["timeout"], 15)
        self.assertIs(kwargs["stdout"], kwargs["stderr"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        if argv == [str(self.helper)]:
            kwargs["stdout"].write(b'{"api_status":0,"returned_count":5}\n')
            return SimpleNamespace(returncode=0)
        kwargs["stdout"].write(SECRET.encode())
        return SimpleNamespace(returncode=0)

    def fake_status(self, argv, **kwargs):
        self.commands.append(argv)
        self.assertEqual(kwargs, dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, shell=False, bufsize=0))
        if self.status_error:
            raise self.status_error
        stream = io.BytesIO(self.status_body)
        return SimpleNamespace(stdout=stream, poll=lambda: self.status_exit,
                               wait=lambda timeout: self.status_exit)

    def observe(self, run=None):
        capture = io.StringIO()
        with patch.object(probe.subprocess, "run", side_effect=run or self.fake_run), \
                patch.object(status_capture.subprocess, "Popen", side_effect=self.fake_status), \
                patch.object(status_capture, "_pipe_reader", side_effect=lambda stream: stream.read), redirect_stdout(capture):
            code = probe.inspect(self.root, self.helper, self.profile, self.wpr)
        output = capture.getvalue()
        self.assertNotIn(SECRET, output)
        self.assertNotIn(str(self.root), output)
        return code, [json.loads(line.removeprefix("WPR_METADATA ")) for line in output.splitlines()]

    def count_result(self, data, exit_code=0):
        def run(argv, **kwargs):
            self.assertEqual(argv, [str(self.helper)])
            self.commands.append(argv)
            kwargs["stdout"].write(data if isinstance(data, bytes) else json.dumps(data).encode())
            return SimpleNamespace(returncode=exit_code)
        with patch.object(probe.subprocess, "run", side_effect=run):
            return probe.session_count(self.helper, self.root)

    def test_exact_readonly_commands_and_fresh_instance(self):
        code, rows = self.observe()
        self.assertEqual(code, 0)
        self.assertEqual(self.commands[:2], [
            [str(self.wpr), "-profiles", str(self.profile)],
            [str(self.wpr), "-profiledetails", str(self.profile) + "!CommitTrace.Light"],
        ])
        self.assertEqual(self.commands[2][:3], [str(self.wpr), "-status", "-instancename"])
        self.assertRegex(self.commands[2][3], r"^R3ReadOnly_[0-9a-f]{32}$")
        self.assertEqual(self.commands[3], [str(self.helper)])
        self.assertEqual(len(self.commands), 4)
        self.assertEqual([r["reason"] for r in rows], ["ok", "ok", "instance_absent", "ok"])
        self.assertEqual(rows[-1]["count_validity"], "API_REPORTED")
        self.assertEqual(rows[-1]["returned_count"], 5)
        first = self.commands[2][3]
        # A different private root allows a second independent offline invocation.
        for path in self.root.glob("*.private"):
            path.unlink()
        self.observe()
        self.assertNotEqual(first, self.commands[6][3])

    def test_failed_profile_does_not_hide_other_readonly_phases(self):
        def run(argv, **kwargs):
            result = self.fake_run(argv, **kwargs)
            return SimpleNamespace(returncode=17) if "-profiles" in argv else result
        code, rows = self.observe(run)
        self.assertEqual(code, 2)
        self.assertEqual(rows[0]["reason"], "command_failed")
        self.assertEqual(rows[0]["native_exit"], 17)
        self.assertEqual(len(self.commands), 4)

    def test_unexpected_status_zero_never_asserts_absence_or_presence(self):
        self.status_body = SECRET.encode()
        code, rows = self.observe()
        self.assertEqual(code, 2)
        self.assertEqual(rows[2]["reason"], "status_body_not_exact")
        self.assertEqual(rows[2]["count_validity"], "UNKNOWN")

    def test_signed_windows_status_is_rejected(self):
        self.status_exit = 0xC5583000 - (1 << 32)
        code, rows = self.observe()
        self.assertEqual(code, 2)
        self.assertEqual(rows[2]["reason"], "status_exit_not_allowed")
        self.assertIsNone(rows[2]["native_exit"])

    def test_timeout_and_exception_text_stay_private(self):
        self.status_error = OSError(SECRET)
        def run(argv, **kwargs):
            self.commands.append(argv)
            kwargs["stdout"].write(SECRET.encode())
            if "-profiles" in argv:
                raise subprocess.TimeoutExpired(SECRET, 15, output=SECRET, stderr=SECRET)
            raise OSError(SECRET)
        code, rows = self.observe(run)
        self.assertEqual(code, 2)
        self.assertEqual(rows[0]["reason"], "command_timeout")
        self.assertEqual(len(self.commands), 4)
        self.assertTrue(all(r["native_exit"] is None for r in rows))

    def test_oversized_private_output_is_never_read_or_published(self):
        def run(argv, **kwargs):
            kwargs["stdout"].write(b"x" * (probe.MAX_OUTPUT + 1))
            return SimpleNamespace(returncode=0)
        with patch.object(probe.subprocess, "run", side_effect=run):
            result, output = probe.command([str(self.helper)], self.root, "session_count")
        self.assertEqual(result["reason"], "output_limit")
        self.assertIsNone(output)

    def test_profile_identity_failure_runs_nothing(self):
        self.profile = self.root / "changed.wprp"
        self.profile.write_text("changed")
        code, rows = self.observe()
        self.assertEqual(code, 2)
        self.assertEqual(rows[0]["reason"], "profile_changed")
        self.assertEqual(self.commands, [])

    def test_missing_wpr_still_queries_native_counter_once(self):
        self.wpr.unlink()
        code, rows = self.observe()
        self.assertEqual(code, 2)
        self.assertEqual([r["reason"] for r in rows[:3]], ["capability_missing"] * 3)
        self.assertEqual(self.commands, [[str(self.helper)]])

    def test_unrelated_api_error_discards_misleading_count(self):
        result = self.count_result({"api_status": 5, "returned_count": 999})
        self.assertEqual(result["reason"], "api_error")
        self.assertEqual(result["api_status"], 5)
        self.assertEqual(result["count_validity"], "UNKNOWN")
        for key in ("returned_count", "capacity_reached", "capacity_exceeded"):
            self.assertIsNone(result[key])
        self.assertEqual(len(self.commands), 1)

    def test_more_data_preserves_count_without_retry(self):
        result = self.count_result({"api_status": 234, "returned_count": 80})
        self.assertEqual(result["reason"], "more_data")
        self.assertEqual(result["returned_count"], 80)
        self.assertEqual(result["count_validity"], "API_REPORTED")
        self.assertEqual((result["capacity"], result["capacity_reached"], result["capacity_exceeded"]), (64, 1, 1))
        self.assertEqual(len(self.commands), 1)

    def test_exact_capacity_is_not_overflow(self):
        result = self.count_result({"api_status": 0, "returned_count": 64})
        self.assertEqual(result["reason"], "capacity_reached")
        self.assertEqual(result["capacity_reached"], 1)
        self.assertEqual(result["capacity_exceeded"], 0)

    def test_inconsistent_success_is_rejected(self):
        result = self.count_result({"api_status": 0, "returned_count": 65})
        self.assertEqual(result["reason"], "count_inconsistent")
        self.assertIsNone(result["returned_count"])
        self.assertEqual(result["count_validity"], "UNKNOWN")
        self.assertIsNone(result["capacity_reached"])
        self.assertIsNone(result["capacity_exceeded"])

    def test_inconsistent_more_data_is_rejected(self):
        result = self.count_result({"api_status": 234, "returned_count": 0})
        self.assertEqual(result["reason"], "count_inconsistent")
        self.assertEqual(result["returned_count"], 0)
        self.assertEqual(result["count_validity"], "UNKNOWN")
        self.assertIsNone(result["capacity_reached"])
        self.assertIsNone(result["capacity_exceeded"])

    def test_native_exit_failure_cannot_publish_counts(self):
        result = self.count_result({"api_status": 0, "returned_count": 5}, 2)
        self.assertEqual(result["reason"], "command_failed")
        self.assertIsNone(result["returned_count"])

    def test_untrusted_native_output_is_rejected(self):
        samples = [
            SECRET.encode(), b"{}", b"[]", b"null", b"x" * 257,
            b'{"api_status":0,"api_status":5,"returned_count":9}',
            {"api_status": 0, "returned_count": 9, "name": SECRET},
            {"api_status": True, "returned_count": 9},
            {"api_status": -1, "returned_count": 9},
            {"api_status": 0, "returned_count": SECRET},
            {"api_status": 0, "returned_count": True},
            {"api_status": 0, "returned_count": -1},
            {"api_status": 0, "returned_count": 1 << 32},
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                (self.root / "session_count.private").unlink(missing_ok=True)
                result = self.count_result(sample)
                self.assertEqual(result["reason"], "native_output_invalid")
                self.assertIsNone(result["returned_count"])
                self.assertNotIn(SECRET, json.dumps(result))

    def test_emitter_rebuilds_fixed_schema_and_rejects_bad_values(self):
        output = io.StringIO()
        data = probe.row("profiles", "ok", 0)
        data["private"] = SECRET
        with redirect_stdout(output):
            probe.emit(data)
        self.assertNotIn(SECRET, output.getvalue())
        for phase, reason, number in ((SECRET, "ok", 0), ("profiles", SECRET, 0),
                                      ("profiles", "ok", True), ("profiles", "ok", -1)):
            with self.assertRaises(ValueError):
                probe.row(phase, reason, number)

    def supported_environment(self):
        return SimpleNamespace(name="nt", environ={"GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows",
                                                   "GITHUB_RUN_ATTEMPT": "1", "SystemRoot": "unused"})

    def test_main_only_removes_owned_child_and_keeps_parent(self):
        marker = self.root / "preexisting.private"
        marker.write_text(SECRET)
        owned = []
        def inspect(root, helper, profile, wpr):
            owned.append(root)
            self.assertEqual(root.parent, self.root)
            (root / "raw.private").write_text(SECRET)
            return 0
        output = io.StringIO()
        with patch.object(probe, "os", self.supported_environment()), patch.object(probe, "inspect", side_effect=inspect), redirect_stdout(output):
            self.assertEqual(probe.main([str(self.root)]), 0)
        self.assertEqual(marker.read_text(), SECRET)
        self.assertEqual(len(owned), 1)
        self.assertFalse(owned[0].exists())
        self.assertNotIn(SECRET, output.getvalue())

    def test_cleanup_failure_is_safe_and_inconclusive(self):
        output = io.StringIO()
        with patch.object(probe, "os", self.supported_environment()), patch.object(probe, "inspect", return_value=0), \
                patch.object(probe.shutil, "rmtree", side_effect=OSError(SECRET)), redirect_stdout(output):
            self.assertEqual(probe.main([str(self.root)]), 2)
        self.assertIn('"reason":"cleanup_failed"', output.getvalue())
        self.assertNotIn(SECRET, output.getvalue())

    def test_preexisting_parent_is_not_removed_after_creation_failure(self):
        output = io.StringIO()
        with patch.object(probe, "os", self.supported_environment()), \
                patch.object(probe.tempfile, "mkdtemp", side_effect=OSError(SECRET)), \
                patch.object(probe.shutil, "rmtree") as remove, redirect_stdout(output):
            self.assertEqual(probe.main([str(self.root)]), 2)
        remove.assert_not_called()
        self.assertNotIn(SECRET, output.getvalue())

    def test_wrong_environment_runs_nothing(self):
        output = io.StringIO()
        with patch.object(probe, "os", SimpleNamespace(name="posix", environ={})), \
                patch.object(probe.tempfile, "mkdtemp") as create, \
                patch.object(probe.subprocess, "run") as run, redirect_stdout(output):
            self.assertEqual(probe.main([str(self.root)]), 2)
        create.assert_not_called()
        run.assert_not_called()
        self.assertIn('"reason":"wrong_environment"', output.getvalue())


class SourceBoundaries(unittest.TestCase):
    def test_profile_is_unchanged(self):
        digest = hashlib.sha256((REPO / "scripts/windows_commit_trace.wprp").read_bytes()).hexdigest()
        self.assertEqual(digest, probe.PROFILE_SHA256)

    def test_no_capture_or_controller_routes(self):
        tool = (REPO / "scripts/r3_windows_wpr_metadata_preflight.py").read_text()
        native = (REPO / "scripts/windows_wpr_session_count.cpp").read_text()
        workflow = (REPO / ".github/workflows/runtime-wpr-metadata-preflight.yml").read_text()
        adapter = (REPO / "scripts/diagnostics/windows_wpr_status_capture.py").read_text()
        parser = (REPO / "scripts/diagnostics/windows_wpr_status_absence.py").read_text()
        for source in (tool, adapter, parser, native, workflow):
            for forbidden in ("-start", "-stop", "-cancel", "--control", "--session-check", "sessionCheck",
                              "StartTrace", "ControlTrace", "OpenTrace", "ProcessTrace", "EnableTrace",
                              "AdjustTokenPrivileges", "r3_windows_commit_trace", "windows_commit_trace_reader"):
                self.assertNotIn(forbidden, source)
        self.assertEqual(native.count("QueryAllTracesW("), 1)
        self.assertIn("constexpr ULONG CAPACITY = 64;", native)
        self.assertNotIn("resize", native)
        self.assertNotIn("wcs", native)
        for forbidden in ("pip install", "setup-python", "upload-artifact", "pytest", "workflow_dispatch"):
            self.assertNotIn(forbidden, workflow)
        self.assertIn("github.run_attempt == 1", workflow)
        self.assertIn("425f04d63c453d96f217b43308d7903569761515", workflow)
        self.assertIn("diagnostic: classify scoped WPR status; read-only metadata 9eb237ac", workflow)
        self.assertIn("if ($owned)", workflow)
        self.assertEqual(workflow.count("Remove-Item"), 1)

    def test_extracted_workflow_public_output_filter(self):
        workflow = (REPO / ".github/workflows/runtime-wpr-metadata-preflight.yml").read_text()
        values = {}
        for name in ("number", "phases", "reasons"):
            values[name] = re.search(r"\$" + name + r" = '([^']+)'", workflow).group(1)
        expression = re.search(r"\$pattern = (.+)", workflow).group(1)
        # This executes the workflow's exact literal regex concatenation, without
        # requiring PowerShell or running any workflow command on this machine.
        parts = re.findall(r"'([^']*)'|\$(\w+)", expression)
        pattern = "".join(literal if not variable else values[variable] for literal, variable in parts)
        for phase in probe.PHASES:
            for reason in probe.REASONS:
                output = io.StringIO()
                with redirect_stdout(output):
                    probe.emit(probe.row(phase, reason, 0xFFFFFFFF, 234, 80))
                self.assertIsNotNone(re.fullmatch(pattern, output.getvalue().rstrip("\n")))
        for invalid in (SECRET, 'WPR_METADATA {"phase":"' + SECRET + '"}',
                        "Traceback (most recent call last):", "::warning::" + SECRET):
            self.assertIsNone(re.fullmatch(pattern, invalid))

    def test_windows_checkout_preserves_profile_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "scripts/windows_commit_trace.wprp"
            profile.parent.mkdir()
            original = (REPO / "scripts/windows_commit_trace.wprp").read_bytes()
            profile.write_bytes(original)
            attributes = root / ".gitattributes"
            configured = (REPO / ".gitattributes").read_bytes()
            self.assertIn(b"scripts/windows_commit_trace.wprp text eol=lf", configured)
            empty_config = root / "empty-git-config"
            empty_config.write_bytes(b"")
            git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
            git_env.update(GIT_ATTR_NOSYSTEM="1", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(empty_config))
            def git(*args):
                return subprocess.run(["git", "-C", str(root), "-c", "core.attributesFile=" + str(empty_config), *args],
                                      env=git_env, capture_output=True, check=True, timeout=15)
            git("init")
            git("-c", "core.autocrlf=false", "add", "scripts/windows_commit_trace.wprp")
            # Red control: without a path rule, Windows checkout changes bytes.
            red = root / "red"
            red.mkdir()
            git("-c", "core.autocrlf=true", "checkout-index", "--prefix=" + red.as_posix() + "/", "--", "scripts/windows_commit_trace.wprp")
            changed = (red / "scripts/windows_commit_trace.wprp").read_bytes()
            self.assertNotEqual(changed, original)
            self.assertIn(b"\r\n", changed)
            # Green: the narrow committed rule preserves the actual raw bytes.
            attributes.write_bytes(configured)
            green = root / "green"
            green.mkdir()
            git("-c", "core.autocrlf=true", "checkout-index", "--prefix=" + green.as_posix() + "/", "--", "scripts/windows_commit_trace.wprp")
            self.assertEqual((green / "scripts/windows_commit_trace.wprp").read_bytes(), original)
            self.assertEqual(hashlib.sha256(original).hexdigest(), probe.PROFILE_SHA256)

    def test_safe_fixture_report_redacts_all_failure_routes(self):
        import sys
        from scripts.diagnostics import windows_wpr_fixture_report as report
        result = report.FixedResult(sys.modules[__name__])
        case = MetadataFixtures("test_exact_readonly_commands_and_fresh_instance")
        for kind in (AssertionError, OSError, subprocess.TimeoutExpired, ImportError, ValueError, TypeError, RuntimeError):
            result.addError(case, (kind, SECRET, None))
        result.addFailure(case, (AssertionError, SECRET, None))
        result.addSubTest(case, SECRET, (AssertionError, SECRET, None))
        result.addSubTest(case, SECRET, (OSError, SECRET, None))
        result.addSkip(case, SECRET)
        for phase in ("setUpClass", "tearDownClass"):
            holder = SimpleNamespace(description=phase + " (" + report.MODULE + ".NativeCounterFixtures)")
            result.addError(holder, (OSError, SECRET, None))
        rows, summary = result.records()
        self.assertEqual({row["case_id"] for row in rows}, {1, 103})
        self.assertEqual(summary["failures"], 2)
        self.assertEqual(summary["errors"], 10)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["reason"], "fixtures_failed")
        self.assertNotIn(SECRET, json.dumps([rows, summary]))
        self.assertEqual({row["error"] for row in rows}, {
            "ASSERTION", "OS_ERROR", "TIMEOUT", "IMPORT", "VALUE", "TYPE", "UNKNOWN", "NONE",
        })

    def test_safe_fixture_report_rejects_unmapped_case(self):
        import sys
        from scripts.diagnostics import windows_wpr_fixture_report as report
        result = report.FixedResult(sys.modules[__name__])
        result.addSuccess(unittest.FunctionTestCase(lambda: None))
        result.addError(SimpleNamespace(description=SECRET), (RuntimeError, SECRET, None))
        rows, summary = result.records()
        self.assertEqual(rows, [{"case_id": 0, "outcome": "INVALID", "error": "UNKNOWN"}] * 2)
        self.assertEqual(summary["reason"], "report_invalid")
        self.assertEqual(summary["invalid"], 2)
        self.assertNotIn(SECRET, json.dumps([rows, summary]))

    def test_extracted_workflow_fixture_output_filter(self):
        import sys
        from scripts.diagnostics import windows_wpr_fixture_report as report
        workflow = (REPO / ".github/workflows/runtime-wpr-metadata-preflight.yml").read_text()
        pattern = re.search(r"\$fixturePattern = '([^']+)'", workflow).group(1)
        summary_pattern = re.search(r"\$fixtureSummaryPattern = '([^']+)'", workflow).group(1)
        result = report.FixedResult(sys.modules[__name__])
        result.addFailure(MetadataFixtures("test_exact_readonly_commands_and_fresh_instance"), (AssertionError, SECRET, None))
        result.addError(SimpleNamespace(description=SECRET), (RuntimeError, SECRET, None))
        rows, summary = result.records()
        for row in rows:
            self.assertIsNotNone(re.fullmatch(pattern, "WPR_FIXTURE " + json.dumps(row, separators=(",", ":"))))
        self.assertIsNotNone(re.fullmatch(summary_pattern, "WPR_FIXTURE_SUMMARY " + json.dumps(summary, separators=(",", ":"))))
        for invalid in (SECRET, 'WPR_FIXTURE {"case_id":1,"outcome":"' + SECRET + '"}', "Traceback: " + SECRET,
                        'WPR_FIXTURE {"case_id":999,"outcome":"PASS","error":"NONE"}'):
            self.assertIsNone(re.fullmatch(pattern, invalid))
            self.assertIsNone(re.fullmatch(summary_pattern, invalid))

    def test_fixture_inventory_rejects_missing_duplicate_and_extra_cases(self):
        import sys
        from scripts.diagnostics import windows_wpr_fixture_report as report
        module = sys.modules[__name__]
        result = report.FixedResult(module)
        def declared():
            return [getattr(module, cls)(method) for _, cls, method in report.CASES]
        report.validate_suite(unittest.TestSuite(declared()), result)
        for cases in (declared()[:-1], declared() + declared()[:1], declared() + [unittest.FunctionTestCase(lambda: None)]):
            with self.assertRaises(ValueError):
                report.validate_suite(unittest.TestSuite(cases), result)


class NativeCounterFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++")
        if not compiler:
            raise unittest.SkipTest("No existing portable C++ compiler; native stub checks unavailable")
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.executable = cls.root / ("counter.exe" if os.name == "nt" else "counter")
        (cls.root / "windows.h").write_text("""
#pragma once
#include <cstdint>
using ULONG = std::uint32_t;
constexpr ULONG ERROR_SUCCESS = 0, ERROR_MORE_DATA = 234;
""")
        (cls.root / "evntrace.h").write_text("""
#pragma once
#include <windows.h>
struct EVENT_TRACE_PROPERTIES {
    struct { ULONG BufferSize; ULONG untouched; } Wnode;
    ULONG LoggerNameOffset, LogFileNameOffset, untouched;
};
extern ULONG QueryAllTracesW(EVENT_TRACE_PROPERTIES**, ULONG, ULONG*);
""")
        (cls.root / "fake.cpp").write_text("""
#include <evntrace.h>
#include <cstdlib>
#include <cstring>
ULONG QueryAllTracesW(EVENT_TRACE_PROPERTIES** properties, ULONG capacity, ULONG* count) {
    static unsigned calls = 0;
    if (++calls != 1 || capacity != 64 || *count != 0) std::exit(91);
    for (ULONG i = 0; i < capacity; ++i) {
        auto* p = properties[i];
        if (!p || p->Wnode.untouched || p->untouched ||
            p->LoggerNameOffset < sizeof(EVENT_TRACE_PROPERTIES) ||
            p->LogFileNameOffset <= p->LoggerNameOffset ||
            p->LogFileNameOffset >= p->Wnode.BufferSize) std::exit(92);
        std::memcpy(reinterpret_cast<char*>(p) + p->LoggerNameOffset, "PRIVATE_SESSION", 16);
        std::memcpy(reinterpret_cast<char*>(p) + p->LogFileNameOffset, "PRIVATE_PATH", 13);
    }
    *count = static_cast<ULONG>(std::strtoul(std::getenv("FAKE_COUNT"), nullptr, 10));
    return static_cast<ULONG>(std::strtoul(std::getenv("FAKE_STATUS"), nullptr, 10));
}
""")
        built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(cls.root),
                                str(REPO / "scripts/windows_wpr_session_count.cpp"), str(cls.root / "fake.cpp"),
                                "-o", str(cls.executable)], capture_output=True, timeout=30, check=False)
        if built.returncode:
            cls.temporary.cleanup()
            raise AssertionError("Native offline stub compilation failed")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_native_status_count_semantics_and_no_detail_output(self):
        for status, count in ((0, 0), (0, 5), (0, 64), (0, 65), (234, 80), (234, 0), (5, 0), (5, 999), (87, 64)):
            with self.subTest(status=status, count=count):
                result = subprocess.run([str(self.executable)], capture_output=True, timeout=5, check=False,
                                        env=dict(os.environ, FAKE_STATUS=str(status), FAKE_COUNT=str(count)))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, b"")
                self.assertEqual(json.loads(result.stdout), {
                    "api_status": status, "returned_count": count if status in (0, 234) else None,
                })
                self.assertNotIn(b"PRIVATE", result.stdout)


if __name__ == "__main__":
    unittest.main()
