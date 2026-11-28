import unittest
from dataclasses import replace
from durable_claims import Ledger, Conflict
from terminal_policy import Generation, Observation
from broker_tick import tick

class CAS:
    def __init__(self): self.rev=0; self.state={}; self.nonces=set(); self.reject=False
    def read(self,k): return self.state.get(k,(0,None))
    def cas(self,k,rev,new,nonce):
        assert nonce not in self.nonces
        self.nonces.add(nonce)
        if self.read(k)[0]!=rev or self.reject:return False
        self.rev+=1
        self.state[k]=(self.rev,new)
        return True

class HTTP:
    def __init__(self):self.calls=0; self.state='absent'
    def read(self,*_):return self.state
    def post(self,*_):
        self.calls+=1
        raise TimeoutError('mock unknown outcome')

class Claims(unittest.TestCase):
    def setUp(self):
        self.cas=CAS()
        self.l=Ledger(self.cas)
        self.g=Generation('a'*64,'2'*40,'3'*40,'emo-ci-shadow/source',1,4,1)
        self.key=self.l.install(self.g)
        self.obs=Observation(self.g,'success','success')
    def test_two_owners_cannot_overlap(self):
        first=self.l.acquire(self.key)
        self.assertIsNone(Ledger(self.cas).acquire(self.key))
        with self.assertRaises(Conflict):self.l.install(replace(self.g,revision=2))
        with first: pass
        self.assertIsNotNone(self.l.acquire(self.key))
    def test_timeout_readback_only_and_fence_retained(self):
        h=HTTP()
        def run():return tick(Ledger(self.cas),h,self.key,self.g,self.obs,now=10,deadline=20)
        self.assertEqual(run(),"uncertain")
        self.assertEqual(run(),"uncertain")
        self.assertEqual(h.calls,1)
        with self.assertRaises(Conflict):self.l.install(replace(self.g,revision=2))
        h.state="exact"
        self.assertEqual(run(),"sealed")
        self.l.install(replace(self.g,revision=2))
    def test_inflight_exception_becomes_uncertain(self):
        with self.assertRaises(ValueError):
            with self.l.acquire(self.key) as p:
                p.freeze(("error","deadline"))
                p.claim()
                raise ValueError("write had no return")
        self.assertEqual(self.cas.read(self.key)[1].phase,"uncertain")
    def test_crash_owner_not_stolen(self):
        first=self.l.acquire(self.key)
        first.freeze(("error","deadline"))
        first.claim()
        # Simulate killed process: no __exit__ and a separate Ledger instance.
        self.assertIsNone(Ledger(self.cas).acquire(self.key))
    def test_refusal_grants_no_claim(self):
        self.cas.reject=True
        self.assertIsNone(self.l.acquire(self.key))
        self.assertIsNone(self.cas.read(self.key)[1].owner)
