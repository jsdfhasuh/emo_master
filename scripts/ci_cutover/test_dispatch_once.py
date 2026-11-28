import json,unittest
from dispatch_once import *

W="4"*40
D=dict(schema=1,source_run=21,source_attempt=1,artifact_id=22,artifact_digest="sha256:"+"a"*64,
       head="1"*40,base=None,execution="1"*40,pr=None,route="source")
def wire(d):
    return json.dumps(d,ensure_ascii=True,separators=(",",":"),sort_keys=True)

class Git:
    def __init__(self):self._authorized=True;self._writer_id=9;self.tag=W;self.calls=[];self.fail=False;self.key=None
    def read_workflow(self,*args):self.calls.append("registered")
    def worker_tag(self,*args):return self.tag
    def _request(self,*a,**kw):
        self.calls.append("POST")
        self.key=a[2]["inputs"]["receipt_key"]
        if self.fail:raise UnknownWrite("verification pending")
        return {"workflow_run_id":24}
    def read_run(self,*a):
        return dict(id=24,repository=dict(id=IDS[PUBLIC]),actor=dict(id=self._writer_id),head_sha=W,
                    head_branch="ci-worker-a",event="workflow_dispatch",run_attempt=1,workflow_id=2,
                    display_title=f"emo-ci-candidate-{self.key}")

class Claim:
    def __init__(self):self.x={}
    def reserve(self,k):
        if k in self.x:return False
        self.x[k]=None
        return True
    def record(self,k,v):self.x[k]=v

class Cases(unittest.TestCase):
    def setUp(self):
        self.g=Git();self.c=Claim()
        self.r=ApprovedRequest(2,".github/workflows/dispatch-source-candidate.yml","ci-worker-a",W,9,wire(D))
    def call(self):
        return send_once(self.g,self.c,self.r)
    def test_exact_dispatch_once(self):
        self.assertEqual(self.call(),24)
        self.assertIsNone(self.call())
        self.assertEqual(self.g.calls.count("POST"),1)
    def test_lost_http_response_preserves_claim(self):
        self.g.fail=True
        with self.assertRaises(UnknownWrite):self.call()
        self.g.fail=False
        self.assertIsNone(self.call())
        self.assertEqual(self.g.calls.count("POST"),1)
    def test_wrong_tag_no_post(self):
        self.g.tag="f"*40
        with self.assertRaises(ValueError):self.call()
        self.assertNotIn("POST",self.g.calls)
    def test_foreign_receipt_is_not_bound(self):
        self.g.read_run=lambda *a:{"id":999}
        with self.assertRaises(ValueError):self.call()
        self.assertEqual(list(self.c.x.values()),[None])
    def test_rejects_noncanonical_or_secret_field(self):
        from dataclasses import replace
        for x in (json.dumps(D),wire(dict(D,token="not-real"))):
            self.r=replace(self.r,wire=x)
            with self.assertRaises(ValueError):self.call()
        self.assertFalse(self.g.calls)

    def test_recovery_of_unique_readback_never_posts(self):
        import test_durable_claims as mock
        self.c=DispatchClaims(mock.CAS(),"1"*40,"1"*40)
        self.g.fail=True
        with self.assertRaises(UnknownWrite): self.call()
        def rows(*a,**kw):
            self.assertEqual(a[0],"GET")
            return {"workflow_runs":[self.g.read_run()]}
        self.g._request=rows
        self.assertEqual(reconcile_run(self.g,self.c,self.r),24)
        self.assertEqual(reconcile_run(self.g,self.c,self.r),24)
        self.assertEqual(self.g.calls.count("POST"),1)

    def test_ambiguous_recovery_never_overwrites(self):
        import test_durable_claims as mock
        self.c=DispatchClaims(mock.CAS(),"1"*40,"1"*40)
        self.g.fail=True
        with self.assertRaises(UnknownWrite): self.call()
        self.g._request=lambda *a,**k:{"workflow_runs":[self.g.read_run(),dict(self.g.read_run(),id=25)]}
        with self.assertRaises(ValueError): reconcile_run(self.g,self.c,self.r)
        self.assertTrue(self.c.held(self.r.identifier()))
