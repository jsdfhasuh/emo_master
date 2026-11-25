"""Decide Source gates from broker-authenticated Actions records, never public claims.

No credential, network, status publication, or required-ruleset wiring lives here.
The caller must get both envelope reads from ci_mainline_envelope.build() using
fresh GitHub metadata, and only construct VerifiedShard after decrypting V2 and
checking the archive/manifest identity and all file digests in the private broker.
"""
from dataclasses import dataclass
from itertools import product
from pathlib import Path

from scripts.ci_mainline_envelope import REPO_ID, require, sha, ident
from scripts.ci_shard_summary import summarize

PUBLIC = 1468905732
SLOTS = tuple(product(("linux", "windows"), ("runtime", "other")))
CONTEXTS = {"linux": "ubuntu-latest / Python 3.10", "windows": "windows-latest / Python 3.10"}
NEGATIVE = {"failure", "cancelled", "timed_out", "action_required", "skipped",
            "startup_failure", "stale", "neutral"}
PENDING = {"queued", "requested", "waiting", "pending", "in_progress"}
FIELDS = ("source_event_name", "source_ref", "source_repository_id", "source_head_sha",
          "source_base_sha", "source_merge_sha", "source_before_sha", "source_pr_number",
          "check_sha", "execution_sha", "request_id")


@dataclass(frozen=True)
class DispatchReceipt:
    request_id: str
    check_sha: str
    execution_sha: str
    worker_run_id: int
    worker_workflow_id: int
    worker_sha: str
    worker_tag: str


@dataclass(frozen=True)
class VerifiedShard:
    run_id: int
    attempt: int
    job_id: int
    archive_id: int
    execution_sha: str
    worker_sha: str
    platform: str
    shard: str
    report_path: Path


def strict(row, expected):
    return all(type(row.get(key)) is type(value) and row[key] == value for key, value in expected.items())


def decide(*, dispatched, current, receipt, run, jobs, evidence, approved_workflow_id,
           approved_worker_sha, approved_worker_tag, expected_names):
    """Return immutable check SHA and legacy context outcomes; stale is not red.

    This conservative source-only stage requires full shards; it does not
    silently accept scope skips. Workflow pins and exact hosted job names are
    configured by trusted control code, not by request evidence.
    """
    require(all(key in dispatched and key in current for key in FIELDS)
            and strict(current, {key: dispatched[key] for key in FIELDS})
            and current["source_repository_id"] == REPO_ID, "stale or incomplete product request")
    check, execution = sha(current["check_sha"]), sha(current["execution_sha"])
    require(check == execution and receipt.check_sha == check and receipt.execution_sha == execution
            and receipt.request_id == current["request_id"], "dispatch receipt does not bind this check")
    require(ident(receipt.worker_workflow_id) == ident(approved_workflow_id)
            and sha(receipt.worker_sha) == sha(approved_worker_sha)
            and receipt.worker_tag == approved_worker_tag
            and isinstance(approved_worker_tag, str) and approved_worker_tag.startswith("ci-worker-"),
            "dispatch receipt is outside approved worker pins")
    run_id = ident(receipt.worker_run_id)
    require(strict(run, dict(id=run_id, workflow_id=approved_workflow_id, head_sha=approved_worker_sha,
                             head_branch=approved_worker_tag, event="workflow_dispatch", run_attempt=1))
            and all(strict(run.get(key, {}), {"id": PUBLIC}) for key in ("repository", "head_repository")),
            "worker identity, workflow version or attempt changed")
    require(set(expected_names) == set(SLOTS) and len(set(expected_names.values())) == 4
            and all(isinstance(name, str) and name for name in expected_names.values()), "invalid required job inventory")
    status, conclusion = run.get("status"), run.get("conclusion")
    if status in PENDING:
        require(conclusion is None, "nonterminal run claims a conclusion")
        return check, {context: "pending" for context in CONTEXTS.values()}
    require(status == "completed" and conclusion in NEGATIVE | {"success"}, "unknown worker conclusion")

    by_name, artifact_ids, proofs = {}, set(), {}
    for row in jobs:
        require(strict(row, {"run_id": run_id, "run_attempt": 1}), "mixed or replayed job run/attempt")
        name = row.get("name")
        if name in expected_names.values():
            require(name not in by_name and row.get("status") == "completed", "duplicate or incomplete source job")
            ident(row.get("id"))
            by_name[name] = row
    require(set(by_name) == set(expected_names.values())
            and len({row["id"] for row in by_name.values()}) == 4, "missing or duplicated required source job")
    for item in evidence:
        slot = (item.platform, item.shard)
        require(slot in expected_names and slot not in proofs and ident(item.archive_id) not in artifact_ids
                and item.run_id == run_id and item.attempt == 1 and item.execution_sha == execution
                and item.worker_sha == approved_worker_sha
                and item.job_id == by_name[expected_names[slot]]["id"], "invalid or replayed V2 shard proof")
        proofs[slot] = item.report_path
        artifact_ids.add(item.archive_id)

    outcomes = {}
    for platform, context in CONTEXTS.items():
        slots = ((platform, "runtime"), (platform, "other"))
        conclusions = [by_name[expected_names[slot]].get("conclusion") for slot in slots]
        require(all(value in NEGATIVE | {"success"} for value in conclusions), "unknown source job conclusion")
        if any(value != "success" for value in conclusions):
            outcomes[context] = "failure"
            continue
        require(all(slot in proofs for slot in slots), "required V2 source shard proof missing")
        # The original product check, unchanged: same collection, disjoint
        # selected IDs, full union, each selected ID executed.
        summarize(*(proofs[slot] for slot in slots))
        outcomes[context] = "success"
    require(conclusion == "success" or "failure" in outcomes.values(), "outer failure not explained by source jobs")
    require(conclusion != "success" or "failure" not in outcomes.values(), "outer success contradicts source job failure")
    return check, outcomes


SHADOW_CONTEXTS = {"linux": "dot/source-v2-shadow/linux", "windows": "dot/source-v2-shadow/windows"}


def write_shadow(*, read_decision, read_status, post_status, receipt, platform, stage, writer_id):
    """Place one non-required shadow marker, with compare/write/readback.

    read_decision must freshly re-bind product refs and worker identity and
    return decide()'s value. read_status must read the latest exact context
    from the product API. The broker must serialize all callers for this SHA
    and context. GitHub statuses do not expose an atomic compare-and-set.
    """
    require(platform in SHADOW_CONTEXTS and stage in ("pending", "terminal"), "invalid shadow route")
    check = sha(receipt.check_sha)
    run = ident(receipt.worker_run_id)
    ident(writer_id)
    generation = receipt.request_id
    require(isinstance(generation, str) and generation.count("-") == 1, "invalid request generation")
    require(all(part.isascii() and part.isdecimal() and 0 < int(part) for part in generation.split("-")),
            "invalid request generation")
    context = SHADOW_CONTEXTS[platform]

    def marker(value):
        return dict(state=value, context=context, description=f"Source V2 {generation}: {value}",
                    target_url=f"https://github.com/jsdfhasuh/emo_master_ci/actions/runs/{run}")

    def owned(status, value):
        return status is not None and strict(status, marker(value)) and ident(status.get("id")) > 0 and strict(
            status.get("creator", {}), {"id": writer_id})

    def verified_decision():
        actual, states = read_decision()
        require(actual == check and set(states) == set(CONTEXTS.values())
                and states[CONTEXTS[platform]] in ("pending", "success", "failure"), "status target changed")
        return actual, states

    frozen = verified_decision()
    outcome = frozen[1][CONTEXTS[platform]]
    if stage == "terminal" and outcome == "pending":
        return "wait"
    value = "pending" if stage == "pending" else outcome
    previous = read_status(check, context)
    if owned(previous, value):
        return "unchanged"
    require(previous is None if stage == "pending" else owned(previous, "pending"),
            "status is missing, foreign, replayed, or already terminal")
    require(verified_decision() == frozen and read_status(check, context) == previous,
            "source result or status changed before write")
    result = post_status(check, marker(value))
    require(owned(result, value), "status receipt mismatch; reconcile before retry")
    latest = read_status(check, context)
    require(owned(latest, value) and latest["id"] == result["id"],
            "status readback mismatch; reconcile before retry")
    return value
