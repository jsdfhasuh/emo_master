import unittest

from scripts.ci_public_result import assess_public_result


class PublicResultTest(unittest.TestCase):
    def check(self, actual_updates=None, jobs=None):
        pinned = "a" * 40
        actual = {"id": 4, "repository_id": 5, "workflow_id": 6, "head_sha": pinned,
                  "run_attempt": 1, "event": "workflow_dispatch", "status": "completed",
                  "conclusion": "success"}
        actual.update(actual_updates or {})
        if jobs is None:
            jobs = [{"name": "required", "run_id": 4, "run_attempt": 1,
                     "status": "completed", "conclusion": "success"}]
        return assess_public_result(actual=actual, jobs=jobs, run_id=4, repository_id=5,
                                    workflow_id=6, workflow_sha=pinned, run_attempt=1,
                                    required_jobs=("required",))

    def test_trusted_run_and_required_job(self):
        self.assertEqual(self.check(), "success")
        self.assertEqual(self.check(jobs=[]), "failure")

    def test_wrong_provenance_cannot_be_pending_or_success(self):
        for key, value in (("id", 99), ("repository_id", 1), ("workflow_id", 1),
                           ("head_sha", "b" * 40), ("run_attempt", 2), ("event", "push")):
            with self.subTest(key=key):
                self.assertEqual(self.check({key: value}), "failure")

    def test_pending_not_success_and_cancelled_fails(self):
        self.assertEqual(self.check({"status": "queued", "conclusion": None}), "pending")
        self.assertEqual(self.check({"status": "queued", "conclusion": "success"}), "failure")
        self.assertEqual(self.check({"conclusion": "cancelled"}), "failure")
