import unittest

from scripts.ci_result_gate import verify_job_conclusions


def record(name, **overrides):
    return {"name": name, "run_id": 42, "run_attempt": 2,
            "status": "completed", "conclusion": "success", **overrides}


class VerifyJobConclusionsTest(unittest.TestCase):
    def check(self, jobs, expected=("Linux", "Windows")):
        verify_job_conclusions(expected_names=expected, run_id=42, run_attempt=2, jobs=jobs)

    def test_required_subset_can_pass(self):
        self.check([record("Windows"), record("Linux"), record("Optional", conclusion="failure")])

    def test_missing_duplicate_and_wrong_names_fail(self):
        for jobs in ([record("Linux")], [record("Linux"), record("Linux"), record("Windows")],
                     [record("linux"), record("Windows")]):
            with self.subTest(jobs=jobs), self.assertRaises(ValueError):
                self.check(jobs)

    def test_non_success_and_nonterminal_fail(self):
        for status, conclusion in (("completed", "skipped"), ("completed", "neutral"),
                                   ("completed", "failure"), ("completed", "cancelled"),
                                   ("in_progress", "success"), ("completed", None)):
            with self.subTest(status=status, conclusion=conclusion), self.assertRaises(ValueError):
                self.check([record("Linux", status=status, conclusion=conclusion), record("Windows")])

    def test_wrong_or_missing_run_identity_fails(self):
        for key, val in (("run_id", 41), ("run_attempt", 1), ("run_attempt", "2"),
                         ("run_id", None), ("run_attempt", True)):
            with self.subTest(key=key, value=val), self.assertRaises(ValueError):
                self.check([record("Linux", **{key: val}), record("Windows")])
        job = record("Linux")
        del job["run_attempt"]
        with self.assertRaises(ValueError):
            self.check([job, record("Windows")])

    def test_bad_required_names_fail(self):
        for names in ((), ("Linux", "Linux"), "Linux", ("",)):
            with self.subTest(names=names), self.assertRaises(ValueError):
                self.check([], names)
