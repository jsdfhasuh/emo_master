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
    (4, "MetadataFixtures", "test_signed_windows_status_is_rejected"),
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
    (30, "StrictStatusAbsenceTests", "test_exact_positive_corpus_all_encodings_and_allowed_exits"),
    (31, "StrictStatusAbsenceTests", "test_individual_ascii_outer_whitespace"),
    (32, "StrictStatusAbsenceTests", "test_complete_flag_required_even_for_exact_prefix"),
    (33, "StrictStatusAbsenceTests", "test_private_capture_required"),
    (34, "StrictStatusAbsenceTests", "test_normal_completion_required_for_timeout_failure_or_termination"),
    (35, "StrictStatusAbsenceTests", "test_flags_must_be_actual_booleans"),
    (36, "StrictStatusAbsenceTests", "test_exit_code_type_is_strict"),
    (37, "StrictStatusAbsenceTests", "test_exit_codes_do_not_alone_prove_absence"),
    (38, "StrictStatusAbsenceTests", "test_nonallowlisted_exit_codes"),
    (39, "StrictStatusAbsenceTests", "test_capture_at_exact_byte_bound_is_permitted"),
    (40, "StrictStatusAbsenceTests", "test_capture_over_byte_bound_is_rejected_before_decode"),
    (41, "StrictStatusAbsenceTests", "test_extra_or_changed_body_negative_corpus"),
    (42, "StrictStatusAbsenceTests", "test_unicode_whitespace_is_not_stripped"),
    (43, "StrictStatusAbsenceTests", "test_repeated_or_mixed_initial_boms_are_rejected"),
    (44, "StrictStatusAbsenceTests", "test_nonleading_or_internal_bom_is_not_removed"),
    (45, "StrictStatusAbsenceTests", "test_utf32_bom_prefix_collision_is_explicitly_rejected"),
    (46, "StrictStatusAbsenceTests", "test_unmarked_utf16_is_never_guessed"),
    (47, "StrictStatusAbsenceTests", "test_unmarked_utf32_is_not_accepted"),
    (48, "StrictStatusAbsenceTests", "test_invalid_encoding_never_uses_replacement_or_fallback"),
    (49, "StrictStatusAbsenceTests", "test_wrong_endian_bom_cannot_confirm_absence"),
    (50, "StrictStatusAbsenceTests", "test_bom_without_body_is_unknown"),
    (51, "StrictStatusAbsenceTests", "test_every_phrase_truncation_is_unknown"),
    (52, "StrictStatusAbsenceTests", "test_exhaustive_single_byte_insertions"),
    (53, "StrictStatusAbsenceTests", "test_exhaustive_single_byte_substitutions"),
    (54, "StrictStatusAbsenceTests", "test_input_shape_is_strict"),
    (55, "StrictStatusAbsenceTests", "test_reason_precedence_is_deterministic"),
    (56, "StrictStatusAbsenceTests", "test_diagnostics_are_closed_set_and_do_not_retain_raw_input"),
    (57, "StrictStatusAbsenceTests", "test_observation_and_decision_are_immutable"),
    (58, "StatusCaptureFixtures", "test_fake_complete_stdout_stderr_and_allowed_exits"),
    (59, "StatusCaptureFixtures", "test_fake_conflicting_stderr_cannot_be_filtered"),
    (60, "StatusCaptureFixtures", "test_fake_exit_before_eof_does_not_accept_prefix"),
    (61, "StatusCaptureFixtures", "test_fake_eof_without_child_completion_is_unknown"),
    (62, "StatusCaptureFixtures", "test_fake_read_error_after_exact_body_is_unknown"),
    (63, "StatusCaptureFixtures", "test_fake_poll_and_wait_errors_are_unknown"),
    (64, "StatusCaptureFixtures", "test_fake_close_error_invalidates_complete_body"),
    (65, "StatusCaptureFixtures", "test_fake_overflow_is_irreversible"),
    (66, "StatusCaptureFixtures", "test_fake_nonallowed_and_signed_exits_are_unknown"),
    (67, "StatusCaptureFixtures", "test_fake_spawn_error_is_private_and_unknown"),
    (68, "StatusCaptureFixtures", "test_real_stdout_and_stderr_are_both_captured"),
    (69, "StatusCaptureFixtures", "test_real_delayed_tail_is_not_ignored"),
    (70, "StatusCaptureFixtures", "test_real_bom_encodings_and_invalid_encoding"),
    (71, "StatusCaptureFixtures", "test_real_exact_cap_and_over_cap"),
    (72, "StatusCaptureFixtures", "test_real_timeout_kills_only_owned_child"),
    (73, "StatusCaptureFixtures", "test_real_inherited_writer_prevents_eof"),
    (74, "StatusCaptureFixtures", "test_real_windows_exit_dword_or_portable_nonzero"),
    (75, "StatusCaptureFixtures", "test_both_cli_import_entrypoints_fail_closed_off_windows"),
    (76, "StatusCaptureFixtures", "test_windows_readfile_eof_and_zero_write_contract"),
    (77, "StatusCaptureFixtures", "test_windows_open_empty_pipe_and_api_errors_are_not_eof"),
    (78, "StatusCaptureFixtures", "test_windows_inconsistent_read_count_is_rejected"),
    (79, "StatusCaptureFixtures", "test_final_read_crossing_deadline_cannot_accept"),
    (80, "StatusCaptureFixtures", "test_public_success_requires_full_case_inventory"),
)
CLASS_IDS = {"MetadataFixtures": 101, "SourceBoundaries": 102, "NativeCounterFixtures": 103,
             "StrictStatusAbsenceTests": 104, "StatusCaptureFixtures": 105}


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
        self.lifecycle = {phase + " (" + getattr(module, cls).__module__ + "." + cls + ")": ident
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
        successful = self.wasSuccessful() and not self.skipped
        if successful:
            expected = set(self.cases.values())
            complete = (self.testsRun == len(expected) and len(self.rows) == len(expected)
                        and {row["case_id"] for row in self.rows} == expected
                        and all(row["outcome"] == "PASS" and row["error"] == "NONE" for row in self.rows))
            invalid = invalid or not complete
        summary = {"phase": "summary", "reason": "report_invalid" if invalid else
                   "ok" if successful else "fixtures_failed",
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
