"""Private immutable admission records for native event recovery.

The dispatch claim is keyed by worker + policy pins. Admission is keyed by
native origin instead so a change of policy cannot dispatch the same event a
second time. A new deployment must honor existing records or decline them.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from secrets import token_hex

from candidate_dispatch import dispatch_or_read_back, unique_pairs
from dispatch_once import ApprovedRequest
from github_state_store import StateStore
from trusted_route import Bound, PRODUCT, PUBLIC, ident, need, sha


@dataclass(frozen=True)
class Admission:
    bound: Bound
    request: ApprovedRequest
    admitted_at: int
    deadline: int


def identity(bound):
    ident(bound.run); ident(bound.attempt)
    need(type(bound.route) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,63}",bound.route),"bad native route")
    return (PRODUCT, bound.run, bound.attempt, bound.route)


def path(key):
    need(type(key) is tuple and len(key)==4 and type(key[0]) is int and key[0]==PRODUCT,"bad admission key")
    ident(key[1]); ident(key[2])
    need(type(key[3]) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", key[3]),"bad admission route")
    wire=json.dumps(key,ensure_ascii=True,separators=(",",":")).encode("ascii")
    return "ci-ledger/native/"+sha256(wire).hexdigest()+".json"


def encode(key, value):
    b, r=value.bound, value.request
    need(type(b) is Bound and type(r) is ApprovedRequest and identity(b)==key, "admission does not match origin")
    digest=r.identifier()
    body=json.loads(r.wire)
    need(b.worker_repository==PUBLIC and b.worker_workflow==r.workflow and b.worker_sha==r.worker_sha and
         b.worker_ref=="refs/tags/"+r.tag and
         (b.run,b.attempt,b.route,b.head,b.base,b.execute,b.pr)==tuple(body[k] for k in
            ("source_run","source_attempt","route","head","base","execution","pr")), "admission differs from request")
    need(type(b.inputs) is tuple and tuple(sorted((k,body[k]) for k in ("source_ref","packager_ref") if k in body))==
         b.inputs,"runtime overrides differ")
    need(type(b.contexts) is tuple and b.contexts and len(b.contexts)<=8 and len(set(b.contexts))==len(b.contexts)
         and all(type(c) is str and re.fullmatch(r"emo-ci-shadow/[a-z][a-z0-9-]{0,63}", c) for c in b.contexts),
         "unreviewed status namespace")
    sha(b.check); sha(b.execute)
    need(type(value.admitted_at) is int and type(value.deadline) is int and
         0<=value.admitted_at<value.deadline<=value.admitted_at+86400,"invalid immutable deadline")
    raw=(json.dumps({"version":1,"digest":digest,"value":asdict(value)},ensure_ascii=True,
                    separators=(",",":"),sort_keys=True)+"\n").encode("ascii")
    need(len(raw)<=8192,"admission too large")
    return raw


def decode(key, raw):
    need(type(raw) is bytes and 0<len(raw)<=8192, "bad admission bytes")
    row=json.loads(raw.decode("ascii"),object_pairs_hook=unique_pairs)
    v=row["value"]
    data=dict(v["bound"])
    data["contexts"]=tuple(data["contexts"]); data["inputs"]=tuple(tuple(x) for x in data["inputs"])
    result=Admission(Bound(**data),ApprovedRequest(**v["request"]),v["admitted_at"],v["deadline"])
    need(encode(key,result)==raw,"noncanonical admission")
    return result


class JournalStore(StateStore):
    _member=staticmethod(path)
    _encode=staticmethod(encode)
    _decode=staticmethod(decode)
    _limit=8192


def admit(journal, bound, request, *, now, deadline):
    """Store the first exact observation or re-read the same one. Never overwrite.

    An incomplete ref update gets one independent read, never an automatic
    second CAS or a dispatch until the full matching record is present.
    """
    key=identity(bound)
    proposal=Admission(bound,request,now,deadline)
    encode(key,proposal)
    base,row=journal.read(key)
    if row is None:
        if journal.cas(key,base,proposal,token_hex(24)):
            return proposal
        _,row=journal.read(key)
        need(row is not None,"native admission not confirmed")
    need(row.bound==bound and row.request==request,"native origin already admitted with different policy")
    return row


def admit_and_dispatch(gh, store, journal, bound, request, *, now, deadline):
    need(gh._authorized and gh._writer_id==request.actor, "protected preflight missing")
    frozen=admit(journal,bound,request,now=now,deadline=deadline)
    need(now<frozen.deadline, "already expired native admission")
    return frozen,dispatch_or_read_back(gh,store,frozen.bound,frozen.request)
