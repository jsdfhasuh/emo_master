import unittest
from copy import deepcopy
from dataclasses import replace
from trusted_route import bind, Lane, PRODUCT, PUBLIC

H,B,M,W,E=['1'*40,'2'*40,'3'*40,'4'*40,'5'*40]

class TestRoute(unittest.TestCase):
    def setUp(self):
        self.ref={"refs/heads/main":H,"refs/heads/trusted/topic":H,"refs/pull/31/merge":M,"refs/tags/v0.6.6":H}
        self.lane=Lane("source",".github/workflows/product-ci-request.yml",11,E,("main",),
                   ("main",),("main","trusted/topic"),("trusted/topic",),22,W,"refs/tags/ci-worker-fixed",
                   ("ci/source/linux","ci/source/windows"))
        self.request=dict(event="push",ref="refs/heads/main",head=H,base=None,merge=None,pr=None,run=100,attempt=1,repo=PRODUCT,inputs={})
        self.run=dict(id=100,run_attempt=1,workflow_id=11,path=self.lane.origin_path,event="push",
                      status="completed",conclusion="success",repository={"id":PRODUCT},head_repository={"id":PRODUCT},
                      head_sha=H,head_branch="main",pull_requests=[])
        self.pr={}
        self.blob=E
        self.worker=W
        self.parents=(B,H)
    def route(self, age=3):
        return bind(self.lane,self.request,self.run,now=10000,emitted=10000-age,read_ref=self.ref.get,
                    read_pr=lambda n:self.pr,read_parents=lambda h:self.parents,read_blob=lambda h,p:self.blob,
                    read_worker_ref=lambda ref:self.worker,resolve_runtime_source=lambda ref:B,
                    resolve_runtime_packager=lambda ref:W)
    def make_pr(self):
        self.request.update(event="pull_request",ref="refs/pull/31/merge",base=B,merge=M,pr=31)
        self.run.update(event="pull_request",head_branch="trusted/topic",
                        pull_requests=[{"number":31,"head":{"sha":H},"base":{"sha":B}}])
        self.pr={"number":31,"state":"open","mergeable":True,"merge_commit_sha":M,
                 "head":{"ref":"trusted/topic","sha":H,"repo":{"id":PRODUCT}},
                 "base":{"ref":"main","sha":B,"repo":{"id":PRODUCT}}}
        self.ref["refs/heads/main"]=B
    def test_push(self):
        b=self.route()
        self.assertEqual((b.head,b.check,b.execute,b.worker_repository),(H,H,H,PUBLIC))
    def test_manual_ignores_unrelated_pr_association(self):
        self.run.update(event="workflow_dispatch",head_branch="trusted/topic",pull_requests=[{"number":99}])
        self.request.update(event="workflow_dispatch",ref="refs/heads/trusted/topic")
        self.assertEqual(self.route().execute,H)
    def test_pr_merge_status(self):
        self.make_pr()
        b=self.route()
        self.assertEqual((b.head,b.base,b.merge,b.check,b.execute,b.pr),(H,B,M,M,M,31))
    def test_stale_delivery_and_branch(self):
        for age in [-1,1801]:
            with self.assertRaises(ValueError):self.route(age)
        self.ref["refs/heads/main"]=M
        with self.assertRaises(ValueError):self.route()
    def test_external_pr(self):
        self.make_pr()
        self.pr["head"]["repo"]["id"]=999
        with self.assertRaises(ValueError):self.route()
    def test_old_run_attempt_stale_worker_and_emitter(self):
        self.run["run_attempt"]=2
        with self.assertRaises(ValueError):self.route()
        self.run["run_attempt"]=1
        self.worker=E
        with self.assertRaises(ValueError):self.route()
        self.worker=W
        self.blob=W
        with self.assertRaises(ValueError):self.route()
    def test_merge_parent_direction(self):
        self.make_pr()
        self.parents=(H,B)
        with self.assertRaises(ValueError):self.route()
    def test_relative_ref_or_mismatched_repo(self):
        self.request["ref"]="refs/heads/a/../main"
        self.run["head_branch"]="a/../main"
        with self.assertRaises(ValueError):self.route()
        self.request["ref"]="refs/heads/main"
        self.run["head_branch"]="main"
        self.run["head_repository"]={"id":999}
        with self.assertRaises(ValueError):self.route()
    def test_publication_route_disabled_by_default(self):
        self.request["ref"]="refs/tags/v0.6.6"
        self.run["head_branch"]="v0.6.6"
        with self.assertRaises(ValueError):self.route()
        self.lane=replace(self.lane,release_tags=True)
        self.assertEqual(self.route().ref,"refs/tags/v0.6.6")
    def test_green_emitter_not_a_worker_verdict(self):
        b=self.route().record()
        self.assertTrue("conclusion" not in b and "success" not in b)
        self.run["conclusion"]="failure"
        with self.assertRaises(ValueError): self.route()
    def test_manual_inputs_and_helper_pin(self):
        self.run.update(event="workflow_dispatch")
        self.request.update(event="workflow_dispatch",inputs={"source_ref":"release-tested","packager_ref":W})
        with self.assertRaises(ValueError):self.route()
        self.lane=replace(self.lane,key="runtime-windows",packager_sha=W,manual_options=(("packager_ref",(W,)),("source_ref",("release-tested",))),helper_blobs=(("scripts/receipt.py",E),))
        self.assertEqual(self.route().inputs,(("packager_ref",W),("source_ref","release-tested")))
        self.assertEqual((self.route().check,self.route().execute,self.route().packager_sha),(H,B,W))
        self.lane=replace(self.lane,helper_blobs=(("scripts/receipt.py",W),))
        with self.assertRaises(ValueError):self.route()
