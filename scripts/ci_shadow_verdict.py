"""Fail-closed decision for the CI reporter; this code performs no API writes.

The caller must obtain run/jobs and the current product ref from authenticated
GitHub API requests, and obtain each EvidenceProof from the trusted decryptor
after verifying outer and inner digests. Job output or a workflow request is
never authority for either. A failed or stale case must not update required
checks; this implementation is for opt-in, non-required shadow statuses.
"""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Literal

from scripts.ci_result_gate import verify_job_conclusions

SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
GENERATION = re.compile(r"[1-9][0-9]{0,19}-[1-9][0-9]{0,5}")
PENDING = frozenset(("queued", "requested", "waiting", "pending", "in_progress"))
NEGATIVE = frozenset(("failure", "cancelled", "timed_out", "action_required", "stale", "startup_failure", "skipped"))
Verdict = Literal["pending", "success", "failure", "error", "stale"]


@dataclass(frozen=True)
class RequestIdentity:
    repository_id: int
    workflow_id: int
    workflow_sha: str
    run_id: int
    attempt: int
    source_sha: str
    check_sha: str
    generation: str


@dataclass(frozen=True)
class EvidenceProof:
    run_id: int
    attempt: int
    job_id: int
    artifact_id: int
    digest: str
    size: int
    source_sha: str
    workflow_sha: str


def _positive(x: object) -> bool:
    return type(x) is int and x > 0


def decide(identity: RequestIdentity, *,
           current: Mapping[str, object], run: Mapping[str, object] | None,
           jobs: Iterable[Mapping[str, object]], artifacts: Iterable[EvidenceProof],
           required_jobs: tuple[str, ...], expected_event: Literal["workflow_dispatch"],
           expired: bool, partition_verified: bool | None) -> Verdict:
    """Return a typed decision, never infer success from missing scope/report.

    `partition_verified=None` is only for routes without a source partition;
    the caller must derive the route and evidence requirements from trusted
    configuration, not from the submitted request.
    """
    if (not all(_positive(x) for x in (identity.repository_id, identity.workflow_id, identity.run_id, identity.attempt))
            or not all(isinstance(x, str) and SHA.fullmatch(x) and x != "0" * 40
                       for x in (identity.workflow_sha, identity.source_sha, identity.check_sha))
            or not isinstance(identity.generation, str) or not GENERATION.fullmatch(identity.generation)
            or expected_event != "workflow_dispatch" or not required_jobs
            or any(not isinstance(name, str) or not name.strip() for name in required_jobs)
            or len(set(required_jobs)) != len(required_jobs)):
        return "error"
    if not all(isinstance(current.get(k), str) for k in ("source_sha", "check_sha", "generation")):
        return "error"
    if any(current[k] != getattr(identity, k) for k in ("source_sha", "check_sha", "generation")):
        return "stale"
    if run is None:
        return "error" if expired else "pending"
    pinned = dict(repository_id=identity.repository_id, workflow_id=identity.workflow_id,
                  head_sha=identity.workflow_sha, id=identity.run_id, run_attempt=identity.attempt,
                  event=expected_event)
    if any(type(run.get(k)) is not type(v) or run[k] != v for k, v in pinned.items()):
        return "error"
    status, conclusion = run.get("status"), run.get("conclusion")
    if status in PENDING:
        return "error" if expired or conclusion is not None else "pending"
    if status != "completed":
        return "error"
    if conclusion in NEGATIVE:
        return "failure"
    if conclusion != "success":
        return "error"
    records = tuple(jobs)
    try:
        verify_job_conclusions(expected_names=required_jobs, run_id=identity.run_id,
                               run_attempt=identity.attempt, jobs=records)
    except (TypeError, ValueError):
        return "error"
    ids = [j.get("id") for j in records if j.get("name") in required_jobs]
    if not all(_positive(x) for x in ids) or len(set(ids)) != len(ids):
        return "error"
    accepted: set[int] = set()
    artifact_ids: set[int] = set()
    for proof in artifacts:
        if (not isinstance(proof, EvidenceProof)
                or not _positive(proof.job_id) or proof.job_id not in ids or proof.job_id in accepted
                or not _positive(proof.artifact_id) or proof.artifact_id in artifact_ids
                or not _positive(proof.size) or proof.size > 52 * 1024 * 1024
                or not isinstance(proof.digest, str) or not DIGEST.fullmatch(proof.digest)
                or not _positive(proof.run_id) or proof.run_id != identity.run_id
                or not _positive(proof.attempt) or proof.attempt != identity.attempt
                or proof.source_sha != identity.source_sha or proof.workflow_sha != identity.workflow_sha):
            return "error"
        accepted.add(proof.job_id)
        artifact_ids.add(proof.artifact_id)
    if accepted != set(ids) or (partition_verified is not None and partition_verified is not True):
        return "error"
    return "success"
