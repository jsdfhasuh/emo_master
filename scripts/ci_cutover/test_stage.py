import unittest,tempfile,hashlib
from pathlib import Path
from private_release_stage import *

class Fake:
    def __init__(self): self.data={};self.private=True;self.draft=True;self.sha='a'*40
    def get(self,p):
        if not p:return {'private':self.private,'id':REPO_ID,'full_name':REPO}
        if p.startswith('/git/ref'):return {'object':{'type':'commit','sha':self.sha}}
        if p.endswith('=100'):return list(self.data.values())
        return {'draft':self.draft,'id':1,'tag_name':'v0.6.6'}
    def upload(self,i,n,p):
        self.data[n]={'name':n,'state':'uploaded','size':1,'digest':'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()}

class Cases(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.dir=Path(self.tmp.name);self.c=Fake()
        self.r=dict(repo=REPO,tag='v0.6.6',product_sha='a'*40,controller_sha='b'*40,packager_sha='c'*40,reusable_sha='d'*40,run_id=123,attempt=1,v2_recovered=True,source_gate_passed=True,assets={})
        for n in asset_names(self.r['tag']):
            (self.dir/n).write_bytes(b'x');self.r['assets'][n]=dict(size=1,sha256=hashlib.sha256(b'x').hexdigest())
    def run_stage(self):return stage(self.c,self.dir,self.r,1,'a'*40,(123,1),('b'*40,'c'*40,'d'*40))
    def test_resume(self):
        self.assertEqual(len(self.run_stage()),6);self.c.upload=lambda *args:self.fail();self.run_stage()
    def test_public(self):
        self.c.private=False
        with self.assertRaises(ValueError):self.run_stage()
    def test_live(self):
        self.c.draft=False
        with self.assertRaises(ValueError):self.run_stage()
    def test_tag_move(self):
        self.c.sha='f'*40
        with self.assertRaises(ValueError):self.run_stage()
    def test_hash(self):
        (self.dir/'manifest.json').write_bytes(b'y')
        with self.assertRaises(ValueError):self.run_stage()
        self.assertFalse(self.c.data)
    def test_incomplete(self):
        self.r['assets'].pop('manifest.json')
        with self.assertRaises(ValueError):self.run_stage()
    def test_collision(self):
        self.run_stage();self.c.data['manifest.json']['digest']=None
        with self.assertRaises(ValueError):self.run_stage()
    def test_wrong_attempt(self):
        self.r['attempt']=2
        with self.assertRaises(ValueError):self.run_stage()
    def test_missing_receipt(self):
        self.r['v2_recovered']=False
        with self.assertRaises(ValueError):self.run_stage()
    def test_changed_packager(self):
        self.r['packager_sha']='f'*40
        with self.assertRaises(ValueError):self.run_stage()
