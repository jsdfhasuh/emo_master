"""Identity binding for the existing product Source CI event shapes.

Call only from a trusted controller, with independently observed workflow run,
event and live refs. The event object must not come from an untrusted artifact.
No I/O or workflow activation is performed here.
"""
import re

PATH = ".github/workflows/ci.yml"
MAIN = "refs/heads/main"
REPO_ID = 1343796337


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) and
            value != "0" * 40, "missing GitHub SHA")
    return value


def ident(value):
    require(type(value) is int and value > 0, "invalid GitHub ID")
    return value


def build(*, event, ref, event_sha, payload, run, run_id, attempt, workflow_id,
          approved_workflow_blob, observed_workflow_blob, current_main,
          read_pull=None, read_merge=None):
    """Normalize check/execution SHA; do not infer an Actions verdict."""
    event_sha = sha(event_sha)
    run_id, attempt, workflow_id = map(ident, (run_id, attempt, workflow_id))
    require(sha(observed_workflow_blob) == sha(approved_workflow_blob), "wrong workflow blob")
    expected_run = dict(id=run_id, run_attempt=attempt, workflow_id=workflow_id,
                        path=PATH, event=event)
    require(all(type(run.get(k)) is type(v) and run[k] == v for k, v in expected_run.items())
            and run.get("repository", {}).get("id") == REPO_ID
            and run.get("head_repository", {}).get("id") == REPO_ID
            and payload.get("repository", {}).get("id") == REPO_ID, "wrong workflow run provenance")
    tip = sha(current_main)
    base = merge = number = before = None
    if event in ("push", "workflow_dispatch"):
        require(ref == MAIN and run.get("head_branch") == "main"
                and run.get("head_sha") == event_sha == tip, "main event is stale or unbound")
        if event == "push":
            require(payload.get("ref") == MAIN and payload.get("deleted") is False
                    and sha(payload.get("after")) == event_sha, "wrong push")
            prev = payload.get("before")
            before = None if prev == "0" * 40 else sha(prev)
        else:
            require(payload.get("ref") in ("main", MAIN) and not payload.get("inputs"),
                    "unexpected Source CI manual input")
        head = execution = event_sha
    elif event == "pull_request":
        require(payload.get("action") in ("opened", "synchronize", "reopened"), "unhandled PR action")
        number = ident(payload.get("number"))
        require(ref == f"refs/pull/{number}/merge", "wrong PR ref")
        pr = payload["pull_request"]
        live = read_pull(number)
        def pair(row):
            head, base = row["head"], row["base"]
            require(row["number"] == number and row["state"] == "open"
                    and head["repo"]["id"] == base["repo"]["id"] == REPO_ID
                    and base["ref"] == "main", "unsupported PR provenance")
            return sha(head["sha"]), sha(base["sha"])
        head, base = pair(pr)
        require(pair(live) == (head, base) and base == tip, "PR changed")
        linked = run.get("pull_requests")
        require(isinstance(linked, list) and len(linked) == 1 and linked[0].get("number") == number
                and linked[0].get("head", {}).get("sha") == head
                and linked[0].get("base", {}).get("sha") == base
                and run.get("head_sha") == head, "run not bound to PR head/base")
        merge = sha(live.get("merge_commit_sha"))
        resolved_ref, parents = read_merge(number)
        require(live.get("mergeable") is True and merge == event_sha == resolved_ref
                and list(parents) == [base, head], "stale or invalid PR merge")
        execution = merge
    else:
        raise ValueError("unsupported Source CI event")
    return dict(source_event_name=event, source_ref=ref, source_repository_id=REPO_ID,
                source_head_sha=head, source_base_sha=base, source_merge_sha=merge,
                source_before_sha=before, source_pr_number=number, check_sha=execution,
                execution_sha=execution, request_id=f"{run_id}-{attempt}")
