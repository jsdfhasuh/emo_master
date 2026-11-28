from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

import test_candidate_dispatch as fx
from native_journal import Admission, admit, admit_and_dispatch, decode, encode, identity, path


class MemoryJournal:
    def __init__(self): self.row=None; self.writes=0
    def read(self,key): return "1"*40,self.row
    def cas(self,key,base,row,nonce):
        self.writes+=1
        if self.row: return False
        self.row=row
        return True


class NativeJournalTests(TestCase):
    def setUp(self):
        f=fx.CandidateDispatch(); f.setUp()
        b,self.r=f.prepare()
        self.b=replace(b,contexts=("emo-ci-shadow/source",))
        self.j=MemoryJournal()

    def test_later_policy_cannot_redispatch_same_native_event(self):
        a=admit(self.j,self.b,self.r,now=100,deadline=200)
        self.assertEqual(admit(self.j,self.b,self.r,now=110,deadline=900),a)
        changed=replace(self.r,worker_sha="c"*40)
        with self.assertRaisesRegex(ValueError,"already admitted"):
            admit(self.j,replace(self.b,worker_sha=changed.worker_sha),changed,now=120,deadline=900)
        self.assertEqual(self.j.writes,1)
        self.assertEqual(self.j.row.deadline,200)

    def test_recovery_reconstructs_canonical_bound_without_reshaping_inputs(self):
        key=identity(self.b); row=Admission(self.b,self.r,100,200)
        self.assertEqual(decode(key,encode(key,row)),row)
        self.assertNotEqual(path(key),path((key[0],key[1],key[2]+1,key[3])))
        with self.assertRaises(ValueError):decode(key,encode(key,row)+b" ")
        with self.assertRaisesRegex(ValueError,"namespace"):
            encode(key,replace(row,bound=replace(self.b,contexts=("formal/source",))))

    def test_no_network_when_first_admission_has_not_been_confirmed(self):
        from types import SimpleNamespace
        self.j.cas=lambda *_:False
        with patch("native_journal.dispatch_or_read_back") as call:
            with self.assertRaisesRegex(ValueError,"not confirmed"):
                admit_and_dispatch(SimpleNamespace(_authorized=True,_writer_id=self.r.actor),
                     None,self.j,self.b,self.r,now=100,deadline=200)
            call.assert_not_called()
