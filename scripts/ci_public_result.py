"""Offline validation for a public worker returned by a trusted dispatch call.

The caller must persist the returned run ID, for the specific private request,
then fetch the public run and all pages of jobs from trusted API endpoints.
This module performs no network access or status writes.
"""
from collections.abc import Iterable, Mapping
from typing import Literal

from scripts.ci_result_gate import verify_job_conclusions


def assess_public_result(
    *,
    actual: Mapping[str, object],
    jobs: Iterable[Mapping[str, object]],
    run_id: int,
    repository_id: int,
    workflow_id: int,
    workflow_sha: str,
    run_attempt: int,
    required_jobs: tuple[str, ...],
) -> Literal["pending", "failure", "success"]:
    identity = {
        "id": run_id, "repository_id": repository_id, "workflow_id": workflow_id,
        "head_sha": workflow_sha, "run_attempt": run_attempt, "event": "workflow_dispatch",
    }
    for key, expected in identity.items():
        value = actual.get(key)
        if type(value) is not type(expected) or value != expected:
            return "failure"
    if run_attempt != 1 or any(type(x) is not int or x <= 0 for x in
                               (run_id, repository_id, workflow_id)):
        # Dispatch a fresh run for a new request or rerun; avoid mixed attempts
        # from selective job reruns.
        return "failure"
    status = actual.get("status")
    if status in ("queued", "in_progress", "waiting", "requested", "pending"):
        return "pending" if actual.get("conclusion") is None else "failure"
    if status != "completed" or actual.get("conclusion") != "success":
        return "failure"
    try:
        verify_job_conclusions(expected_names=required_jobs, run_id=run_id,
                               run_attempt=run_attempt, jobs=jobs)
    except (ValueError, TypeError):
        return "failure"
    return "success"
