"""Evaluate terminal job conclusions from a trusted, fully paginated API read.

Normalize each API job to name, run_id, run_attempt, status, and conclusion.
If an API response does not include attempt on each job, obtain jobs from the
attempt-specific endpoint and bind that verified attempt during normalization.
Never use step output, artifacts, or event payloads as job records.
"""

from collections.abc import Iterable, Mapping


def verify_job_conclusions(
    *,
    expected_names: Iterable[str],
    run_id: int,
    run_attempt: int,
    jobs: Iterable[Mapping[str, object]],
) -> None:
    """Return normally on success, or raise ValueError on any mismatch."""
    if (type(run_id) is not int or run_id < 1
            or type(run_attempt) is not int or run_attempt < 1):
        raise ValueError("invalid run identity")
    if isinstance(expected_names, (str, bytes)):
        raise ValueError("expected names must be a collection")
    expected = tuple(expected_names)
    if not expected or any(not isinstance(name, str) or not name.strip() for name in expected):
        raise ValueError("invalid required job names")
    required = set(expected)
    if len(required) != len(expected):
        raise ValueError("duplicate required job name")

    seen: set[str] = set()
    for job in jobs:
        if not isinstance(job, Mapping):
            raise ValueError("invalid job record")
        actual_id, actual_attempt = job.get("run_id"), job.get("run_attempt")
        if (type(actual_id) is not int or actual_id != run_id
                or type(actual_attempt) is not int or actual_attempt != run_attempt):
            raise ValueError("job does not match the expected run attempt")
        name = job.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("invalid job name")
        if name not in required:
            continue
        if name in seen:
            raise ValueError("duplicate required job record")
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            raise ValueError("required job did not complete successfully")
        seen.add(name)
    if seen != required:
        raise ValueError("missing required job")
