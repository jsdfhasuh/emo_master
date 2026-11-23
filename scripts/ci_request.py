"""Validate the fixed provenance of a public CI request before any product I/O.

Both mappings passed here must be built by trusted control code using GitHub
API responses tied to the expected workflow and artifact, not workflow inputs
or a checkout from the submitted revision. This only validates the relation;
it does not acquire credentials, dispatch work, or write a status.
"""
from collections.abc import Mapping
import re


PRODUCT_REPOSITORY_ID = 1343796337
SHA = re.compile(r"[0-9a-f]{40}")


def _require_sha(value: object) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value) or set(value) == {"0"}:
        raise ValueError("a fixed SHA is required")
    return value


def _positive_int(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("a positive GitHub identifier is required")
    return value


def validate_request(
    request: Mapping[str, object], *, dispatcher: Mapping[str, object],
    product_head: Mapping[str, object], approved_public_sha: str,
    expected_dispatcher_workflow_id: int, artifact_id: int,
) -> None:
    """Reject stale or foreign requests, even if their submitted SHA is valid.

    dispatcher is verified API provenance with normalized repository_id,
    workflow_id, run_id, attempt, artifact_id, original_event, original_ref.
    product_head is a fresh independent product API snapshot with head_sha,
    check_sha, ref, and, for PRs, number, base_sha, and merge_sha. A caller
    unable to verify a ref/tag/PR must not call this with guessed metadata.
    """
    if request.get("schema_version") != 1:
        raise ValueError("unsupported request schema")
    if (type(request.get("source_repository_id")) is not int
            or request["source_repository_id"] != PRODUCT_REPOSITORY_ID
            or dispatcher.get("repository_id") != PRODUCT_REPOSITORY_ID):
        raise ValueError("wrong source repository")
    run = _positive_int(dispatcher.get("run_id"))
    attempt = _positive_int(dispatcher.get("attempt"))
    if (request.get("request_id") != f"{run}-{attempt}"
            or dispatcher.get("workflow_id") != _positive_int(expected_dispatcher_workflow_id)
            or dispatcher.get("artifact_id") != _positive_int(artifact_id)):
        raise ValueError("wrong dispatcher, run attempt, or artifact")
    if (_require_sha(request.get("ci_workflow_sha")) != _require_sha(approved_public_sha)
            or _require_sha(request.get("source_head_sha")) != _require_sha(product_head.get("head_sha"))
            or _require_sha(request.get("check_sha")) != _require_sha(product_head.get("check_sha"))):
        raise ValueError("wrong workflow, source head, or check SHA")
    event = request.get("source_event_name")
    ref = request.get("source_ref")
    if (event not in ("push", "pull_request", "workflow_dispatch")
            or event != dispatcher.get("original_event")
            or not isinstance(ref, str) or ref != dispatcher.get("original_ref")
            or ref != product_head.get("ref")):
        raise ValueError("wrong event or product ref")
    if event == "pull_request":
        if (_positive_int(request.get("source_pr_number")) != _positive_int(product_head.get("number"))
                or _require_sha(request.get("source_base_sha")) != _require_sha(product_head.get("base_sha"))
                or _require_sha(request.get("source_merge_sha")) != _require_sha(product_head.get("merge_sha"))
                or request["check_sha"] != request["source_merge_sha"]):
            raise ValueError("stale PR head/base/merge or wrong gate SHA")
