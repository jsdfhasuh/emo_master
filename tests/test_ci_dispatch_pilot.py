import json
import unittest
from urllib.parse import urlsplit

from scripts.ci_dispatch_pilot import BIND, JOB, PRODUCT, PRODUCT_ID, PROBE, PUBLIC, SEED, SEED_BRANCH, WORKFLOW_PATH, Pilot, PilotError


class FakeGitHub:
    def __init__(self, bad_bind=False, stale=False):
        self.bad_bind, self.stale = bad_bind, stale
        self.dispatched = {}
        self.statuses = []
        self.tag = "ci-dispatch-pilot-test"
        self.public_sha = "a" * 40
        self.branch = SEED_BRANCH
        self.blob = "c" * 40
        self.ref_reads = 0

    def __call__(self, req):
        url = urlsplit(req.full_url)
        assert url.netloc == "api.github.com"
        assert req.get_header("X-github-api-version") == "2026-03-10"
        path = url.path
        payload = json.loads(req.data) if req.data else None
        code = 200
        if path in {PRODUCT, PUBLIC}:
            body = {"id": PRODUCT_ID if path == PRODUCT else 20, "full_name": path.removeprefix("/repos/"), "default_branch": "main"}
        elif path == PRODUCT + "/git/ref/heads/" + self.branch:
            self.ref_reads += 1
            body = {"ref": "refs/heads/" + self.branch, "object": {
                "type": "commit", "sha": "b" * 40 if self.stale and self.ref_reads > 1 else SEED}}
        elif path == PUBLIC + "/git/ref/tags/" + self.tag:
            body = {"ref": "refs/tags/" + self.tag, "object": {"type": "commit", "sha": self.public_sha}}
        elif path == PUBLIC + "/contents/" + WORKFLOW_PATH:
            body = {"path": WORKFLOW_PATH, "type": "file", "sha": self.blob}
        elif path == PUBLIC + "/actions/workflows/" + WORKFLOW_PATH.split("/")[-1]:
            body = {"id": 30, "path": WORKFLOW_PATH, "state": "active"}
        elif path == PUBLIC + "/actions/workflows/30/dispatches":
            assert payload["ref"] == self.tag
            assert payload["inputs"]["product_sha"] == SEED
            run_id = 40 + len(self.dispatched)
            self.dispatched[run_id] = payload["inputs"]["probe"]
            body = {"workflow_run_id": run_id}
        elif path.startswith(PUBLIC + "/actions/runs/"):
            run_id = int(path.split("/")[6])
            outcome = "success" if self.dispatched[run_id] == "pass" else "failure"
            if path.endswith("/attempts/1/jobs"):
                body = {"total_count": 1, "jobs": [{"run_id": run_id, "name": JOB,
                         "status": "completed", "conclusion": outcome, "steps": [
                             {"name": BIND, "status": "completed", "conclusion": "skipped" if self.bad_bind else "success"},
                             {"name": PROBE, "status": "completed", "conclusion": outcome}]}]}
            else:
                body = {"id": run_id, "workflow_id": 30, "run_attempt": 1, "event": "workflow_dispatch",
                        "head_sha": self.public_sha, "repository": {"id": 20}, "head_repository": {"id": 20},
                        "status": "completed", "conclusion": outcome}
        elif path == f"{PRODUCT}/statuses/{SEED}":
            self.statuses.insert(0, payload)
            body, code = payload, 201
        elif path == f"{PRODUCT}/commits/{SEED}/statuses":
            body = self.statuses
        else:
            raise AssertionError(path)
        return code, json.dumps(body).encode()


class PilotControllerTests(unittest.TestCase):
    def execute(self, transport):
        rows = []
        Pilot("test_token", public_id=20, workflow_sha=transport.public_sha,
              tag=transport.tag, workflow_blob=transport.blob, seed_branch=transport.branch, transport=transport).execute(record=rows.append)
        return [json.loads(row) for row in rows]

    def test_records_distinct_runs_and_rereads_both_statuses(self):
        fake = FakeGitHub()
        rows = self.execute(fake)
        self.assertEqual([row["workflow_run_id"] for row in rows[:2]], [40, 41])
        self.assertEqual({r["context"]: r["state"] for r in rows[-1]["statuses"]},
                         {"dot/dispatch-pilot/pass": "success", "dot/dispatch-pilot/fail": "failure"})

    def test_stale_source_blocks_all_status_writes(self):
        fake = FakeGitHub(stale=True)
        with self.assertRaisesRegex(PilotError, "seed moved"):
            self.execute(fake)
        self.assertEqual(fake.statuses, [])

    def test_skipped_binding_blocks_all_status_writes(self):
        fake = FakeGitHub(bad_bind=True)
        with self.assertRaisesRegex(PilotError, "bound seed"):
            self.execute(fake)
        self.assertEqual(fake.statuses, [])
