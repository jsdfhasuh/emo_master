from dataclasses import replace
import unittest

from scripts.ci_shadow_verdict import decide, EvidenceProof, RequestIdentity


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.identity = RequestIdentity(10, 20, "a"*40, 30, 2, "b"*40, "c"*40, "85-1")
        self.current = dict(source_sha="b"*40, check_sha="c"*40, generation="85-1")
        self.run = dict(repository_id=10, workflow_id=20, head_sha="a"*40, id=30, run_attempt=2,
                        event="workflow_dispatch", status="completed", conclusion="success")
        self.jobs = [dict(name="Source / Linux", id=1, run_id=30, run_attempt=2,
                          status="completed", conclusion="success"),
                     dict(name="Source / Windows", id=2, run_id=30, run_attempt=2,
                          status="completed", conclusion="success")]
        self.proofs = [EvidenceProof(30, 2, n, 10+n, "sha256:"+"d"*64, 1000, "b"*40, "a"*40)
                       for n in (1, 2)]

    def check(self, **k):
        defaults = dict(current=self.current, run=self.run, jobs=self.jobs, artifacts=self.proofs,
                        required_jobs=("Source / Linux", "Source / Windows"), expected_event="workflow_dispatch",
                        expired=False, partition_verified=True)
        return decide(self.identity, **{**defaults, **k})

    def test_all_anchors_and_proofs(self):
        self.assertEqual(self.check(), "success")
        self.assertEqual(self.check(partition_verified=False), "error")
        self.assertEqual(self.check(artifacts=self.proofs[:1]), "error")
        self.assertEqual(self.check(jobs=self.jobs[:1]), "error")

    def test_stale_commit_base_or_generation_never_writes_success(self):
        for k, v in (("source_sha", "d"*40), ("check_sha", "d"*40), ("generation", "86-1")):
            self.assertEqual(self.check(current={**self.current, k:v}), "stale")

    def test_negative_and_missing_terminal_states(self):
        for conclusion in ("failure", "cancelled", "timed_out", "skipped"):
            self.assertEqual(self.check(run={**self.run, "conclusion":conclusion}), "failure")
        self.assertEqual(self.check(run=None), "pending")
        self.assertEqual(self.check(run=None, expired=True), "error")

    def test_wrong_run_attempt_proof_is_an_error(self):
        for k, v in (("run_attempt", 1), ("head_sha", "b"*40), ("event", "push"), ("id", 4)):
            self.assertEqual(self.check(run={**self.run, k:v}), "error")
        self.assertEqual(self.check(artifacts=[replace(self.proofs[0], attempt=1), self.proofs[1]]), "error")
        self.assertEqual(self.check(artifacts=[replace(self.proofs[0], artifact_id=12), self.proofs[1]]), "error")
