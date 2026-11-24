import copy
import unittest

from scripts.ci_mainline_envelope import build, PATH, MAIN, REPO_ID

BASE, HEAD, MERGE, BLOB = tuple(c * 40 for c in "abcd")
REPO = {"id": REPO_ID}


def fixture(event):
    pr = dict(number=8, state="open", mergeable=True, merge_commit_sha=MERGE,
              head=dict(repo=REPO, sha=HEAD), base=dict(repo=REPO, sha=BASE, ref="main"))
    run = dict(id=40, run_attempt=2, workflow_id=9, path=PATH, event=event,
               repository=REPO, head_repository=REPO, head_branch="main", head_sha=HEAD)
    payload = dict(repository=REPO)
    ref, event_sha, main = MAIN, HEAD, HEAD
    if event == "push":
        payload.update(ref=MAIN, deleted=False, before=BASE, after=HEAD)
    if event == "workflow_dispatch":
        payload.update(ref="main", inputs={})
    if event == "pull_request":
        ref, event_sha, main = "refs/pull/8/merge", MERGE, BASE
        run.update(pull_requests=[dict(number=8, head={"sha": HEAD}, base={"sha": BASE})])
        payload.update(action="synchronize", number=8, pull_request=copy.deepcopy(pr))
    return dict(event=event, ref=ref, event_sha=event_sha, payload=payload, run=run,
                run_id=40, attempt=2, workflow_id=9, approved_workflow_blob=BLOB,
                observed_workflow_blob=BLOB, current_main=main, read_pull=lambda n: pr,
                read_merge=lambda n: (MERGE, [BASE, HEAD]))


class EventBinding(unittest.TestCase):
    def test_default_source_events(self):
        for event, expected in (("push", HEAD), ("workflow_dispatch", HEAD), ("pull_request", MERGE)):
            with self.subTest(event=event):
                result = build(**fixture(event))
                self.assertEqual(result["check_sha"], expected)
                self.assertEqual(result["execution_sha"], expected)
                self.assertEqual(result["source_head_sha"], HEAD)
                self.assertEqual(result["request_id"], "40-2")

    def test_attempt_and_blob(self):
        for field, value in (("attempt", 1), ("observed_workflow_blob", BASE)):
            args = fixture("push")
            args[field] = value
            with self.assertRaises(ValueError):
                build(**args)

    def test_pr_requires_link_and_current_refs(self):
        args = fixture("pull_request")
        for change in (dict(current_main=BLOB), dict(read_merge=lambda n: (MERGE, [HEAD, BASE])),
                       dict(run={**args["run"], "pull_requests": []})):
            with self.assertRaises(ValueError):
                build(**{**args, **change})

    def test_manual_has_no_override(self):
        args = fixture("workflow_dispatch")
        args["payload"]["inputs"] = {"source_ref": BASE}
        with self.assertRaisesRegex(ValueError, "manual input"):
            build(**args)

    def test_rejects_nonmain_push(self):
        args = fixture("push")
        args["ref"] = "refs/heads/agent/runtime-workflow-architecture-v1"
        with self.assertRaises(ValueError):
            build(**args)
