"""Protected Git Database CAS for the private status-claim ledger.

The fixed private deployment supplies an independently approved anchor and an
authorized GitHub transport; no entrypoint here enables a deployment.
"""
from dataclasses import asdict
import base64
import hashlib
import json
import re
from durable_claims import State, bind_key
from terminal_policy import Generation
from github_broker_transport import PRIVATE, LEDGER, sha, need, Rejected, UnknownWrite, Refused

P=f"/repos/{PRIVATE}"
REF=f"refs/heads/{LEDGER}"
MAX_STATE=4096
MAX_TAIL=512
NONCE=re.compile("[0-9a-f]{48}")


def canonical(key,state):
    need(bind_key(state.generation)==key,"invalid ledger key")
    need(state.phase in ("idle","inflight","uncertain","sealed","blocked"),"invalid ledger phase")
    need(type(state.tries) is int and 0<=state.tries<=5,"invalid attempt count")
    need(state.owner is None or type(state.owner) is str and NONCE.fullmatch(state.owner),"invalid owner")
    if state.decision is not None:
        need(type(state.decision) is tuple and len(state.decision)==2 and
            state.decision[0] in ("success","failure","error") and
            type(state.decision[1]) is str and re.fullmatch(r"[a-z][a-z_]{0,80}",state.decision[1]),
            "invalid frozen terminal")
    need(state.phase=="idle" or state.decision is not None,"missing frozen terminal")
    need(state.phase!="inflight" or (state.tries>0 and state.owner is not None),"inflight without owner")
    wire=(json.dumps({"version":1,"state":asdict(state)},ensure_ascii=True,separators=(",",":"),sort_keys=True)+"\n").encode("ascii")
    need(len(wire)<=MAX_STATE,"claim exceeds bound")
    return wire


def decode(key,raw):
    need(isinstance(raw,bytes) and 0<len(raw)<=MAX_STATE,"oversized claim")
    obj=json.loads(raw)
    need(type(obj) is dict and set(obj)=={"version","state"} and type(obj["version"]) is int and obj["version"]==1,
         "invalid claim envelope")
    item=obj["state"]
    g=Generation(**item["generation"])
    row=State(g,item["phase"],None if item["decision"] is None else tuple(item["decision"]),item["tries"],item["owner"])
    need(canonical(key,row)==raw,"claim not canonical")
    return row


def member(key):
    head,context=key
    sha(head)
    need(type(context) is str and re.fullmatch(r"emo-ci-shadow/[a-z0-9][a-z0-9/_-]{0,80}",context),"unapproved claims namespace")
    # No artifact/user path can reach the GitHub Contents or tree API.
    k=hashlib.sha256((head+"\n"+context).encode("ascii")).hexdigest()
    return f"ci-ledger/{k}.json"


class StateStore:
    def __init__(self,github,anchor):
        self.github=github
        self.anchor=sha(anchor)
    def _data(self,method,path,payload=None,expected=200):
        need(self.github._authorized,"protected preflight has not passed")
        return self.github._request(method,P+path,payload,expected=expected)
    def tip(self):
        row=self._data("GET","/git/ref/heads/"+LEDGER)
        need(row.get("ref")==REF and row["object"].get("type")=="commit","wrong ledger branch")
        return sha(row["object"]["sha"])
    def check_anchor(self,tip):
        if tip==self.anchor:
            return
        row=self._data("GET",f"/compare/{self.anchor}...{tip}?per_page=1")
        need(row.get("merge_base_commit",{}).get("sha")==self.anchor and row.get("status")=="ahead" and
            type(row.get("ahead_by")) is int and 0<row["ahead_by"]<=MAX_TAIL and row.get("behind_by")==0,
            "unapproved ledger ancestry or checkpoint")
    def read(self,key):
        tip=self.tip()
        self.check_anchor(tip)
        return self._read_at(key,tip)
    def _read_at(self,key,tip):
        path=member(key)
        try:
            row=self._data("GET",f"/contents/{path}?ref={tip}")
        except Rejected as e:
            if str(e)=="read HTTP 404":
                # 404 is absence only once authenticated root and checkpoint
                # content at this exact commit can be read and pinned.
                marker=self._data("GET",f"/contents/ci-ledger-anchor.txt?ref={tip}")
                need(marker.get("type")=="file" and marker.get("encoding")=="base64" and
                     base64.b64decode("".join(marker["content"].splitlines()),validate=True)==b"emo-ci-optional-ledger-v1\n",
                     "missing independently reviewed ledger marker")
                return tip,None
            raise
        need(row.get("path")==path and row.get("type")=="file" and row.get("encoding")=="base64" and
             0<row.get("size",0)<=MAX_STATE,"invalid claim member")
        data=base64.b64decode("".join(row["content"].splitlines()),validate=True)
        need(len(data)==row["size"],"claim size mismatch")
        return tip,decode(key,data)
    def cas(self,key,expected,new,nonce):
        expected=sha(expected)
        need(type(nonce) is str and NONCE.fullmatch(nonce),"candidate nonce refused")
        path=member(key)
        content=canonical(key,new)
        if self.tip()!=expected:
            return False
        self.check_anchor(expected)
        base=self._data("GET","/git/commits/"+expected)
        need(base.get("sha")==expected,"ledger base mismatch")
        created=self._data("POST","/git/trees",{"base_tree":sha(base["tree"]["sha"]),
                    "tree":[dict(path=path,mode="100644",type="blob",content=content.decode("ascii"))]},expected=201)
        tree=sha(created["sha"])
        candidate=self._data("POST","/git/commits",{"message":"emo-ci protected state "+nonce,
                       "tree":tree,"parents":[expected]},expected=201)
        commit=sha(candidate["sha"])
        need(commit!=expected and candidate.get("tree",{}).get("sha")==tree and
             [x.get("sha") for x in candidate.get("parents",[])]==[expected],"candidate is not an exact child")
        if self.tip()!=expected:
            return False
        try:
            self._data("PATCH","/git/refs/heads/"+LEDGER,{"sha":commit,"force":False})
        except Rejected as e:
            # 409 or 422 can be a competing fast-forward. No ownership.
            if str(e) in ("write rejected HTTP 409","write rejected HTTP 422"):
                return False
            raise
        except UnknownWrite:
            # A lost response never resends PATCH. Exact readback can still prove
            # this unique candidate landed before the caller enters another stage.
            pass
        # Treat even a malformed success response as unconfirmed until the same
        # independently fetched commit and bound slot prove actual adoption.
        try:
            return self._confirmed(key,expected,new,commit)
        except Refused as exc:
            # Retry only transient GET failures. Keep the same candidate SHA;
            # never re-create a candidate or repeat the ref update.
            if str(exc) not in ("read not confirmed","read HTTP 502","read HTTP 503","read HTTP 504"):
                raise
            return self._confirmed(key,expected,new,commit)

    def _confirmed(self,key,expected,new,commit):
        tip,observed=self.read(key)
        # Other slots share this ref. Follow only exact single-parent Git objects,
        # reading the bound slot at each immutable SHA (including ABA intermediates).
        # Bounded work; ambiguity never authorizes ownership or another POST.
        diagnostic=f"unconfirmed ledger CAS parent={expected} candidate={commit} observed={tip}"
        cursor=tip
        for _ in range(8):
            if cursor==commit or observed!=new:
                break
            obj=self._data("GET","/git/commits/"+cursor)
            parents=obj.get("parents",[])
            need(obj.get("sha")==cursor and len(parents)==1,diagnostic)
            cursor=sha(parents[0]["sha"])
            _,observed=self._read_at(key,cursor)
        need(cursor==commit and observed==new,diagnostic)
        return True
