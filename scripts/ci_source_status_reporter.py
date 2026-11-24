"""Opt-in, single-writer main-push source reporter. Not registered by this module.

The trusted caller injects authenticated GitHub GET/POST and artifact transport.
The encrypted source verifier must run in the trusted controller, not a worker.
GitHub commit status POST is append-only and offers no atomic CAS.
"""
from dataclasses import dataclass
import hashlib
import io
import json
import re
from typing import NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from zipfile import ZipFile

from scripts.ci_control_client import API, VERSION, NoRedirect
from scripts.ci_request import validate_request
from scripts.ci_shadow_verdict import RequestIdentity, decide

PRODUCT = "/repos/jsdfhasuh/emo_master"
PUBLIC = "/repos/jsdfhasuh/emo_master_ci"
PRODUCT_ID, PUBLIC_ID = 1343796337, 1468905732
PRODUCT_WORKFLOW = ".github/workflows/public-ci-dispatch.yml"
WORKER_WORKFLOW = ".github/workflows/product-ci-dispatch.yml"
MAIN = "refs/heads/main"
SHA = re.compile(r"[a-f0-9]{40}")
MAX = 256 * 1024
BLOB = re.compile(r"(?:[a-z0-9-]+\.)?(?:blob\.core\.windows\.net|actions\.githubusercontent\.com)")


class Withheld(ValueError):
    """No status was written unless the caller received an indeterminate POST."""


def need(ok, message):
    if not ok:
        raise Withheld(message)


def same(actual, expected):
    return all(type(actual.get(k)) is type(v) and actual[k] == v for k, v in expected.items())


def is_sha(value):
    return isinstance(value, str) and bool(SHA.fullmatch(value)) and value != "0" * 40


def is_id(value):
    return type(value) is int and 0 < value < 10**20


@dataclass(frozen=True)
class Pins:
    dispatcher_id: int
    dispatcher_blob: str
    control_commit: str
    worker_id: int
    worker_sha: str
    worker_tag: str
    context: str
    status_writer_id: int
    required_jobs: tuple[str, ...]


@dataclass(frozen=True)
class Selection:
    check_sha: str
    dispatch_id: int
    attempt: int
    request_artifact_id: int
    receipt_artifact_id: int


class Authority(NamedTuple):
    identity: RequestIdentity
    request_digest: str
    receipt_digest: str


def verified_json_artifact(get, download, *, artifact_id, run, attempt, prefix, member):
    metadata = get(f"{PRODUCT}/actions/artifacts/{artifact_id}")
    require_name = f"{prefix}-{run}-{attempt}"
    need(same(metadata, {"id": artifact_id, "name": require_name, "expired": False})
         and metadata.get("workflow_run", {}).get("id") == run, "artifact is outside the exact dispatcher attempt")
    size, digest = metadata.get("size_in_bytes"), metadata.get("digest")
    need(type(size) is int and 0 < size <= MAX and isinstance(digest, str)
         and re.fullmatch(r"sha256:[a-f0-9]{64}", digest), "unverifiable dispatcher artifact")
    data = download(artifact_id, size)
    need(isinstance(data, bytes) and len(data) == size and "sha256:" + hashlib.sha256(data).hexdigest() == digest,
         "dispatcher artifact digest mismatch")

    def distinct(pairs):
        result = dict(pairs)
        need(len(result) == len(pairs), "duplicate JSON key")
        return result

    with ZipFile(io.BytesIO(data)) as archive:
        records = archive.infolist()
        need(len(records) == 1 and records[0].filename == member and 0 < records[0].file_size <= 8192,
             "unexpected dispatcher artifact contents")
        with archive.open(records[0]) as stream:
            value = json.loads(stream.read(8193), object_pairs_hook=distinct)
    need(isinstance(value, dict), "expected dispatcher artifact object")
    return value, digest


def read_authority(get, download, selection, pins):
    sha, run_id, attempt, req_id, receipt_id = (selection.check_sha, selection.dispatch_id,
        selection.attempt, selection.request_artifact_id, selection.receipt_artifact_id)
    need(is_sha(sha) and all(is_id(x) for x in (run_id, attempt, req_id, receipt_id)) and req_id != receipt_id,
         "invalid exact source selection")
    need(all(is_id(x) for x in (pins.dispatcher_id, pins.worker_id))
         and all(is_sha(x) for x in (pins.dispatcher_blob, pins.control_commit, pins.worker_sha)),
         "invalid trusted workflow pin")
    ref = get(PRODUCT + "/git/ref/heads/main")
    need(ref.get("ref") == MAIN and same(ref.get("object", {}), {"type": "commit", "sha": sha}), "product main moved")
    run = get(f"{PRODUCT}/actions/runs/{run_id}")
    need(same(run, dict(id=run_id, workflow_id=pins.dispatcher_id, path=PRODUCT_WORKFLOW,
                       event="push", head_branch="main", head_sha=sha, run_attempt=attempt,
                       status="completed", conclusion="success"))
         and all(run.get(field, {}).get("id") == PRODUCT_ID for field in ("repository", "head_repository")),
         "wrong or incomplete dispatcher run")
    # An ambiguous second push run for the same SHA is not silently ordered by
    # client clock or guessed run ID. Select/reconcile explicitly outside here.
    fresh = get(f"{PRODUCT}/actions/workflows/{pins.dispatcher_id}/runs?event=push&branch=main&head_sha={sha}&per_page=2")
    rows = fresh.get("workflow_runs", [])
    need(fresh.get("total_count") == 1 and len(rows) == 1
         and same(rows[0], {"id": run_id, "run_attempt": attempt, "head_sha": sha}), "replayed or ambiguous dispatcher generation")
    definition = get(f"{PRODUCT}/contents/{PRODUCT_WORKFLOW}?ref={sha}")
    need(same(definition, {"path": PRODUCT_WORKFLOW, "type": "file", "sha": pins.dispatcher_blob}),
         "unapproved dispatcher definition")

    request, request_digest = verified_json_artifact(get, download, artifact_id=req_id, run=run_id, attempt=attempt,
                                                     prefix="ci-request", member="request.json")
    receipt, receipt_digest = verified_json_artifact(get, download, artifact_id=receipt_id, run=run_id, attempt=attempt,
                                                     prefix="ci-dispatch-receipt", member="dispatch-receipt.json")
    generation = f"{run_id}-{attempt}"
    worker_run = receipt.get("public_run_id")
    need(is_id(worker_run), "missing exact worker run")
    target = f"https://github.com/jsdfhasuh/emo_master_ci/actions/runs/{worker_run}"
    need(same(receipt, dict(request_id=generation, product_repository_id=PRODUCT_ID,
                           product_dispatch_run_id=run_id, product_dispatch_attempt=attempt, product_workflow_sha=sha,
                           control_commit_sha=pins.control_commit, request_artifact_id=req_id,
                           request_artifact_sha256=request_digest.removeprefix("sha256:"), check_sha=sha,
                           source_event_name="push", public_repository_id=PUBLIC_ID, public_workflow_id=pins.worker_id,
                           public_workflow_sha=pins.worker_sha, public_run_id=worker_run, public_run_url=target)),
         "invalid or replayed dispatcher receipt")
    validate_request(request, dispatcher=dict(repository_id=PRODUCT_ID, workflow_id=pins.dispatcher_id,
        run_id=run_id, attempt=attempt, artifact_id=req_id, original_event="push", original_ref=MAIN),
        product_head=dict(head_sha=sha, check_sha=sha, ref=MAIN), approved_public_sha=pins.worker_sha,
        expected_dispatcher_workflow_id=pins.dispatcher_id, artifact_id=req_id)
    suites = request.get("suites")
    need(request.get("execution_sha") == sha and isinstance(suites, list) and "ci" in suites,
         "request did not select the exact source suite")
    return Authority(RequestIdentity(PUBLIC_ID, pins.worker_id, pins.worker_sha, worker_run, 1, sha, sha, generation),
                     request_digest, receipt_digest)


def worker_snapshot(get, identity, pins):
    run = get(f"{PUBLIC}/actions/runs/{identity.run_id}")
    need(same(run, dict(id=identity.run_id, workflow_id=pins.worker_id, head_sha=pins.worker_sha,
                       run_attempt=1, event="workflow_dispatch", path=WORKER_WORKFLOW, head_branch=pins.worker_tag))
         and all(run.get(field, {}).get("id") == PUBLIC_ID for field in ("repository", "head_repository")),
         "wrong worker workflow, SHA, or attempt")
    return {**run, "repository_id": PUBLIC_ID}


def read_marker(get, sha, context, writer):
    # Combined status response retains newest record per context. Fail closed
    # if the dedicated context could be below an unconsumed page.
    rows = get(f"{PRODUCT}/commits/{sha}/status?per_page=100")
    statuses = rows.get("statuses")
    need(rows.get("sha") == sha and isinstance(statuses, list) and
         type(rows.get("total_count")) is int and rows["total_count"] <= 100
         and rows["total_count"] == len(statuses), "could not inspect current status context")
    records = [row for row in statuses if isinstance(row.get("context"), str)
               and row["context"].casefold() == context.casefold()]
    need(len(records) <= 1, "ambiguous current context")
    if records:
        need(records[0].get("context") == context and records[0].get("creator", {}).get("id") == writer
             and is_id(records[0].get("id")), "status context is owned by another writer")
    return records[0] if records else None


def marker(identity, context, state):
    return dict(context=context, state=state,
                target_url=f"https://github.com/jsdfhasuh/emo_master_ci/actions/runs/{identity.run_id}",
                description=f"Source CI request {identity.generation}: {state}")


def report(*, get, post, download, read_jobs, collect_verified_source, selection, pins, phase, expired=False):
    """Publish one state transition; no public-run retry or check selection by artifact.

    `collect_verified_source(identity, jobs)` returns (per-job proofs, partition_verified)
    only from trusted authenticated download and decrypted manifest checks.
    Serialize all invocations on the one context and check SHA before enabling.
    """
    need(phase in ("pending", "terminal") and isinstance(pins.context, str)
         and re.fullmatch(r"[A-Za-z0-9/_. -]{1,80}", pins.context)
         and isinstance(pins.worker_tag, str) and re.fullmatch(r"ci-worker-[A-Za-z0-9._-]{1,90}", pins.worker_tag)
         and is_id(pins.status_writer_id) and len(set(pins.required_jobs)) == len(pins.required_jobs) == 4, "invalid source status policy")
    authority = read_authority(get, download, selection, pins)
    identity = authority.identity
    run = worker_snapshot(get, identity, pins)
    state = "pending"
    if phase == "terminal":
        jobs, proofs, partition = (), (), False
        if run.get("status") == "completed" and run.get("conclusion") == "success":
            jobs = tuple(read_jobs(identity.run_id))
            proofs, partition = collect_verified_source(identity, jobs)
        state = decide(identity, current=dict(source_sha=identity.source_sha, check_sha=identity.check_sha,
                            generation=identity.generation), run=run, jobs=jobs, artifacts=proofs,
                       required_jobs=pins.required_jobs, expected_event="workflow_dispatch",
                       expired=expired, partition_verified=partition is True)
        if state == "pending":
            return "wait"
        need(state in ("success", "failure", "error"), "withholding unverified result")
    wanted = marker(identity, pins.context, state)
    previous = read_marker(get, identity.check_sha, pins.context, pins.status_writer_id)
    if previous and same(previous, wanted):
        return "unchanged"
    if phase == "terminal":
        need(previous and same(previous, marker(identity, pins.context, "pending")), "no current pending marker")
    else:
        need(previous is None, "status belongs to a terminal or different generation")

    # Re-read the authority and then the exact prior context immediately before
    # posting. This is not an atomic compare-and-set with GitHub.
    need(read_authority(get, download, selection, pins) == authority, "dispatcher changed before status")
    latest_worker = worker_snapshot(get, identity, pins)
    if phase == "terminal":
        need((latest_worker.get("status"), latest_worker.get("conclusion")) ==
             (run.get("status"), run.get("conclusion")), "worker changed before status")
    need(read_marker(get, identity.check_sha, pins.context, pins.status_writer_id) == previous, "status context changed before write")
    written = post(f"{PRODUCT}/statuses/{identity.check_sha}", wanted)
    need(same(written, wanted) and is_id(written.get("id"))
         and written.get("creator", {}).get("id") == pins.status_writer_id, "status POST receipt not verified; do not blindly retry")
    latest = read_marker(get, identity.check_sha, pins.context, pins.status_writer_id)
    need(latest is not None and same(latest, {**wanted, "id": written["id"]}),
         "status readback raced or failed; do not blindly retry")
    return state



def download_dispatcher_artifact(artifact_id, size, authorization, *, open_url=None):
    """Use existing controller auth only at the fixed GitHub artifact endpoint."""
    need(is_id(artifact_id) and type(size) is int and 0 < size <= MAX
         and isinstance(authorization, str) and re.fullmatch(r"Bearer [A-Za-z0-9_]+", authorization),
         "invalid exact artifact fetch parameters")
    if open_url is None:
        open_url = build_opener(NoRedirect()).open
    endpoint = API + PRODUCT + f"/actions/artifacts/{artifact_id}/zip"
    request = Request(endpoint, headers={"Authorization": authorization,
                       "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": VERSION})
    try:
        try:
            with open_url(request, timeout=20):
                raise Withheld("expected a GitHub artifact redirect")
        except HTTPError as error:
            try:
                need(error.code == 302, "GitHub artifact endpoint rejected")
                address = urlsplit(error.headers.get("Location", ""))
            finally:
                error.close()
        need(address.scheme == "https" and address.hostname and BLOB.fullmatch(address.hostname)
             and address.username is None and address.password is None and address.port in (None, 443),
             "unapproved artifact redirect")
        # No auth headers, and NoRedirect does not follow a second redirect.
        with open_url(Request(address.geturl()), timeout=20) as reply:
            need(reply.status == 200, "could not read dispatcher archive")
            return reply.read(size + 1)
    except (OSError, URLError, ValueError) as error:
        if isinstance(error, Withheld):
            raise
        raise Withheld("dispatcher archive fetch unavailable") from None


def report_github(client, *, verified_source_collector, selection, pins, phase, expired=False):
    """Use the #23 authenticated client in a trusted, independently serialized caller."""
    need((client.repository_id, client.workflow_id, client.worker_sha) ==
         (PUBLIC_ID, pins.worker_id, pins.worker_sha) and pins.context in client.contexts,
         "trusted client configuration does not match source reporter pins")
    need(client._request("/user").get("id") == pins.status_writer_id, "wrong authenticated status writer")
    return report(get=client._request,
                  post=lambda path, value: client._request(path, method="POST", payload=value, expected=201),
                  download=lambda artifact_id, size: download_dispatcher_artifact(
                      artifact_id, size, client._authorization),
                  read_jobs=client._read_jobs, collect_verified_source=verified_source_collector,
                  selection=selection, pins=pins, phase=phase, expired=expired)
