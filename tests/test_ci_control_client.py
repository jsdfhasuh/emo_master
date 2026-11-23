import json
import unittest

from scripts.ci_control_client import ControlClient, ControlError, FreshRequest, NoRedirect


PIN = "a" * 40


class Transport:
    def __init__(self):
        self.replies = []
        self.requests = []

    def append(self, data, status=200):
        self.replies.append((status, json.dumps(data).encode()))

    def __call__(self, request):
        self.requests.append(request)
        return self.replies.pop(0)


class ControlTest(unittest.TestCase):
    def setUp(self):
        self.http = Transport()
        self.client = ControlClient("fake_test_only", public_repository_id=11, worker_workflow_id=21,
                                    worker_sha=PIN, status_contexts=("CI source",), transport=self.http)

    def run_row(self, **extra):
        repo = {"id": 11, "full_name": "jsdfhasuh/emo_master_ci"}
        return {"id": 90, "repository": repo, "head_repository": repo, "head_sha": PIN,
                "run_attempt": 1, "workflow_id": 21, "event": "workflow_dispatch",
                "status": "completed", "conclusion": "success", **extra}

    def test_dispatch_requires_exact_result_and_pinned_tag(self):
        self.http.append({"object": {"type": "commit", "sha": PIN}})
        self.http.append({"workflow_run_id": 90})
        self.assertEqual(self.client.dispatch(dispatcher_run_id=1, artifact_id=2, worker_tag="ci-worker-v1"), 90)
        request = self.http.requests[-1]
        self.assertTrue(request.full_url.endswith("/actions/workflows/21/dispatches"))
        self.assertEqual(json.loads(request.data)["inputs"], {"dispatcher_run_id": "1", "request_artifact_id": "2"})
        self.assertEqual(request.get_header("X-github-api-version"), "2026-03-10")
        self.http.append({"object": {"type": "commit", "sha": "b" * 40}})
        with self.assertRaises(ControlError):
            self.client.dispatch(dispatcher_run_id=1, artifact_id=2, worker_tag="ci-worker-v1")
        self.assertEqual(len(self.http.requests), 3)

    def test_no_redirect_and_stale_pending_not_written(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://untrusted.example"))
        expected = FreshRequest("10-1", "1" * 40, "2" * 40, "3" * 40)
        with self.assertRaises(ControlError):
            self.client.report(expected=expected, read_current=lambda: FreshRequest("10-2", "1" * 40, "2" * 40, "3" * 40),
                               context="CI source", required_jobs=("source",))
        self.assertFalse(self.http.requests)

    def test_terminal_status_targets_only_fresh_exact_sha(self):
        expected = FreshRequest("10-1", "1" * 40, "2" * 40, "3" * 40)
        self.http.append(self.run_row())
        self.http.append({"total_count": 1, "jobs": [{"name": "source", "run_id": 90,
                         "status": "completed", "conclusion": "success"}]})
        self.http.append({}, status=201)
        self.assertEqual(self.client.report(expected=expected, read_current=lambda: expected,
                         context="CI source", required_jobs=("source",), run_id=90,
                         verify_evidence=lambda run, identity, ctx: (run, identity, ctx) == (90, expected, "CI source")),
                         "success")
        self.assertIn("/attempts/1/jobs", self.http.requests[1].full_url)
        self.assertTrue(self.http.requests[2].full_url.endswith("/statuses/" + expected.check_sha))
        self.assertEqual(json.loads(self.http.requests[2].data)["state"], "success")

    def test_run_cannot_pass_on_run_conclusion_alone(self):
        self.http.append(self.run_row())
        self.http.append({"total_count": 0, "jobs": []})
        self.assertEqual(self.client.assess(run_id=90, required_jobs=("source",)), "failure")


    def test_green_run_without_evidence_stays_failure(self):
        current = FreshRequest("10-1", "1" * 40, "2" * 40, "3" * 40)
        self.http.append(self.run_row())
        self.http.append({"total_count": 1, "jobs": [{"name": "source", "run_id": 90,
                         "status": "completed", "conclusion": "success"}]})
        self.http.append({}, status=201)
        actual = self.client.report(expected=current, read_current=lambda: current, context="CI source",
                                    required_jobs=("source",), run_id=90)
        self.assertEqual(actual, "failure")
        self.assertEqual(json.loads(self.http.requests[-1].data)["state"], "failure")
