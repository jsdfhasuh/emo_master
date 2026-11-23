"""Strict GitHub CI control plane. Only trusted controllers should import this.

No public worker executes this client with a credential. No network calls are
retried implicitly: a timed-out dispatch may already have been accepted.
"""
from dataclasses import dataclass
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from scripts.ci_public_result import assess_public_result
from scripts.ci_request import validate_request
from scripts.ci_route import route_verified_event

API = "https://api.github.com"
PRODUCT = "/repos/jsdfhasuh/emo_master"
PUBLIC = "/repos/jsdfhasuh/emo_master_ci"
VERSION = "2026-03-10"
SHA = re.compile(r"[0-9a-f]{40}")
MAX_RESPONSE = 4 * 1024 * 1024


class ControlError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def github_transport(request):
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as reply:
            return reply.status, reply.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        error.close()
        raise ControlError(f"GitHub API returned HTTP {error.code}") from None
    except (URLError, TimeoutError, OSError):
        raise ControlError("GitHub API request did not complete") from None


def identifier(value):
    if type(value) is not int or value < 1:
        raise ControlError("invalid API identifier")
    return value


def sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value) or value == "0" * 40:
        raise ControlError("invalid commit SHA")
    return value


@dataclass(frozen=True)
class FreshRequest:
    request_id: str
    source_head: str
    check_sha: str
    source_base: str | None = None


class ControlClient:
    def __init__(self, token, *, public_repository_id, worker_workflow_id, worker_sha,
                 status_contexts, transport=github_transport):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_]+", token):
            raise ControlError("invalid authentication configuration")
        self._authorization = "Bearer " + token
        self.repository_id = identifier(public_repository_id)
        self.workflow_id = identifier(worker_workflow_id)
        self.worker_sha = sha(worker_sha)
        self.contexts = frozenset(status_contexts)
        if not self.contexts or any(not isinstance(x, str) or not re.fullmatch(r"[a-zA-Z0-9 /._-]{1,100}", x)
                                    for x in self.contexts):
            raise ControlError("invalid status contexts")
        self._transport = transport

    def _request(self, path, *, method="GET", payload=None, expected=200):
        request = Request(API + path, data=None if payload is None else json.dumps(payload).encode(),
                          method=method, headers={"Authorization": self._authorization,
                          "X-GitHub-Api-Version": VERSION, "Accept": "application/vnd.github+json",
                          "Content-Type": "application/json"})
        status, data = self._transport(request)
        if status != expected or len(data) > MAX_RESPONSE:
            raise ControlError("unexpected API response")
        try:
            value = json.loads(data)
        except (UnicodeError, ValueError):
            raise ControlError("invalid API response") from None
        if not isinstance(value, dict):
            raise ControlError("invalid API response")
        return value

    def plan(self, request, *, dispatcher, product_head, artifact_id, dispatcher_workflow_id,
             trigger_table, changed_paths=None, requested_suite=None):
        """Route verified read-only snapshots; derive suite/path via trusted reader."""
        validate_request(request, dispatcher=dispatcher, product_head=product_head,
                         approved_public_sha=self.worker_sha,
                         expected_dispatcher_workflow_id=dispatcher_workflow_id,
                         artifact_id=artifact_id)
        targets = route_verified_event(trigger_table, event=dispatcher["original_event"],
                                     ref=dispatcher["original_ref"], paths=changed_paths,
                                     requested=requested_suite)
        return FreshRequest(request["request_id"], product_head["head_sha"], product_head["check_sha"],
                            product_head.get("base_sha")), targets

    def dispatch(self, *, dispatcher_run_id, artifact_id, worker_tag):
        """Caller first validates source request and derives approved tag from config."""
        if not isinstance(worker_tag, str) or not re.fullmatch(r"ci-worker-[A-Za-z0-9._-]{1,90}", worker_tag):
            raise ControlError("invalid worker tag")
        ref = self._request(PUBLIC + "/git/ref/tags/" + quote(worker_tag, safe=""))
        target = ref.get("object")
        if not isinstance(target, dict) or target.get("type") != "commit" or target.get("sha") != self.worker_sha:
            raise ControlError("worker tag must be a pinned lightweight tag")
        payload = {"ref": worker_tag, "inputs": {
            "dispatcher_run_id": str(identifier(dispatcher_run_id)),
            "request_artifact_id": str(identifier(artifact_id)),
        }}
        result = self._request(f"{PUBLIC}/actions/workflows/{self.workflow_id}/dispatches",
                               method="POST", payload=payload)
        return identifier(result.get("workflow_run_id"))

    def _read_jobs(self, run_id):
        jobs = []
        for page in range(1, 21):
            raw = self._request(f"{PUBLIC}/actions/runs/{run_id}/attempts/1/jobs?per_page=100&page={page}")
            total = raw.get("total_count")
            records = raw.get("jobs")
            if (type(total) is not int or not 0 <= total <= 2000
                    or not isinstance(records, list) or len(records) > 100
                    or len(jobs) + len(records) > total):
                raise ControlError("invalid jobs listing")
            for row in records:
                if not isinstance(row, dict) or row.get("run_attempt", 1) != 1:
                    raise ControlError("invalid job record")
                jobs.append({**row, "run_attempt": 1})
            if len(jobs) == total:
                return jobs
            if len(records) != 100:
                raise ControlError("incomplete jobs listing")
        raise ControlError("incomplete jobs listing")

    def assess(self, *, run_id, required_jobs):
        run_id = identifier(run_id)
        raw = self._request(f"{PUBLIC}/actions/runs/{run_id}")
        repo = raw.get("repository")
        owner = raw.get("head_repository")
        if (not isinstance(repo, dict) or not isinstance(owner, dict)
                or repo.get("full_name") != "jsdfhasuh/emo_master_ci"
                or owner.get("id") != self.repository_id):
            return "failure"
        required = dict(run_id=run_id, repository_id=self.repository_id, workflow_id=self.workflow_id,
                        workflow_sha=self.worker_sha, run_attempt=1, required_jobs=required_jobs)
        normalized = {**raw, "repository_id": repo.get("id")}
        # First check identity before requesting jobs. A completed successful
        # run still cannot pass with an empty job set.
        if raw.get("status") != "completed" or raw.get("conclusion") != "success":
            return assess_public_result(actual=normalized, jobs=(), **required)
        return assess_public_result(actual=normalized, jobs=self._read_jobs(run_id), **required)

    def report(self, *, expected, read_current, context, required_jobs, run_id=None, verify_evidence=None):
        """Report pending before dispatch, or independently assess an exact run ID.

        read_current must use fresh trusted API data and bind the original ref/PR
        plus request generation. The trusted evidence verifier must require the
        pinned run and report digests before success. GET-to-POST is not atomic. Do not reuse a head-
        scoped check across different base snapshots without an independent gate.
        """
        if context not in self.contexts or not isinstance(expected, FreshRequest):
            raise ControlError("invalid status configuration")
        if not isinstance(expected.request_id, str) or not re.fullmatch(r"[1-9][0-9]*-[1-9][0-9]*", expected.request_id):
            raise ControlError("invalid request generation")
        sha(expected.source_head)
        sha(expected.check_sha)
        if expected.source_base is not None:
            sha(expected.source_base)
        state = "pending" if run_id is None else self.assess(run_id=run_id, required_jobs=required_jobs)
        if state == "success":
            if verify_evidence is None or verify_evidence(run_id, expected, context) is not True:
                state = "failure"
        if read_current() != expected:
            raise ControlError("stale request; status withheld")
        if run_id is not None and state == "pending":
            return state  # polling is read-only until terminal
        payload = {"state": state, "context": context, "description": {
            "pending": "Waiting for required CI jobs",
            "failure": "Required CI did not pass",
            "success": "Required CI passed",
        }[state]}
        if run_id is not None:
            payload["target_url"] = f"https://github.com/jsdfhasuh/emo_master_ci/actions/runs/{identifier(run_id)}"
        self._request(f"{PRODUCT}/statuses/{expected.check_sha}", method="POST", payload=payload, expected=201)
        return state
