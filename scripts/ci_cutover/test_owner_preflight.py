import unittest
from types import SimpleNamespace
from owner_preflight import *

H='3'*40
W='4'*40
P=DeploymentPins(2,H,3,W,4,'.github/workflows/broker.yml','coord',W,'ci-worker-src',5,'.github/workflows/dispatch-source-candidate.yml')

class TestOwnerPreflight(unittest.TestCase):
    def setUp(self):
        root=f'/repos/{PRIVATE}'
        self.state={
          '/user':dict(id=2),
          root+'/branches/main':dict(commit=dict(sha=H)),
          root+'/pulls/3':dict(merged=True,merge_commit_sha=H,merged_by=dict(id=2),base=dict(ref='main',repo=dict(id=IDS[PRIVATE])),user=dict(id=2),head=dict(sha=W)),
          root+'/environments/coord':dict(name='coord',deployment_branch_policy=dict(protected_branches=False,custom_branch_policies=True)),
          root+'/environments/coord/deployment-branch-policies?per_page=100':dict(total_count=1,branch_policies=[dict(name='main',type='branch')]),
          root+'/actions/runs/6/attempts/1':dict(id=6,run_attempt=1,status='in_progress',head_sha=H,
               head_branch='main',workflow_id=4,path=P.runtime_workflow_path,repository=dict(id=IDS[PRIVATE]),head_repository=dict(id=IDS[PRIVATE]),event='push',actor=dict(id=2),triggering_actor=dict(id=2)),
          root+'/rulesets?includes_parents=true&per_page=100':[dict(id=9,target='branch',enforcement='active')],
          root+'/rulesets/9':dict(target='branch',enforcement='active',bypass_actors=[],conditions=dict(ref_name=dict(include=['refs/heads/private-ci-ledger'],exclude=[])),rules=[dict(type='non_fast_forward'),dict(type='deletion')]),
          f'/repos/{PUBLIC}/rulesets?includes_parents=true&per_page=100':[dict(id=8,target='tag',enforcement='active')],
          f'/repos/{PUBLIC}/rulesets/8':dict(target='tag',enforcement='active',bypass_actors=[],conditions=dict(ref_name=dict(include=['refs/tags/ci-worker-src'],exclude=[])),rules=[dict(type='update'),dict(type='deletion')])
        }
        self.gh=SimpleNamespace(_request=lambda meth,path: self.state[path],
          read_repo=lambda r:dict(owner=dict(id=2,type='User')), worker_tag=lambda _:W,read_workflow=lambda *a: {},
          read_protection=lambda *a:dict(allow_force_pushes=dict(enabled=False),allow_deletions=dict(enabled=False),enforce_admins=dict(enabled=True)))
    def run_preflight(self,enabled=True):
        return enable_owner_source(self.gh,enabled=enabled,pins=P,runtime_run=6,runtime_attempt=1)
    def test_dark(self):
        self.state.clear()
        self.assertEqual(self.run_preflight(False),'dark')
        self.assertFalse(self.gh._authorized)
    def test_held_pins(self):
        self.assertEqual(self.run_preflight(),'eligible_owner_coordinated_source_shadow_only')
        self.assertEqual(self.gh._contexts,set(SOURCE_CONTEXTS))
    def test_runtime_stale_or_foreign(self):
        row=self.state[f'/repos/{PRIVATE}/actions/runs/6/attempts/1']
        for key,value in [('head_branch','other'),('head_sha',W),('run_attempt',2),('event','pull_request_target'),('path','different')]:
            old=row[key]
            row[key]=value
            with self.assertRaises(ValueError): self.run_preflight()
            self.assertFalse(self.gh._authorized)
            row[key]=old
    def test_no_state_admin_enforcement(self):
        self.gh.read_protection=lambda *a:{}
        with self.assertRaises(ValueError): self.run_preflight()
    def test_stale_review(self):
        self.state[f'/repos/{PRIVATE}/pulls/3']['head']['sha']=H
        with self.assertRaises(ValueError): self.run_preflight()
    def test_tag_bypass(self):
        self.state[f'/repos/{PUBLIC}/rulesets/8']['bypass_actors']=[dict(actor_id=2)]
        with self.assertRaises(ValueError): self.run_preflight()
