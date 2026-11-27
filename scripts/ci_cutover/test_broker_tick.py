from types import SimpleNamespace
import unittest
from dataclasses import replace
from broker_tick import tick, DefiniteRejection
from test_terminal_policy import G
from terminal_policy import Observation

class Fake:
    def __init__(self):
        self.state=SimpleNamespace(generation=G,decision=None,phase='idle',tries=0)
        self.reply='absent'; self.posts=0; self.failure=None; self.locked=False
    def acquire(self,key): return None if self.locked else self
    def __enter__(self): self.locked=True; return self
    def __exit__(self,*args): self.locked=False
    def snapshot(self): return self.state
    def freeze(self,value): self.state.decision=value
    def seal(self): self.state.phase='sealed'
    def block(self): self.state.phase='blocked'
    def uncertain(self): self.state.phase='uncertain'
    def claim(self):
        self.state.phase='inflight'; self.state.tries+=1
    def release_claim(self): self.state.phase='idle'
    def read(self,*args): return self.reply
    def post(self,*args):
        assert self.state.phase=='inflight'
        self.posts+=1
        if self.failure: raise self.failure
        self.reply='exact'

class Check(unittest.TestCase):
    def setUp(self): self.f=Fake()
    def go(self, time=4, evidence=None, expected=G):
        return tick(self.f,self.f,'k',expected,evidence,now=time,deadline=3)
    def test_deadline(self):
        self.assertEqual(self.go(2),'wait')
        self.assertEqual(self.go(),'sealed')
        self.assertEqual(self.f.state.decision,('error','missing_receipt'))
    def test_uncertain_readback(self):
        self.f.failure=TimeoutError()
        self.assertEqual(self.go(),'uncertain')
        self.assertEqual(self.go(),'uncertain')
        self.f.reply='exact'
        self.assertEqual(self.go(),'sealed')
        self.assertEqual(self.f.posts,1)
    def test_no_steal(self):
        self.f.locked=True
        self.assertEqual(self.go(),'busy')
        self.assertEqual(self.f.posts,0)
    def test_stale(self):
        self.f.state.generation=replace(G,attempt=2)
        self.assertEqual(self.go(),'superseded')
        self.assertEqual(self.f.posts,0)
    def test_definite_rejection_bounded(self):
        self.f.failure=DefiniteRejection()
        self.assertEqual([self.go() for _ in range(4)],['retry']*3+['blocked'])
        self.assertEqual(self.f.posts,3)
    def test_crash_after_claim(self):
        self.f.state.decision=('error','missing_receipt')
        self.f.claim()
        self.assertEqual(self.go(),'uncertain')
        self.assertEqual(self.f.posts,0)

    def test_worker_green_without_v2_proof_waits_then_errors(self):
        observation = Observation(G, 'success', 'missing')
        self.assertEqual(self.go(2, observation), 'wait')
        self.assertEqual(self.f.posts, 0)
        self.assertEqual(self.go(3, observation), 'sealed')
        self.assertEqual(self.f.state.decision, ('error', 'deadline_without_verified_result'))

    def test_v2_verification_failure_never_publishes_success(self):
        self.assertEqual(self.go(2, Observation(G, 'success', 'failure')), 'sealed')
        self.assertEqual(self.f.state.decision, ('failure', 'verified_failure'))
        self.assertEqual(self.f.posts, 1)

    def test_status_receipt_then_mismatched_readback_does_not_repost(self):
        def post_without_matching_readback(*args):
            self.f.posts += 1
            self.f.reply = 'foreign'
        self.f.post = post_without_matching_readback
        self.assertEqual(self.go(), 'uncertain')
        self.assertEqual(self.go(), 'uncertain')
        self.assertEqual(self.f.posts, 1)

    def test_request_restart_after_readback_disconnect_does_not_repost(self):
        original_post = self.f.post
        def post_then_disconnect(*args):
            original_post(*args)
            def disconnect(*args):
                raise ConnectionError('simulated GitHub readback disconnect')
            self.f.read = disconnect
        self.f.post = post_then_disconnect
        with self.assertRaises(ConnectionError):
            self.go()
        self.assertEqual(self.f.state.phase, 'inflight')
        self.f.read = lambda *args: 'exact'
        self.assertEqual(self.go(), 'sealed')
        self.assertEqual(self.f.posts, 1)

    def test_prior_attempt_green_cannot_write_for_new_attempt(self):
        generation = replace(G, attempt=G.attempt + 1)
        self.f.state.generation = generation
        older = Observation(G, 'success', 'success')
        self.assertEqual(self.go(2, older, generation), 'wait')
        self.assertEqual(self.f.posts, 0)
        self.assertEqual(self.go(3, older, generation), 'sealed')
        self.assertEqual(self.f.state.decision, ('error', 'matching_receipt_missing'))
