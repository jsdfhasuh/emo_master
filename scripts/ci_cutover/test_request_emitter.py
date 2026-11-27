import unittest
from request_emitter import build,REPO

R={"id":REPO}
H,B,M,W=["1"*40,"2"*40,"3"*40,"4"*40]
class Emitter(unittest.TestCase):
    def e(self,event,origin_ref,sha,**req):
        return build(event,origin_ref,sha,"23","1",dict(repository=R,**req))
    def test_push_native_scope(self):
        ref="refs/heads/agent/runtime-workflow-architecture-v1"
        out=self.e("push",ref,H,**{"after":H,"deleted":False,**{"ref":ref}})
        self.assertEqual(out["head"],H)
        self.assertEqual(out["inputs"],{})
    def test_tag_is_identity_only(self):
        ref="refs/tags/v0.6.6"
        out=build("push",ref,H,"23","1",dict(repository=R,after=H,deleted=False,ref=ref))
        self.assertEqual(out["ref"],ref)
    def test_manual_keeps_packager_and_source(self):
        out=build("workflow_dispatch","refs/heads/main",H,"23","1",
                  dict(repository=R,ref="main",inputs=dict(source_ref="main",packager_ref=W)))
        self.assertEqual(out["inputs"],dict(source_ref="main",packager_ref=W))
    def test_manual_refuses_secrets_or_shell(self):
        for inputs in (dict(token="placeholder"),dict(source_ref="main",packager_ref="wrong; exit")):
            with self.assertRaises(ValueError):
                build("workflow_dispatch","refs/heads/main",H,"23","1",dict(repository=R,ref="main",inputs=inputs))
    def test_pr_merges_exact_sha_and_rejects_fork(self):
        pr=dict(head=dict(repo=R,sha=H),base=dict(repo=R,sha=B))
        out=self.e("pull_request","refs/pull/9/merge",M,action="opened",number=9,pull_request=pr)
        self.assertEqual((out["head"],out["base"],out["merge"]),(H,B,M))
        pr["head"]["repo"]={"id":2}
        with self.assertRaises(ValueError):self.e("pull_request","refs/pull/9/merge",M,action="opened",number=9,pull_request=pr)
