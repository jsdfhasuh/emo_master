"""Source-only optional status activation; call only from the private entrypoint."""
from dataclasses import dataclass
from urllib.parse import quote
from time import sleep
from github_broker_transport import PRODUCT, PUBLIC, PRIVATE, LEDGER, IDS, ident, sha, need

SOURCE_CONTEXTS=("emo-ci-shadow/source",)

@dataclass(frozen=True)
class DeploymentPins:
    owner_id: int
    deployment_sha: str
    deployment_pr: int
    reviewed_head_sha: str
    runtime_workflow_id: int
    runtime_workflow_path: str
    environment: str
    worker_sha: str
    worker_tag: str
    worker_workflow_id: int
    worker_path: str


def enable_owner_source(gh, *, enabled=False, pins=None, runtime_run=None, runtime_attempt=None):
    """Pins originate from reviewed deployment configuration, never request input.
    Owner coordination is an operational control, not a GitHub-enforced writer identity.
    """
    gh._authorized=False
    gh._writer_id=None
    gh._contexts=frozenset()
    if enabled is not True:
        return 'dark'
    need(type(pins) is DeploymentPins,"missing checked deployment pins")
    get=lambda path: gh._request("GET",path)
    identity=ident(pins.owner_id)
    need(get("/user").get("id")==identity,"PAT actor not owner")
    for repo in (PRODUCT,PUBLIC,PRIVATE):
        metadata=gh.read_repo(repo)
        need(metadata.get("owner",{}).get("id")==identity and metadata["owner"].get("type")=="User",
             "coordinator is not the personal repository owner")
    for repo in (PRIVATE,PUBLIC):
        policy=gh.read_protection(repo,"main")
        for key, required in (("allow_force_pushes",False),("allow_deletions",False),("enforce_admins",True)):
            need((policy.get(key) or {}).get("enabled") is required,"unsafe branch policy")
    exact_rules(get,PRIVATE,"branch","refs/heads/"+LEDGER,{"non_fast_forward","deletion"})
    base=f"/repos/{PRIVATE}"
    need(get(base+"/branches/main").get("commit",{}).get("sha")==sha(pins.deployment_sha),"deployment pin is not current main")
    prpath=base+f"/pulls/{ident(pins.deployment_pr)}"
    # Push may arrive before PR state/merge actor is visible at the REST edge.
    for _ in range(13):
        pr=get(prpath)
        if (pr.get("merged") is True or (pr.get("state")=="closed" and isinstance(pr.get("merged_at"),str))) and pr.get("merge_commit_sha")==pins.deployment_sha and pr.get("merged_by",{}).get("id")==identity:
            break
        sleep(4)
    if pr.get("merged") is not True:
        gh._request("GET",prpath+"/merge",expected=204)
    if pr.get("merge_commit_sha")!=pins.deployment_sha:
        observed=pr.get("merge_commit_sha")
        try:
            observed=sha(observed)
        except ValueError:
            observed="absent"
        raise ValueError("reviewed PR merge SHA differs: expected "+pins.deployment_sha+" observed "+observed)
    need(pr.get("base",{}).get("ref")=="main","reviewed PR base branch differs")
    need(pr["base"].get("repo",{}).get("id")==IDS[PRIVATE],"reviewed PR base repository differs")
    # Explicit pin comes from independently reviewing the precise PR source.
    # The live GitHub merge actor records the personal owner's acceptance.
    need(pr.get("merged_by",{}).get("id")==identity and
         pr.get("head",{}).get("sha")==sha(pins.reviewed_head_sha),"owner merge or reviewed revision mismatch")
    run=get(base+f"/actions/runs/{ident(runtime_run)}/attempts/{ident(runtime_attempt)}")
    need(all(run.get(k)==v for k,v in dict(id=runtime_run,run_attempt=runtime_attempt,status="in_progress",
         head_sha=pins.deployment_sha,head_branch="main",workflow_id=ident(pins.runtime_workflow_id),path=pins.runtime_workflow_path).items())
         and run.get("repository",{}).get("id")==IDS[PRIVATE] and run.get("head_repository",{}).get("id")==IDS[PRIVATE]
         and run.get("event")=="push" and run.get("actor",{}).get("id")==identity and run.get("triggering_actor",{}).get("id")==identity, "not the pinned private deployment run")
    env_name=quote(pins.environment,safe="")
    env=get(base+"/environments/"+env_name)
    need(env.get("name")==pins.environment and env.get("deployment_branch_policy")=={
         "protected_branches":False,"custom_branch_policies":True},"environment must restrict to main")
    paths=get(base+"/environments/"+env_name+"/deployment-branch-policies?per_page=100")
    need(paths.get("total_count")==1 and [(p.get("name"),p.get("type")) for p in paths.get("branch_policies",[])]==[("main","branch")],
         "environment allows unexpected deployment refs")
    need(gh.worker_tag(pins.worker_tag)==sha(pins.worker_sha),"worker tag drift")
    exact_rules(get,PUBLIC,"tag","refs/tags/"+pins.worker_tag,{"update","deletion"})
    gh.read_workflow(pins.worker_workflow_id,pins.worker_path)
    need(pins.worker_path==".github/workflows/dispatch-source-candidate.yml","source worker only")
    gh._contexts=frozenset(SOURCE_CONTEXTS)
    gh._writer_id=identity
    gh._authorized=True
    return 'eligible_owner_coordinated_source_shadow_only'

def exact_rules(get, repo, target, ref, required):
    rules=get(f"/repos/{repo}/rulesets?includes_parents=true&per_page=100")
    need(type(rules) is list and 0<len(rules)<100,"rulesets unavailable")
    for candidate in rules:
        if candidate.get("target")!=target or candidate.get("enforcement")!="active":
            continue
        detail=get(f"/repos/{repo}/rulesets/{ident(candidate.get('id'))}")
        conditions=(detail.get("conditions") or {}).get("ref_name") or {}
        if (detail.get("enforcement")=="active" and detail.get("target")==target
            and detail.get("bypass_actors")==[] and conditions=={
                "include":[ref],"exclude":[]}
            and required == {r.get("type") for r in detail.get("rules",[])}):
            return
    need(False,"no matching active no-bypass ruleset")
