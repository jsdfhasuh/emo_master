from dataclasses import replace
import unittest
from terminal_policy import *
G=Generation('a'*64,'b'*40,'c'*40,'source / Windows',1,4,1)
class Policy(unittest.TestCase):
    def result(self,c='success',p='success',**kw):
        return terminal(G,G,Observation(G,c,p,**kw),now=2,deadline=3)[0]
    def test_green(self): self.assertEqual(self.result(),'success')
    def test_missing_proof(self): self.assertEqual(self.result(p='missing'),'wait')
    def test_failure(self): self.assertEqual(self.result('failure'),'failure')
    def test_source(self): self.assertEqual(self.result('source_failure'),'error')
    def test_cancel_watchdog(self):
        for w,reason in [(False,'cancelled'),(True,'timeout')]:
            self.assertEqual(terminal(G,G,Observation(G,'cancelled','missing',w),now=2,deadline=3),('error',reason))
    def test_expired_send(self):
        q=replace(G,worker_run=None,attempt=None)
        self.assertEqual(terminal(q,q,None,now=3,deadline=3),('error','missing_receipt'))
    def test_not_terminal(self): self.assertEqual(self.result('in_progress'),'wait')
    def test_deadline(self):
        self.assertEqual(terminal(G,G,Observation(G,'success','missing'),now=3,deadline=3)[0],'error')
    def test_late_success_does_not_reopen_deadline(self):
        self.assertEqual(terminal(G,G,Observation(G,'success','success'),now=3,deadline=3),
                         ('error','deadline_without_verified_result'))
    def test_stale(self):
        for key,value in [('attempt',2),('revision',2),('execution_sha','f'*40)]:
            newer=replace(G,**{key:value})
            self.assertEqual(terminal(G,newer,None,now=4,deadline=3)[0],'ignore')
            self.assertEqual(terminal(newer,newer,Observation(G,'success','success'),now=1,deadline=2)[0],'wait')
    def test_retry(self):
        for receipt,n,result in [('exact',3,'sealed'),('absent',0,'claim_then_post'),('own_pending',3,'escalate_no_write'),('foreign',0,'escalate_no_write'),('ambiguous',0,'escalate_no_write')]:
            self.assertEqual(delivery(G,G,receipt=receipt,desired='success',tries=n),result)
