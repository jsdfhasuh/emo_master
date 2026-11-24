"""Trusted one-shot pilot controller. Fake transport tests do not use GitHub."""
import argparse
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

API = "https://api.github.com"
PRODUCT = "/repos/jsdfhasuh/emo_master"
PUBLIC = "/repos/jsdfhasuh/emo_master_ci"
SEED = "966d6821357b0380d328ee0b1c86243826247cdd"
PRODUCT_ID = 1343796337
SEED_BRANCH = "dot/dispatch-channel-status-pilot-20261124"
WORKFLOW_PATH = ".github/workflows/dispatch-channel-pilot.yml"
BIND = "Bind exact product status target"
PROBE = "Return fixed synthetic result"
JOB = "Dispatch pilot result"
SHA = re.compile(r"[0-9a-f]{40}")
MAX_READ = 2 * 1024 * 1024


class PilotError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def network(request):
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as reply:
            return reply.status, reply.read(MAX_READ + 1)
    except HTTPError as error:
        error.close()
        raise PilotError(f"GitHub HTTP {error.code}") from None
    except (URLError, OSError):
        raise PilotError("GitHub request indeterminate; do not blindly repeat a POST") from None


def count_id(value):
    if type(value) is not int or value <= 0:
        raise PilotError("invalid GitHub ID")
    return value


class Pilot:
    def __init__(self, token, *, public_id, workflow_sha, workflow_blob, tag, seed_branch=SEED_BRANCH, transport=network):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_]+", token):
            raise PilotError("invalid credential configuration")
        if not isinstance(workflow_sha, str) or not SHA.fullmatch(workflow_sha) or workflow_sha == "0" * 40:
            raise PilotError("invalid workflow commit")
        if not isinstance(tag, str) or not re.fullmatch(r"ci-dispatch-pilot-[A-Za-z0-9._-]+", tag):
            raise PilotError("not a dedicated pilot tag")
        if seed_branch != SEED_BRANCH:
            raise PilotError("not a dedicated pilot source branch")
        if not isinstance(workflow_blob, str) or not SHA.fullmatch(workflow_blob) or workflow_blob == "0" * 40:
            raise PilotError("invalid approved workflow blob")
        self.workflow_blob = workflow_blob
        self._auth = "Bearer " + token
        self.public_id, self.workflow_sha = count_id(public_id), workflow_sha
        self.tag, self.seed_branch = tag, seed_branch
        self._transport = transport

    def call(self, path, *, payload=None, status=200):
        request = Request(API + path, data=None if payload is None else json.dumps(payload).encode(),
                          headers={"Authorization": self._auth, "X-GitHub-Api-Version": "2026-03-10",
                                   "Content-Type": "application/json", "Accept": "application/vnd.github+json"})
        code, data = self._transport(request)
        if code != status or len(data) > MAX_READ:
            raise PilotError("unexpected API response")
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise PilotError("invalid API response") from None

    def verify_seed(self):
        repo = self.call(PRODUCT)
        if repo.get("id") != PRODUCT_ID or repo.get("full_name") != "jsdfhasuh/emo_master":
            raise PilotError("wrong product repository")
        ref = self.call(PRODUCT + "/git/ref/heads/" + quote(self.seed_branch, safe="/"))
        obj = ref.get("object")
        if (ref.get("ref") != "refs/heads/" + self.seed_branch or not isinstance(obj, dict)
                or obj.get("sha") != SEED or obj.get("type") != "commit"):
            raise PilotError("product pilot seed moved")

    def verify_tag(self):
        ref = self.call(PUBLIC + "/git/ref/tags/" + quote(self.tag, safe=""))
        obj = ref.get("object", {})
        if ref.get("ref") != "refs/tags/" + self.tag or obj.get("type") != "commit" or obj.get("sha") != self.workflow_sha:
            raise PilotError("pilot tag is not pinned")

    def prepare(self):
        self.verify_seed()
        repo = self.call(PUBLIC)
        if repo.get("id") != self.public_id or repo.get("full_name") != "jsdfhasuh/emo_master_ci":
            raise PilotError("wrong public repository")
        self.verify_tag()
        if repo.get("default_branch") != "main":
            raise PilotError("unexpected public default branch")
        for ref in ("main", self.workflow_sha):
            file = self.call(f"{PUBLIC}/contents/{WORKFLOW_PATH}?ref={ref}")
            if (file.get("path") != WORKFLOW_PATH or file.get("type") != "file"
                    or file.get("sha") != self.workflow_blob):
                raise PilotError("workflow blob does not match reviewed version")
        work = self.call(PUBLIC + "/actions/workflows/" + WORKFLOW_PATH.rsplit("/", 1)[1])
        if work.get("path") != WORKFLOW_PATH or work.get("state") != "active":
            raise PilotError("pilot workflow is not registered on the default branch")
        return count_id(work.get("id"))

    def dispatch(self, workflow_id, probe):
        self.verify_tag()
        response = self.call(f"{PUBLIC}/actions/workflows/{workflow_id}/dispatches",
                             payload={"ref": self.tag, "inputs": {"probe": probe, "product_sha": SEED}})
        return count_id(response.get("workflow_run_id"))

    def poll(self, workflow_id, run_id, *, probe, deadline, clock=time.monotonic, sleep=time.sleep):
        outcome = "success" if probe == "pass" else "failure"
        while clock() < deadline:
            run = self.call(f"{PUBLIC}/actions/runs/{run_id}")
            identity = {"id": run_id, "workflow_id": workflow_id, "run_attempt": 1,
                        "event": "workflow_dispatch", "head_sha": self.workflow_sha}
            if (any(type(run.get(k)) is not type(v) or run.get(k) != v for k, v in identity.items())
                    or any(run.get(k, {}).get("id") != self.public_id for k in ("repository", "head_repository"))):
                raise PilotError("unexpected exact-run identity")
            if run.get("status") == "completed":
                if run.get("conclusion") != outcome:
                    raise PilotError("pilot run did not get its expected result")
                rows = self.call(f"{PUBLIC}/actions/runs/{run_id}/attempts/1/jobs?per_page=100")
                records = rows.get("jobs")
                if rows.get("total_count") != 1 or not isinstance(records, list) or len(records) != 1:
                    raise PilotError("expected exactly one pilot job")
                job = records[0]
                if job.get("run_id") != run_id or job.get("run_attempt", 1) != 1 or job.get("name") != JOB:
                    raise PilotError("wrong pilot job identity")
                if job.get("status") != "completed" or job.get("conclusion") != outcome:
                    raise PilotError("pilot job has unexpected result")
                for name, wanted in ((BIND, "success"), (PROBE, outcome)):
                    matches = [r for r in job.get("steps", ()) if r.get("name") == name]
                    if len(matches) != 1 or matches[0].get("status") != "completed" or matches[0].get("conclusion") != wanted:
                        raise PilotError("bound seed or exact probe step was not verified")
                return outcome
            if run.get("status") not in ("queued", "requested", "waiting", "pending", "in_progress"):
                raise PilotError("unknown pilot run state")
            sleep(5)
        raise PilotError("pilot polling deadline reached")

    def publish(self, run_id, probe, state):
        if (probe, state) not in (("pass", "success"), ("fail", "failure")):
            raise PilotError("wrong fixed probe status")
        self.verify_seed()  # GitHub has no atomic ref-check-and-set-status endpoint.
        payload = {"context": "dot/dispatch-pilot/" + probe, "state": state,
                   "description": "Verified non-required dispatch channel pilot",
                   "target_url": f"https://github.com/jsdfhasuh/emo_master_ci/actions/runs/{count_id(run_id)}"}
        row = self.call(f"{PRODUCT}/statuses/{SEED}", payload=payload, status=201)
        return self._verify_status(row, payload)

    def _verify_status(self, row, wanted):
        if any(row.get(k) != wanted[k] for k in ("context", "state", "target_url")):
            raise PilotError("product status did not match expected values")
        return {k: wanted[k] for k in ("context", "state", "target_url")}

    def execute(self, *, record=print, clock=time.monotonic, sleep=time.sleep):
        workflow_id = self.prepare()
        runs = {}
        for probe in ("pass", "fail"):
            runs[probe] = self.dispatch(workflow_id, probe)
            record(json.dumps({"probe": probe, "workflow_run_id": runs[probe], "attempt": 1}))
        if runs["pass"] == runs["fail"]:
            raise PilotError("dispatch responses returned the same ID")
        verdicts = {probe: self.poll(workflow_id, run_id, probe=probe, deadline=clock() + 600,
                                    clock=clock, sleep=sleep) for probe, run_id in runs.items()}
        statuses = [self.publish(run_id, probe, verdicts[probe]) for probe, run_id in runs.items()]
        rows = self.call(f"{PRODUCT}/commits/{SEED}/statuses?per_page=100")
        for wanted in statuses:
            actual = next((r for r in rows if r.get("context") == wanted["context"]), {})
            self._verify_status(actual, wanted)
        record(json.dumps({"seed": SEED, "statuses": statuses}, sort_keys=True))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--public-id", type=int, required=True)
    p.add_argument("--workflow-sha", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--workflow-blob", required=True)
    a = p.parse_args()
    try:
        Pilot(os.environ.get("EMO_CI_TOKEN"), public_id=a.public_id, workflow_sha=a.workflow_sha,
              tag=a.tag, workflow_blob=a.workflow_blob).execute(record=lambda s: print(s, flush=True))
    except PilotError as error:
        p.exit(1, f"Pilot not verified: {error}\n")


if __name__ == "__main__":
    main()
