"""Optional shadow worker dispatch using an independently protected claim.

The active private broker must bind and authenticate the exact product envelope
and the checked-in worker policy before constructing an ApprovedRequest.
"""
from dataclasses import dataclass
import hashlib
import json
import re
from github_broker_transport import PUBLIC, IDS, UnknownWrite, need, ident, sha

TAG=re.compile(r"ci-worker-[A-Za-z0-9._-]{1,96}")


@dataclass(frozen=True)
class ApprovedRequest:
    workflow: int
    path: str
    tag: str
    worker_sha: str
    actor: int
    wire: str     # deterministic JSON with version, artifact, run, attempt, revision; never secret

    def identifier(self):
        ident(self.workflow); ident(self.actor); sha(self.worker_sha)
        need(type(self.tag) is str and TAG.fullmatch(self.tag),"wrong worker tag")
        need(type(self.path) is str and re.fullmatch(r"\.github/workflows/dispatch-[a-z0-9-]+\.yml",self.path),"wrong worker entry")
        need(type(self.wire) is str and self.wire.isascii() and 0<len(self.wire)<=4096,"bad request wire")
        obj=json.loads(self.wire)
        keys={"schema","source_run","source_attempt","artifact_id","artifact_digest","head","base","execution","pr","route"}
        need(type(obj) is dict and set(obj) in (keys,keys|{"source_ref","packager_ref"}),"unexpected request wire fields")
        need(obj["schema"]==1 and type(obj["schema"]) is int and all(ident(obj[k]) for k in
             ("source_run","source_attempt","artifact_id")),"invalid request origins")
        for k in ("head","execution"):sha(obj[k])
        need(type(obj["artifact_digest"]) is str and re.fullmatch(r"sha256:[0-9a-f]{64}",obj["artifact_digest"]),"bad receipt digest")
        need(type(obj["route"]) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,63}",obj["route"]),"bad route")
        if obj["pr"] is not None:
            ident(obj["pr"]);sha(obj["base"])
        else:
            need(obj["base"] is None,"unbound PR base")
        if "source_ref" in obj:
            for k in ("source_ref","packager_ref"):
                need(type(obj[k]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_.-]{0,199}",obj[k])
                     and ".." not in obj[k] and "//" not in obj[k],"bad override")
        # Reject duplicates and ambiguous serialization by requiring canonical bytes.
        need(self.wire==json.dumps(obj,sort_keys=True,ensure_ascii=True,separators=(",",":")),"noncanonical request wire")
        return hashlib.sha256(json.dumps([self.workflow,self.path,self.tag,self.worker_sha,self.actor,self.wire],
                          ensure_ascii=True,separators=(",",":")).encode("ascii")).hexdigest()


def send_once(gh, claim, request):
    """claim is a durable private unique reservation; no send without it.

    claim.reserve(key) writes an exclusive intent before POST. claim.record
    binds the independently authenticated run. The caller reconciles a held key
    by run title/actor/version and never sends again just because lookup is empty.
    """
    key=request.identifier()
    need(gh._authorized,"protected preflight did not pass")
    need(gh._writer_id==request.actor,"wrong configured sender")
    gh.read_workflow(request.workflow,request.path)
    need(gh.worker_tag(request.tag)==request.worker_sha,"worker tag changed")
    if not claim.reserve(key):
        return None
    # Recheck after claim; if this or networking fails, preserve the held key.
    need(gh.worker_tag(request.tag)==request.worker_sha,"worker tag changed after claim")
    body={"ref":request.tag,"inputs":{"request":request.wire,"receipt_key":key},"return_run_details":True}
    out=gh._request("POST",f"/repos/{PUBLIC}/actions/workflows/{request.workflow}/dispatches",body,expected=200)
    number=ident(out["workflow_run_id"])
    run=gh.read_run(PUBLIC,number)
    need(run.get("id")==number and match_run(run,request,key),"worker receipt differs; keep claim closed")
    claim.record(key,number)
    return number


def match_run(run,request,key):
    return (run.get("repository",{}).get("id")==IDS[PUBLIC] and
            run.get("actor",{}).get("id")==request.actor and run.get("head_sha")==request.worker_sha and
            run.get("head_branch")==request.tag and run.get("event")=="workflow_dispatch" and run.get("run_attempt")==1 and
            run.get("workflow_id")==request.workflow and run.get("display_title")==f"emo-ci-candidate-{key}")


def reconcile_run(gh,claim,request):
    """Recover a held reservation from bounded authenticated lookups. Never POST."""
    key=request.identifier()
    need(gh._authorized and gh._writer_id==request.actor and claim.held(key),"no approved held request")
    gh.read_workflow(request.workflow,request.path)
    rows=[]
    seen=set()
    for page in range(1,6):
        result=gh._request("GET",f"/repos/{PUBLIC}/actions/workflows/{request.workflow}/runs?"
            f"event=workflow_dispatch&head_sha={request.worker_sha}&per_page=100&page={page}")
        batch=result["workflow_runs"]
        need(type(batch) is list and len(batch)<=100,"invalid run enumeration")
        for r in batch:
            number=ident(r["id"])
            need(number not in seen,"ambiguous duplicated run listing")
            seen.add(number)
            if match_run(r,request,key):
                rows.append(number)
        if len(batch)<100:
            break
    else:
        raise ValueError("dispatch recovery window exhausted; preserve claim")
    if not rows:
        return None
    need(len(rows)==1,"multiple matching worker runs; preserve claim")
    number=rows[0]
    run=gh.read_run(PUBLIC,number)
    need(run.get("id")==number and match_run(run,request,key),"exact worker mismatch")
    claim.record(key,number)
    return number


class DispatchClaims:
    """Append-only dispatch reservation using the same protected CAS backend."""
    def __init__(self,store,check_sha,execution_sha):
        self.store,self.head,self.execution=store,sha(check_sha),sha(execution_sha)
    def slot(self,key):
        need(type(key) is str and re.fullmatch("[0-9a-f]{64}",key),"bad dispatch reservation key")
        return self.head,"emo-ci-shadow/dispatch/"+key
    def reserve(self,key):
        from durable_claims import State
        from terminal_policy import Generation
        from secrets import token_hex
        slot=self.slot(key)
        base,s=self.store.read(slot)
        if s is not None:
            return False
        g=Generation(key,self.head,self.execution,slot[1],1,None,None)
        return self.store.cas(slot,base,State(g,owner=key[:48]),token_hex(24))
    def held(self,key):
        _,s=self.store.read(self.slot(key))
        return s is not None and s.owner==key[:48] and s.generation.binding==key
    def record(self,key,number):
        from dataclasses import replace
        from secrets import token_hex
        slot=self.slot(key)
        base,s=self.store.read(slot)
        need(s is not None and s.owner==key[:48] and s.generation.binding==key,"no matching dispatch reservation")
        ident(number)
        if s.generation.worker_run==number and s.generation.attempt==1:
            return
        need(s.generation.worker_run is None,"different worker already associated")
        g=replace(s.generation,worker_run=number,attempt=1)
        need(self.store.cas(slot,base,replace(s,generation=g),token_hex(24)), "worker binding not confirmed")
