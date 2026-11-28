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

    def test_confirm_only_untouched_slot_across_ref_advance(self):
        for aba in (False,True):
            with self.subTest(aba=aba):
                git=Git(); original=git._request
                first,last="6"*40,"7"*40
                def request(method,path,payload=None,expected=200):
                    if method=="GET" and path.startswith(P+"/git/commits/"):
                        target=path.rsplit("/",1)[1]
                        if target in (first,last):
                            return dict(sha=target,parents=[dict(sha=first if target==last else C)])
                    if method=="GET" and path.startswith(P+"/contents/ci-ledger/"):
                        target=path.split("?ref=")[-1]
                        saved=git.tip
                        try:
                            git.tip=target
                            return original(method,path,payload,expected)
                        finally: git.tip=saved
                    result=original(method,path,payload,expected)
                    if method=="PATCH":
                        git.known[first]=(canonical(self.key,replace(State(self.g),tries=1)) if aba else git.known[C])
                        git.known[last]=git.known[C];git.tip=last
                    return result
                git._request=request
                store=StateStore(git,A)
                if aba:
                    with self.assertRaisesRegex(ValueError,"unconfirmed ledger CAS"):
                        store.cas(self.key,A,State(self.g),"a"*48)
                else:
                    self.assertTrue(store.cas(self.key,A,State(self.g),"a"*48))

    def test_lost_patch_response_requires_actual_adoption_and_never_repatches(self):
        for landed in (False,True):
            with self.subTest(landed=landed):
                git=Git(); original=git._request
                def request(method,path,payload=None,expected=200):
                    if method=="PATCH":
                        original(method,path,payload,expected)
                        if not landed: git.tip=A
                        raise UnknownWrite("response lost")
                    return original(method,path,payload,expected)
                git._request=request
                store=StateStore(git,A)
                if landed:
                    self.assertTrue(store.cas(self.key,A,State(self.g),"a"*48))
                else:
                    with self.assertRaisesRegex(ValueError,"unconfirmed ledger CAS"):
                        store.cas(self.key,A,State(self.g),"a"*48)
                self.assertEqual(sum(method=="PATCH" for method,*_ in git.writes),1)
