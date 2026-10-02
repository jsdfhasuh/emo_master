"""Fixed-ID offline fixture report. Raw test output remains runner-private."""
from contextlib import redirect_stderr, redirect_stdout
import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

MODULE = "scripts.diagnostics.windows_wpr_metadata_preflight_fixtures"
# Stable IDs are explicit, never assigned by discovery order or exception text.
CASES = (
    (1, "MetadataFixtures", "test_exact_readonly_commands_and_fresh_instance"),
    (2, "MetadataFixtures", "test_failed_profile_does_not_hide_other_readonly_phases"),
    (3, "MetadataFixtures", "test_unexpected_status_zero_never_asserts_absence_or_presence"),
    (4, "MetadataFixtures", "test_signed_windows_status_is_normalized"),
    (5, "MetadataFixtures", "test_timeout_and_exception_text_stay_private"),
    (6, "MetadataFixtures", "test_oversized_private_output_is_never_read_or_published"),
    (7, "MetadataFixtures", "test_profile_identity_failure_runs_nothing"),
    (8, "MetadataFixtures", "test_missing_wpr_still_queries_native_counter_once"),
    (9, "MetadataFixtures", "test_unrelated_api_error_discards_misleading_count"),
    (10, "MetadataFixtures", "test_more_data_preserves_count_without_retry"),
    (11, "MetadataFixtures", "test_exact_capacity_is_not_overflow"),
    (12, "MetadataFixtures", "test_inconsistent_success_is_rejected"),
    (13, "MetadataFixtures", "test_inconsistent_more_data_is_rejected"),
    (14, "MetadataFixtures", "test_native_exit_failure_cannot_publish_counts"),
    (15, "MetadataFixtures", "test_untrusted_native_output_is_rejected"),
    (16, "MetadataFixtures", "test_emitter_rebuilds_fixed_schema_and_rejects_bad_values"),
    (17, "MetadataFixtures", "test_main_only_removes_owned_child_and_keeps_parent"),
    (18, "MetadataFixtures", "test_cleanup_failure_is_safe_and_inconclusive"),
    (19, "MetadataFixtures", "test_preexisting_parent_is_not_removed_after_creation_failure"),
    (20, "MetadataFixtures", "test_wrong_environment_runs_nothing"),
    (21, "SourceBoundaries", "test_profile_is_unchanged"),
    (22, "SourceBoundaries", "test_no_capture_or_controller_routes"),
    (23, "SourceBoundaries", "test_extracted_workflow_public_output_filter"),
    (24, "NativeCounterFixtures", "test_native_status_count_semantics_and_no_detail_output"),
    (25, "SourceBoundaries", "test_windows_checkout_preserves_profile_bytes"),
    (26, "SourceBoundaries", "test_safe_fixture_report_redacts_all_failure_routes"),
    (27, "SourceBoundaries", "test_safe_fixture_report_rejects_unmapped_case"),
    (28, "SourceBoundaries", "test_extracted_workflow_fixture_output_filter"),
    (29, "SourceBoundaries", "test_fixture_inventory_rejects_missing_duplicate_and_extra_cases"),
)
CLASS_IDS = {"MetadataFixtures": 101, "SourceBoundaries": 102, "NativeCounterFixtures": 103}


def error_category(error):
    kind = error[0]
    for expected, category in ((AssertionError, "ASSERTION"), (subprocess.TimeoutExpired, "TIMEOUT"),
                               (OSError, "OS_ERROR"), (ImportError, "IMPORT"),
                               (ValueError, "VALUE"), (TypeError, "TYPE")):
        if issubclass(kind, expected):
            return category
    return "UNKNOWN"


class FixedResult(unittest.TestResult):
    def __init__(self, module):
        super().__init__()
        self.rows = []
        self.invalid = 0
        self.cases = {(getattr(module, cls), method): ident for ident, cls, method in CASES}
        self.lifecycle = {phase + " (" + MODULE + "." + cls + ")": ident
                          for cls, ident in CLASS_IDS.items()
                          for phase in ("setUpClass", "tearDownClass")}

    def record(self, test, outcome, error):
        if isinstance(test, unittest.TestCase):
            ident = self.cases.get((type(test), test._testMethodName))
        else:
            # unittest's class setup/teardown holder has no TestCase. Only an
            # exact predeclared description can map to a numeric lifecycle ID.
            ident = self.lifecycle.get(getattr(test, "description", None))
        if ident is None:
            ident, outcome, error = 0, "INVALID", "UNKNOWN"
            self.invalid += 1
        self.rows.append({"case_id": ident, "outcome": outcome, "error": error})

    def addSuccess(self, test):
        self.record(test, "PASS", "NONE")

    def addFailure(self, test, err):
        self.failures.append((test, None))
        self.record(test, "FAIL", error_category(err))

    def addError(self, test, err):
        self.errors.append((test, None))
        self.record(test, "ERROR", error_category(err))

    def addSkip(self, test, _reason):
        self.skipped.append((test, None))
        self.record(test, "SKIP", "NONE")

    def addSubTest(self, test, _subtest, err):
        if err is not None:
            if issubclass(err[0], test.failureException):
                self.addFailure(test, err)
            else:
                self.addError(test, err)

    def addExpectedFailure(self, test, err):
        self.addError(test, err)

    def addUnexpectedSuccess(self, test):
        self.errors.append((test, None))
        self.record(test, "ERROR", "UNKNOWN")

    def records(self):
        invalid = self.invalid or len(self.rows) > 128
        summary = {"phase": "summary", "reason": "report_invalid" if invalid else
                   "ok" if self.wasSuccessful() else "fixtures_failed",
                   "tests_run": self.testsRun, "failures": len(self.failures),
                   "errors": len(self.errors), "skipped": len(self.skipped),
                   "invalid": int(invalid)}
        rows = self.rows if len(self.rows) <= 128 else [{"case_id": 0, "outcome": "INVALID", "error": "UNKNOWN"}]
        return rows, summary


def validate_suite(suite, result):
    pending = list(suite)
    found = []
    while pending:
        test = pending.pop()
        if isinstance(test, unittest.TestSuite):
            pending.extend(test)
        elif isinstance(test, unittest.TestCase):
            found.append((type(test), test._testMethodName))
        else:
            raise ValueError("fixture_inventory")
    if len(found) != len(result.cases) or set(found) != set(result.cases):
        raise ValueError("fixture_inventory")


def main(args):
    rows = []
    summary = {"phase": "summary", "reason": "report_invalid", "tests_run": 0,
               "failures": 0, "errors": 1, "skipped": 0, "invalid": 1}
    try:
        if len(args) != 1:
            raise ValueError("arguments")
        with (Path(args[0]) / "fixture-raw.private").open("x", encoding="utf-8") as stream:
            with redirect_stdout(stream), redirect_stderr(stream):
                module = importlib.import_module(MODULE)
                result = FixedResult(module)
                suite = unittest.defaultTestLoader.loadTestsFromModule(module)
                validate_suite(suite, result)
                suite.run(result)
                rows, summary = result.records()
    except BaseException as error:
        rows = [{"case_id": 900, "outcome": "INVALID", "error": error_category((type(error), None, None))}]
        summary = {"phase": "summary", "reason": "report_invalid", "tests_run": 0,
                   "failures": 0, "errors": 1, "skipped": 0, "invalid": 1}
    for record in rows:
        print("WPR_FIXTURE " + json.dumps(record, separators=(",", ":")))
    print("WPR_FIXTURE_SUMMARY " + json.dumps(summary, separators=(",", ":")))
    return 0 if summary["reason"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
