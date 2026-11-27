import base64, unittest
from dataclasses import replace
from github_state_store import *
from durable_claims import Ledger
from terminal_policy import Generation

A,C,T="1"*40,"2"*40,"3"*40
CTX="emo-ci-shadow/source"
class Git:
    def __init__(self):self._authorized=True;self.tip=A;self.known={};self.writes=[];self.race=False
    def _request(self,method,path,payload=None,expected=200):
        suffix=path.removeprefix(P)
        if method=="GET":
            if suffix.startswith("/git/ref/heads"):
                return {"ref":REF,"object":{"type":"commit","sha":self.tip}}
            if suffix.startswith("/compare/"):
                return {"status":"ahead","ahead_by":1,"behind_by":0,"merge_base_commit":{"sha":A}}
            if suffix.startswith("/git/commits"):
                return {"sha":self.tip,"tree":{"sha":T}}
            if suffix.startswith("/contents/ci-ledger-anchor"):
                return {"type":"file","encoding":"base64","content":base64.b64encode(b"emo-ci-optional-ledger-v1\n").decode()}
            if suffix.startswith("/contents/ci-ledger/"):
                if self.tip==A:raise Rejected("read HTTP 404")
                item=self.known[self.tip]
                return {"size":len(item),"path":suffix[10:].split("?")[0],"type":"file",
                        "encoding":"base64","content":base64.b64encode(item).decode()}
        self.writes.append((method,suffix,payload))
        if suffix=="/git/trees":
            self.known[C]=payload["tree"][0]["content"].encode()
            return {"sha":T}
        if suffix=="/git/commits":
            return {"sha":C,"tree":{"sha":T},"parents":[{"sha":A}]}
        if self.race:raise Rejected("write rejected HTTP 422")
        self.tip=C
        return {"ref":REF,"object":{"sha":C}}

class Tests(unittest.TestCase):
    def setUp(self):
        self.git=Git()
        self.store=StateStore(self.git,A)
        self.g=Generation("e"*64,"4"*40,"5"*40,CTX,1,None,None)
        self.key=bind_key(self.g)
    def test_real_transport_payloads_and_readback(self):
        self.assertEqual(Ledger(self.store).install(self.g),self.key)
        self.assertEqual(self.store.read(self.key),(C,State(self.g)))
        self.assertEqual(self.git.writes[-1],("PATCH","/git/refs/heads/"+LEDGER,{"sha":C,"force":False}))
    def test_competing_commit_refusal(self):
        self.git.race=True
        self.assertFalse(self.store.cas(self.key,A,State(self.g),"a"*48))
        self.assertEqual(self.store.read(self.key),(A,None))
    def test_dark_and_foreign_context_do_not_write(self):
        self.git._authorized=False
        with self.assertRaises(ValueError): self.store.read(self.key)
        self.assertFalse(self.git.writes)
        with self.assertRaises(ValueError):member((self.g.check_sha,"a/b"))
    def test_wrong_canonical_state_or_duplicate_json(self):
        data=canonical(self.key,State(self.g))
        self.assertEqual(decode(self.key,data),State(self.g))
        with self.assertRaises(ValueError):decode(self.key,data.replace(b'"version":1',b'"version":1,"version":1'))
        with self.assertRaises(ValueError):canonical(self.key,replace(State(self.g),phase="inflight"))
