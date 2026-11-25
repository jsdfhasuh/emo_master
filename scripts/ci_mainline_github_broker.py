"""Isolated Source GitHub broker. No runner-facing credential or automatic activation."""
from collections.abc import Mapping
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from scripts.ci_mainline_envelope import ident, sha, require
from scripts.ci_mainline_source_result import DispatchReceipt, FIELDS, PUBLIC, SHADOW_CONTEXTS, decide, strict, write_shadow

API = "https://api.github.com"
PRODUCT_PATH = "/repos/jsdfhasuh/emo_master"
WORKER_PATH = "/repos/jsdfhasuh/emo_master_ci"
API_VERSION = "2026-03-10"
MAX_RESPONSE = 4 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


def request_api(request):
    try:
        with build_opener(NoRedirect()).open(request, timeout=25) as reply:
            return reply.status, reply.read(MAX_RESPONSE + 1)
    except HTTPError as exc:
        exc.close()
        raise ValueError(f"GitHub returned HTTP {exc.code}; do not blindly repeat writes") from None
    except (OSError, URLError):
        raise ValueError("GitHub call not confirmed; reconcile before repeating a write") from None


class SourceBroker:
    def __init__(self, token, *, workflow_id, worker_sha, worker_tag, worker_file, writer_id, transport=request_api):
        self.workflow_id, self.worker_sha, self.writer_id = ident(workflow_id), sha(worker_sha), ident(writer_id)
        require(isinstance(worker_tag, str) and re.fullmatch(r"ci-worker-[A-Za-z0-9._-]{1,90}", worker_tag),
                "unapproved worker tag")
        require(isinstance(worker_file, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,90}\.yml", worker_file),
                "unapproved worker workflow file")
        require(isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_]+", token), "missing broker credential")
        self.worker_tag, self.worker_file = worker_tag, worker_file
        self.workflow_path = ".github/workflows/" + worker_file
        self._auth, self._transport = "Bearer " + token, transport

    def _api(self, path, *, payload=None, expected=200):
        fixed = (PRODUCT_PATH, WORKER_PATH)
        require(path == "/user" or any(path.startswith(prefix + "/") or path == prefix for prefix in fixed),
                "unapproved broker endpoint")
        request = Request(API + path, data=None if payload is None else json.dumps(payload).encode(),
                          headers={"Authorization": self._auth, "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": API_VERSION, "Content-Type": "application/json"},
                          method="GET" if payload is None else "POST")
        status, raw = self._transport(request)
        require(status == expected and isinstance(raw, bytes) and len(raw) <= MAX_RESPONSE,
                "GitHub response not confirmed; do not blindly repeat writes")
        value = json.loads(raw)
        require(isinstance(value, dict), "expected GitHub API object")
        return value

    def _confirm_worker_pin(self):
        repo = self._api(WORKER_PATH)
        require(strict(repo, {"id": PUBLIC, "full_name": "jsdfhasuh/emo_master_ci"}), "wrong worker repository")
        tag = self._api(f"{WORKER_PATH}/git/ref/tags/{self.worker_tag}")
        require(tag.get("ref") == f"refs/tags/{self.worker_tag}"
                and strict(tag.get("object", {}), {"type": "commit", "sha": self.worker_sha}),
                "worker tag is not a pinned lightweight tag")
        workflow = self._api(f"{WORKER_PATH}/actions/workflows/{self.workflow_id}")
        require(strict(workflow, dict(id=self.workflow_id, path=self.workflow_path, state="active")),
                "worker workflow not registered and active")
        query = urlencode({"ref": self.worker_sha})
        file = self._api(f"{WORKER_PATH}/contents/{self.workflow_path}?{query}")
        require(strict(file, dict(type="file", path=self.workflow_path)), "worker pin does not contain the workflow")

    def dispatch(self, *, read_current, bound, artifact_id):
        """One POST. Caller must persist receipt; ambiguous POST is never retried here."""
        require(isinstance(bound, Mapping) and all(k in bound for k in FIELDS), "unbound product event")
        request_id = bound["request_id"]
        require(isinstance(request_id, str) and re.fullmatch(r"[1-9][0-9]{0,19}-[1-9][0-9]{0,5}", request_id),
                "invalid originating run attempt")
        dispatch_id = ident(int(request_id.split("-")[0]))
        check, execution = sha(bound["check_sha"]), sha(bound["execution_sha"])
        require(check == execution, "source execution differs from status target")
        artifact_id = ident(artifact_id)
        artifact = self._api(f"{PRODUCT_PATH}/actions/artifacts/{artifact_id}")
        require(strict(artifact, {"id": artifact_id, "name": f"ci-request-{request_id}", "expired": False})
                and strict(artifact.get("workflow_run", {}), {"id": dispatch_id})
                and isinstance(artifact.get("digest"), str)
                and re.fullmatch(r"sha256:[a-f0-9]{64}", artifact["digest"]),
                "request artifact is not bound to dispatcher run and attempt")
        self._confirm_worker_pin()
        require(strict(read_current(), {key: bound[key] for key in FIELDS}), "stale dispatch refused")
        result = self._api(f"{WORKER_PATH}/actions/workflows/{self.workflow_id}/dispatches",
                           payload={"ref": self.worker_tag, "inputs": {
                               "dispatcher_run_id": str(dispatch_id), "request_artifact_id": str(artifact_id)}})
        return DispatchReceipt(request_id, check, execution, ident(result.get("workflow_run_id")),
                               self.workflow_id, self.worker_sha, self.worker_tag)

    def _worker_run(self, receipt):
        run = self._api(f"{WORKER_PATH}/actions/runs/{ident(receipt.worker_run_id)}")
        require(run.get("path") == self.workflow_path, "unexpected public run workflow")
        return run

    def _jobs(self, run_id):
        prefix = f"{WORKER_PATH}/actions/runs/{ident(run_id)}/attempts/1/jobs"
        jobs = []
        for page in range(1, 21):
            result = self._api(f"{prefix}?per_page=100&page={page}")
            total, rows = result.get("total_count"), result.get("jobs")
            require(type(total) is int and 0 <= total <= 2000
                    and isinstance(rows, list) and len(rows) <= 100 and len(jobs)+len(rows) <= total,
                    "incomplete public jobs")
            for row in rows:
                require(isinstance(row, dict) and row.get("run_attempt", 1) == 1, "mixed public attempts")
                jobs.append({**row, "run_attempt": 1})
            if len(jobs) == total:
                return jobs
            require(len(rows) == 100, "truncated public jobs")
        raise ValueError("incomplete public jobs")

    def _status(self, check, context):
        require(context in SHADOW_CONTEXTS.values(), "not a non-required Source context")
        data = self._api(f"{PRODUCT_PATH}/commits/{sha(check)}/status?per_page=100")
        rows = data.get("statuses")
        require(data.get("sha") == check and isinstance(rows, list) and data.get("total_count") == len(rows)
                and len(rows) <= 100, "could not inspect status predecessor")
        matches = [row for row in rows if str(row.get("context", "")).casefold() == context.casefold()]
        require(len(matches) <= 1, "ambiguous current context")
        return matches[0] if matches else None

    def _post_shadow(self, check, payload):
        require(payload.get("context") in SHADOW_CONTEXTS.values(), "not a non-required Source context")
        return self._api(f"{PRODUCT_PATH}/statuses/{sha(check)}", payload=payload, expected=201)

    def report_shadow(self, *, dispatched, read_current, receipt, verified_evidence, expected_names, platform, stage):
        """Only a trusted serialized broker may call; verified_evidence is not worker JSON."""
        require(strict(self._api("/user"), {"id": self.writer_id}), "wrong authenticated writer")

        def snapshot():
            run = self._worker_run(receipt)
            jobs = self._jobs(receipt.worker_run_id) if run.get("status") == "completed" else ()
            evidence = verified_evidence(receipt, jobs) if stage == "terminal" and jobs else ()
            if stage == "pending":
                # Initial marker does not require decrypting when the short
                # dispatch receipt finishes shortly after the public worker.
                run = {**run, "status": "in_progress", "conclusion": None}
            return decide(dispatched=dispatched, current=read_current(), receipt=receipt, run=run, jobs=jobs,
                          evidence=evidence, approved_workflow_id=self.workflow_id,
                          approved_worker_sha=self.worker_sha, approved_worker_tag=self.worker_tag,
                          expected_names=expected_names)

        return write_shadow(read_decision=snapshot, read_status=self._status, post_status=self._post_shadow,
                            receipt=receipt, platform=platform, stage=stage, writer_id=self.writer_id)
