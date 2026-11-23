import unittest

from scripts.ci_request import PRODUCT_REPOSITORY_ID, validate_request


class RequestIdentityTests(unittest.TestCase):
    def setUp(self):
        self.dispatcher = dict(repository_id=PRODUCT_REPOSITORY_ID, workflow_id=77, run_id=8, attempt=2,
                               artifact_id=42, original_event="pull_request", original_ref="refs/pull/2/merge")
        self.head = dict(head_sha="a" * 40, base_sha="b" * 40, merge_sha="c" * 40, check_sha="c" * 40,
                         number=2, ref="refs/pull/2/merge")
        self.request = dict(schema_version=1, request_id="8-2", source_repository_id=PRODUCT_REPOSITORY_ID,
                            ci_workflow_sha="d" * 40, source_head_sha="a" * 40, source_base_sha="b" * 40,
                            source_merge_sha="c" * 40, check_sha="c" * 40, source_pr_number=2,
                            source_ref="refs/pull/2/merge", source_event_name="pull_request")

    def call(self, req=None, dispatch=None, head=None, artifact=42):
        validate_request(req or self.request, dispatcher=dispatch or self.dispatcher,
                         product_head=head or self.head, approved_public_sha="d" * 40,
                         expected_dispatcher_workflow_id=77, artifact_id=artifact)

    def test_verified_request(self):
        self.call()

    def test_foreign_or_stale_provenance(self):
        for overrides in ({"repository_id": 99}, {"workflow_id": 78}, {"run_id": 9}, {"attempt": 1},
                          {"artifact_id": 41}, {"original_event": "push"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.call(dispatch={**self.dispatcher, **overrides})
        with self.assertRaises(ValueError):
            self.call(artifact=43)

    def test_changed_commit_or_workflow_does_not_pass(self):
        for overrides in ({"head_sha": "e" * 40}, {"base_sha": "e" * 40}, {"merge_sha": "e" * 40},
                          {"check_sha": "e" * 40}, {"number": 3}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.call(head={**self.head, **overrides})
        for key in ("source_head_sha", "source_merge_sha", "ci_workflow_sha"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.call(req={**self.request, key: "abc"})

    def test_pr_head_cannot_be_used_as_gate_for_merge(self):
        with self.assertRaises(ValueError):
            self.call(req={**self.request, "check_sha": "a" * 40}, head={**self.head, "check_sha": "a" * 40})
