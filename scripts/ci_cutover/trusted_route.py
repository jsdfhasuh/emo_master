"""Trusted request ingress for a private CI broker; no network, dispatch or secrets."""
from dataclasses import asdict, dataclass
import re

PRODUCT = 1343796337
PUBLIC = 1468905732
MAX_AGE_SECONDS = 1800
SHA = re.compile(r"[0-9a-f]{40}")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
RELEASE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")


def need(ok, message):
    if not ok:
        raise ValueError(message)


def ident(value):
    need(type(value) is int and 0 < value < 2**63, "invalid identity")
    return value


def sha(value):
    need(isinstance(value, str) and SHA.fullmatch(value) and value != "0"*40, "invalid SHA")
    return value


def branch(value):
    need(isinstance(value, str) and BRANCH.fullmatch(value) and
         not any(x in value for x in ("..", "//", "@{")) and
         all(p and not p.startswith(".") and not p.endswith(".lock") for p in value.split("/")) and
         not value.endswith("."), "invalid branch")
    return value


def exact(row, values):
    return type(row) is dict and all(type(row.get(k)) is type(v) and row[k] == v for k,v in values.items())


@dataclass(frozen=True)
class Lane:
    key: str
    origin_path: str
    origin_workflow_id: int
    emitter_blob: str
    push_branches: tuple
    pr_bases: tuple
    manual_branches: tuple
    trusted_pr_heads: tuple
    worker_workflow_id: int
    worker_workflow_sha: str
    worker_ref: str
    status_contexts: tuple
    release_tags: bool = False
    # Exact reviewed blob for every helper participating in receipt generation.
    helper_blobs: tuple = ()
    # Optional manually requested input values are a private allowlist.
    manual_options: tuple = ()
    packager_sha: str | None = None
    # Nonempty only for lanes whose dedicated receipt job is reviewed.
    receipt_job_name: str | None = None


@dataclass(frozen=True)
class Bound:
    route: str
    run: int
    attempt: int
    event: str
    ref: str
    head: str
    base: str | None
    merge: str | None
    pr: int | None
    check: str
    execute: str
    worker_repository: int
    worker_workflow: int
    worker_sha: str
    worker_ref: str
    contexts: tuple
    inputs: tuple
    packager_sha: str | None

    def key(self):
        return f"{PRODUCT}/{self.run}/{self.attempt}/{self.route}"

    def record(self):
        return asdict(self)


def bind(lane, record, run, *, now, emitted, read_ref, read_pr, read_parents, read_blob, read_worker_ref, resolve_runtime_source=None, resolve_runtime_packager=None, receipt_job=None):
    """Inputs run/ref/PR/parents/blob are authenticated observations, never artifact fields.

    Route policy is loaded from reviewed private deployment, never from a request.
    If configured, receipt_job must come from a fresh authenticated GET under
    /actions/runs/<run>/attempts/<attempt>/jobs, not from the artifact.
    'emitted' is the API artifact creation timestamp translated to seconds; 'now'
    is the broker time. The caller must also verify outer artifact identity and ZIP digest.
    """
    ident(lane.origin_workflow_id); ident(lane.worker_workflow_id)
    need(re.fullmatch(r"[a-z][a-z0-9-]{0,63}",lane.key), "wrong route policy")
    need(re.fullmatch(r"\.github/workflows/[a-z0-9-]+\.ya?ml",lane.origin_path), "wrong emitter policy")
    need(re.fullmatch(r"refs/tags/ci-worker-[A-Za-z0-9._-]{1,100}",lane.worker_ref), "worker not pinned")
    need(type(now) is int and type(emitted) is int and 0 <= now-emitted <= MAX_AGE_SECONDS, "stale receipt")
    keys={"event","ref","head","base","merge","pr","run","attempt","repo","inputs"}
    need(type(record) is dict and set(record)==keys and exact(record,{"repo":PRODUCT}), "wrong request shape")
    rid,attempt=ident(record["run"]),ident(record["attempt"])
    event=record["event"]
    need(event in ("push","pull_request","workflow_dispatch"), "wrong event")
    need(exact(run,{"id":rid,"run_attempt":attempt,"workflow_id":lane.origin_workflow_id,
                    "path":lane.origin_path,"event":event})
         and exact(run.get("repository"),{"id":PRODUCT}) and exact(run.get("head_repository"),{"id":PRODUCT}),
         "wrong origin run")
    if lane.receipt_job_name is None:
        need(exact(run,{"status":"completed","conclusion":"success"}), "origin receipt run did not succeed")
    else:
        need(type(lane.receipt_job_name) is str and 1<=len(lane.receipt_job_name)<=128
             and run.get("status") in {"in_progress","completed"}
             and exact(receipt_job,{"run_id":rid,"run_attempt":attempt,"name":lane.receipt_job_name,
                                    "head_sha":run.get("head_sha"),"status":"completed","conclusion":"success"}),
             "exact receipt job did not succeed")
        ident(receipt_job.get("id"))
    options=record["inputs"]
    need(type(options) is dict and len(options)<=8 and
         all(type(k) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,63}",k)
             and type(v) is str and 0<len(v)<=256 for k,v in options.items()), "invalid action inputs")
    allowed_options=dict(lane.manual_options)
    need(not options or (event=="workflow_dispatch" and all(k in allowed_options and v in allowed_options[k]
         for k,v in options.items())), "unreviewed action input")
    ref,head=record["ref"],sha(record["head"])
    pr=base=merge=None
    if event=="pull_request":
        pr=ident(record["pr"]); base=sha(record["base"]); merge=sha(record["merge"])
        need(ref==f"refs/pull/{pr}/merge" and run.get("head_sha")==head, "wrong PR event")
        live=read_pr(pr)
        need(exact(live,{"number":pr,"state":"open","mergeable":True,"merge_commit_sha":merge}), "PR not current")
        hb,bb=live["head"],live["base"]
        need(exact(hb.get("repo"),{"id":PRODUCT}) and exact(bb.get("repo"),{"id":PRODUCT}), "external PR")
        hr,br=branch(hb["ref"]),branch(bb["ref"])
        need(hr in lane.trusted_pr_heads and br in lane.pr_bases and exact(hb,{"sha":head}) and exact(bb,{"sha":base})
             and run.get("head_branch")==hr, "unreviewed PR ref")
        linked=run.get("pull_requests")
        need(type(linked) is list and len(linked)==1 and exact(linked[0],{"number":pr}) and
             exact(linked[0].get("head"),{"sha":head}) and exact(linked[0].get("base"),{"sha":base}), "unbound Actions PR")
        need(read_ref("refs/heads/"+br)==base and read_ref("refs/heads/"+hr)==head and read_ref(ref)==merge and
             tuple(read_parents(merge))==(base,head), "merge identity changed")
        # Review actual merge tree's emitter, not claimed PR head source.
        execution=merge
    else:
        need(record["base"] is record["merge"] is record["pr"] is None and run.get("head_sha")==head, "wrong branch event")
        need(isinstance(ref,str),"invalid event ref")
        if ref.startswith("refs/heads/"):
            name=branch(ref.removeprefix("refs/heads/"))
            allowed=lane.push_branches if event=="push" else lane.manual_branches
            need(name in allowed and run.get("head_branch")==name, "unreviewed source ref")
        else:
            name=ref.removeprefix("refs/tags/")
            need(event=="push" and lane.release_tags and ref=="refs/tags/"+name and
                 RELEASE.fullmatch(name) and run.get("head_branch")==name, "unexpected release ref")
        need(read_ref(ref)==head,"event superseded")
        execution=head
    need(read_blob(execution,lane.origin_path)==sha(lane.emitter_blob), "emitter code not reviewed")
    need(all(isinstance(path,str) and re.fullmatch(r"[A-Za-z0-9_./-]{1,256}",path)
             and not any(p in ("",".","..") for p in path.split("/"))
             and read_blob(execution,path)==sha(digest) for path,digest in lane.helper_blobs), "emitter helper not reviewed")
    need(read_worker_ref(lane.worker_ref)==sha(lane.worker_workflow_sha), "worker ref changed")
    actual=execution
    packager=None
    if lane.key=="runtime-windows":
        need(event=="workflow_dispatch" and set(options)=={"source_ref","packager_ref"} and
             callable(resolve_runtime_source) and callable(resolve_runtime_packager), "runtime overrides not resolved")
        actual=sha(resolve_runtime_source(options["source_ref"]))
        packager=sha(resolve_runtime_packager(options["packager_ref"]))
        need(packager==sha(lane.packager_sha), "runtime packager is not pinned")
    else:
        need(not options,"unexpected overrides for route")
    return Bound(lane.key,rid,attempt,event,ref,head,base,merge,pr,execution,actual,
                 PUBLIC,lane.worker_workflow_id,lane.worker_workflow_sha,lane.worker_ref,lane.status_contexts,tuple(sorted(options.items())),packager)
