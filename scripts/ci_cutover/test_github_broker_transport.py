import io,json,unittest
from urllib.error import HTTPError
from github_broker_transport import *

class Resp:
    def __init__(self, status, body): self.status=status; self.body=json.dumps(body).encode()
    def __enter__(self): return self
    def __exit__(self,*_): pass
    def read(self,n): return self.body[:n]

class Repo:
    def __init__(self): self.log=[]; self.rows={}
    def open(self,req,timeout):
        pair=(req.method,req.full_url.removeprefix(HOST))
        self.log.append(pair)
        r=self.rows[pair]
        if isinstance(r,BaseException): raise r
        if isinstance(r,int): raise HTTPError(req.full_url,r,"mock",{},io.BytesIO())
        assert timeout==15
        return Resp(*r)

class T(unittest.TestCase):
    def setUp(self):
        self.repo=Repo()
        self.gh=GitHub("mock-no-live-token",self.repo)
        self.h="1"*40
        self.ctx="emo-ci-shadow/source/linux"
        self.aid=18
        self.post=("POST",f"/repos/{PRODUCT}/statuses/{self.h}")
        self.review={"sole_writer":True,"immutable_tags":True,"protected_private_environment":True,"private_deployment_reviewed":True}
        self.repo.rows["GET","/user"]=200,{"id":self.aid}
        for key,rid in IDS.items():
            self.repo.rows["GET",f"/repos/{key}"]=200,{"id":rid,"private":key==PRIVATE,"archived":False}
        for key,branch in [(PRIVATE,LEDGER),(PRIVATE,"main"),(PUBLIC,"main")]:
            path=f"/repos/{key}/branches/{branch}"
            self.repo.rows["GET",path]=200,{"name":branch,"protected":True}
            self.repo.rows["GET",path+"/protection"]=200,{"enforce_admins":{"enabled":True},"allow_force_pushes":{"enabled":False},
                "allow_deletions":{"enabled":False},"required_status_checks":None,"required_pull_request_reviews":None}
    def permit(self,**kw):
        args=dict(enabled=True,actor_id=self.aid,contexts=(self.ctx,),review=self.review)
        args.update(kw)
        return self.gh.protected_preflight(**args)
    def write(self,context=None):
        return self.gh.post_status(self.h,context or self.ctx,"success",234,"verified run 234 attempt 1")
    def approved(self):
        self.repo.rows[self.post]=201,{"context":self.ctx,"state":"success",
           "creator":{"id":self.aid},"target_url":f"https://github.com/{PUBLIC}/actions/runs/234","description":"verified run 234 attempt 1"}
    def test_dark_performs_no_io(self):
        self.assertEqual(self.permit(enabled=False),"dark")
        with self.assertRaises(Refused): self.write()
        self.assertFalse(self.repo.log)
    def test_missing_ledger(self):
        self.repo.rows["GET",f"/repos/{PRIVATE}/branches/{LEDGER}"]=404
        with self.assertRaises(Rejected): self.permit()
        with self.assertRaises(Refused): self.write()
        self.assertNotIn(self.post,self.repo.log)
    def test_force_and_admin_bypass(self):
        p=self.repo.rows["GET",f"/repos/{PRIVATE}/branches/{LEDGER}/protection"][1]
        for key,val in (("allow_force_pushes",True),("allow_deletions",True),("enforce_admins",False)):
            original=p[key]
            p[key]={"enabled":val}
            with self.assertRaises(Refused): self.permit()
            p[key]=original
    def test_scope_is_optional_only(self):
        with self.assertRaises(Refused): self.permit(contexts=("ubuntu-latest / Python 3.10",))
        self.permit()
        with self.assertRaises(Refused): self.write("different")
        self.assertNotIn(self.post,self.repo.log)
    def test_verified_receipt_and_forgery(self):
        self.permit()
        self.approved()
        self.assertEqual(self.write()["target_url"],f"https://github.com/{PUBLIC}/actions/runs/234")
        self.repo.rows[self.post][1]["creator"]["id"]=9999
        with self.assertRaises(UnknownWrite): self.write()
    def test_network_or_rate_limit_ambiguous(self):
        self.permit()
        for v in (502,429,302,TimeoutError()):
            self.repo.rows[self.post]=v
            with self.assertRaises(UnknownWrite): self.write()
        self.assertEqual(self.repo.log.count(self.post),4)
    def test_actor_and_attestation(self):
        with self.assertRaises(Refused): self.permit(review={})
        with self.assertRaises(Refused): self.permit(actor_id=27)
    def test_registered_exact_workflow_only(self):
        self.repo.rows["GET",f"/repos/{PUBLIC}/actions/workflows/2"]=200,{"id":2,"state":"active","path":".github/workflows/dispatch-source-candidate.yml"}
        self.assertEqual(self.gh.read_workflow(2,".github/workflows/dispatch-source-candidate.yml")["id"],2)
        with self.assertRaises(Refused): self.gh.read_workflow(2,".github/workflows/dispatch-web-candidate.yml")

    def g(self,worker=234):
        from terminal_policy import Generation
        return Generation("a"*64,self.h,"2"*40,self.ctx,1,worker,1 if worker else None)

    def test_pagination_matches_exact_owner_and_binding(self):
        self.permit()
        adapter=StatusWriter(self.gh,123)
        gen=self.g()
        first=f"/repos/{PRODUCT}/commits/{self.h}/statuses?per_page=100&page=1"
        second=f"/repos/{PRODUCT}/commits/{self.h}/statuses?per_page=100&page=2"
        self.repo.rows["GET",first]=200,[dict(id=i,context="other") for i in range(1,101)]
        row=dict(id=101,context=self.ctx,state="success",target_url=self.gh.target_url(234,123),
            description=adapter._description(gen,"success"),creator={"id":self.aid})
        self.repo.rows["GET",second]=200,[row]
        self.assertEqual(adapter.read(gen,("success","verified")),"exact")
        row.update(creator={"id":17})
        self.assertEqual(adapter.read(gen,("success","verified")),"foreign")

    def test_missing_worker_deadline_targets_real_product_run(self):
        self.permit()
        adapter=StatusWriter(self.gh,123)
        gen=self.g(worker=None)
        self.repo.rows[self.post]=201,{"context":self.ctx,"state":"error","creator":{"id":self.aid},
           "target_url":f"https://github.com/{PRODUCT}/actions/runs/123","description":adapter._description(gen,"error")}
        adapter.post(gen,("error","missing_receipt"))
        self.assertIn(self.post,self.repo.log)

    def test_required_or_not_appendable_ledger_stays_dark(self):
        p=self.repo.rows["GET",f"/repos/{PRIVATE}/branches/{LEDGER}/protection"][1]
        p["required_status_checks"]={"checks":[]}
        with self.assertRaises(Refused): self.permit()
        with self.assertRaises(Refused): self.write()

    def test_auth_rejection_vs_inconclusive(self):
        from broker_tick import DefiniteRejection
        self.permit()
        adapter=StatusWriter(self.gh,123)
        self.repo.rows[self.post]=403
        with self.assertRaises(DefiniteRejection): adapter.post(self.g(),("error","missing_receipt"))
        self.repo.rows[self.post]=503
        with self.assertRaises(UnknownWrite): adapter.post(self.g(),("error","missing_receipt"))
