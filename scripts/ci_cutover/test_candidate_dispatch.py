from dataclasses import replace
from io import BytesIO
from hashlib import sha256
import json
import unittest
from zipfile import ZipFile

from candidate_dispatch import prepare, dispatch_or_read_back
import test_trusted_route as route_fixture
from trusted_route import PRODUCT
from test_dispatch_once import Git
from test_durable_claims import CAS


class CandidateDispatch(unittest.TestCase):
    def setUp(self):
        self.source = route_fixture.TestRoute()
        self.source.setUp()

    def prepare(self, mutate=None, html=False):
        f = self.source
        output = BytesIO()
        with ZipFile(output, "w") as z:
            z.writestr("ci-request.json", json.dumps(f.request))
            if html:
                z.writestr("extra", "")
        data = output.getvalue()
        art = dict(id=201, name="ci-request-100-1", digest="sha256:" + sha256(data).hexdigest(),
                   expired=False, size_in_bytes=len(data),
                   workflow_run=dict(id=100, repository_id=PRODUCT, head_repository_id=PRODUCT,
                                     head_sha=f.run["head_sha"]))
        if mutate:
            art.update(mutate)
        return prepare(f.lane, f.run, art, data,
                       worker_path=".github/workflows/dispatch-source-candidate.yml", actor=9,
                       now=10000, emitted=9999, read_ref=f.ref.get, read_pr=lambda _: f.pr,
                       read_parents=lambda _: f.parents, read_blob=lambda _h, _p: f.blob,
                       read_worker_ref=lambda _r: f.worker)

    def test_native_events_bind_exact_public_request_and_pr_merge_target(self):
        h = self.source.request["head"]
        for event in ("push", "workflow_dispatch", "pull_request"):
            with self.subTest(event=event):
                if event == "workflow_dispatch":
                    self.source.request.update(event=event)
                    self.source.run.update(event=event)
                if event == "pull_request":
                    self.source.make_pr()
                bound, req = self.prepare()
                message = json.loads(req.wire)
                self.assertEqual(bound.event, event)
                self.assertEqual(message["head"], h)
                self.assertEqual(bound.check, message["execution"])
                if event == "pull_request":
                    self.assertNotEqual(bound.check, h)

    def test_unbound_artifact_or_duplicate_member_refused_before_any_dispatch(self):
        for bad in ({"digest":"sha256:"+"b"*64},{"name":"ci-request-100-2"},{"workflow_run":{}}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError,"unbound"):
                self.prepare(mutate=bad)
        with self.assertRaisesRegex(ValueError,"ambiguous"):
            self.prepare(html=True)

    def test_dispatch_held_request_reads_back_without_a_second_post(self):
        bound, req = self.prepare()
        gh = Git()
        gh.tag = req.worker_sha
        gh.read_run = lambda *_: dict(id=24, repository={"id":1468905732}, actor={"id":9},
            head_sha=req.worker_sha, head_branch=req.tag, event="workflow_dispatch", run_attempt=1,
            workflow_id=req.workflow, display_title=f"emo-ci-candidate-{req.identifier()}")
        store = CAS()
        self.assertEqual(dispatch_or_read_back(gh, store, bound, req), 24)
        before=gh.calls.count("POST")
        gh._request = lambda method,*a: ({"workflow_runs":[gh.read_run()]} if method=="GET"
                                         else self.fail("replayed dispatch"))
        self.assertEqual(dispatch_or_read_back(gh, store, bound, req), 24)
        self.assertEqual(before,1)
        with self.assertRaisesRegex(ValueError,"differs from bound"):
            dispatch_or_read_back(gh, store, replace(bound, run=101), req)
