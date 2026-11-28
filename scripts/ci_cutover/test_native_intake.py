from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from unittest import TestCase
from urllib.error import HTTPError
from zipfile import ZipFile

from github_broker_transport import PRODUCT, IDS, Refused
from native_intake import bind_native, artifact_zip
import test_trusted_route as fixture


class Native(TestCase):
    def setUp(self):
        self.f = fixture.TestRoute()
        self.f.setUp()

    def bind(self, *, duplicate=False, actor=9, latest=1):
        f = self.f
        f.run.update(actor={"id": 9}, triggering_actor={"id": actor})
        output = BytesIO()
        with ZipFile(output, "w") as z:
            z.writestr("ci-request.json", json.dumps(f.request))
        archive = output.getvalue()
        art = dict(id=12, name="ci-request-100-1", size_in_bytes=len(archive), expired=False,
                   digest="sha256:" + sha256(archive).hexdigest(), created_at="2026-01-01T00:00:00Z",
                   workflow_run={"id":100,"repository_id":IDS[PRODUCT],"head_repository_id":IDS[PRODUCT],
                                 "head_sha":f.run["head_sha"]})
        root = "/repos/" + PRODUCT
        prefix = root + "/actions/runs/100"
        responses = {prefix: dict(f.run,run_attempt=latest), prefix+"/attempts/1": f.run, root+"/pulls/31":f.pr,
                     prefix+"/artifacts?name=ci-request-100-1&per_page=50&page=1":
                         {"artifacts":[art,art] if duplicate else [art]},
                     root+"/actions/artifacts/12": art,
                     prefix+"/attempts/1/jobs?per_page=50&page=1": {"jobs":[dict(id=1,name="native",run_id=100,
                        head_sha=f.run["head_sha"],run_attempt=1,status="completed",conclusion="success")]}}
        for ref,commit in f.ref.items():
            responses[root+"/git/ref/"+ref.removeprefix("refs/")] = {"ref":ref,"object":{"type":"commit","sha":commit}}
        for commit in f.ref.values():
            responses[root+"/contents/"+f.lane.origin_path+"?ref="+commit] = dict(type="file",path=f.lane.origin_path,sha=f.blob)
            responses[root+"/git/commits/"+commit] = dict(sha=commit,parents=[{"sha":p} for p in f.parents])
        class API:
            def _request(self,method,path):
                assert method=="GET"
                return responses[path]
            def read_workflow(self,wid,path): pass
            def worker_tag(self,tag): return f.worker
        return bind_native(API(),replace(f.lane,receipt_job_name="native"),".github/workflows/dispatch-source-candidate.yml",
                           9,100,1,clock=lambda:1767225601,fetch=lambda *_:archive)

    def test_push_pr_manual(self):
        for event in ("push","workflow_dispatch","pull_request"):
            if event=="pull_request": self.f.make_pr()
            else:
                self.f.request["event"]=self.f.run["event"]=event
            with self.subTest(event=event):
                b,r=self.bind()
                self.assertEqual(b.event,event)
                self.assertEqual(json.loads(r.wire)["execution"], self.f.request["merge"] or self.f.request["head"])

    def test_wrong_actor_duplicate_evidence_denied(self):
        with self.assertRaisesRegex(ValueError,"actor"): self.bind(actor=10)
        with self.assertRaisesRegex(ValueError,"ambiguous"): self.bind(duplicate=True)
        with self.assertRaisesRegex(ValueError,"superseded"): self.bind(latest=2)

    def test_credential_not_forwarded_to_storage(self):
        calls=[]
        def download(req,timeout):
            calls.append(req)
            if len(calls)==1:
                raise HTTPError(req.full_url,302,"",{"Location":"https://cache.blob.core.windows.net/opaque"},None)
            result=BytesIO(b"zip")
            result.status=200
            return result
        from types import SimpleNamespace
        gh=SimpleNamespace(_token="dummy",_http=SimpleNamespace(open=download))
        self.assertEqual(artifact_zip(gh,2,3),b"zip")
        self.assertTrue(calls[0].has_header("Authorization"))
        self.assertFalse(calls[1].has_header("Authorization"))
