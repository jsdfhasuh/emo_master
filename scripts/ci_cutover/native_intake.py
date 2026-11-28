"""Private Broker transport for native workflow receipts, including isolated deployments.

The trusted entry point supplies policy, a protected GitHub adapter, and event IDs.
No product code is imported; artifact contents are data, never execution input.
"""
from datetime import datetime, timezone
from time import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request

from candidate_dispatch import MAX_ZIP, prepare
from native_journal import admit_and_dispatch
from github_broker_transport import HOST, PRODUCT, Refused, ident, need, sha


def artifact_zip(gh, aid, size):
    """Take one exact GitHub API redirect, never sending the PAT to asset storage."""
    ident(aid)
    need(type(size) is int and 0 < size <= MAX_ZIP, "invalid archive size")
    source = f"{HOST}/repos/{PRODUCT}/actions/artifacts/{aid}/zip"
    try:
        with gh._http.open(Request(source, headers={
                "Authorization": "Bearer " + gh._token, "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}), timeout=15):
            raise Refused("artifact did not redirect")
    except HTTPError as err:
        with err:
            need(err.code == 302, "artifact redirect refused")
            location = err.headers.get("Location", "")
    except (URLError, OSError):
        raise Refused("artifact request unavailable") from None
    try:
        parts = urlsplit(location)
        need(parts.scheme == "https" and parts.port in (None, 443) and not parts.username
             and not parts.password and not parts.fragment and parts.hostname
             and parts.hostname.endswith((".blob.core.windows.net", ".actions.githubusercontent.com")),
             "unexpected artifact host")
        with gh._http.open(Request(location), timeout=15) as response:
            need(response.status == 200, "invalid artifact download")
            data = response.read(size + 1)
    except (URLError, OSError, ValueError):
        # Never allow an exception with a presigned URL in its message into CI logs.
        raise Refused("artifact download refused") from None
    need(len(data) == size, "artifact truncated or oversized")
    return data


def listed_unique(gh, path, field, accept):
    matches = []
    for page in range(1, 6):
        listing = gh._request("GET", f"{path}{'&' if '?' in path else '?'}per_page=50&page={page}")
        batch = listing[field]
        need(type(batch) is list and len(batch) <= 50, "invalid GitHub listing")
        matches.extend(row for row in batch if accept(row))
        need(len(matches) <= 1, "ambiguous native evidence")
        if len(batch) < 50:
            need(len(matches) == 1, "missing native evidence")
            return matches[0]
    raise Refused("native evidence listing incomplete")


def bind_native(gh, lane, worker_path, owner, rid, attempt, *, clock=time, fetch=artifact_zip):
    """Rebuild all observations from authenticated GitHub reads. No writes."""
    ident(rid); ident(attempt); ident(owner)
    root = f"/repos/{PRODUCT}"
    runpath = f"{root}/actions/runs/{rid}/attempts/{attempt}"
    latest = gh._request("GET", f"{root}/actions/runs/{rid}")
    run = gh._request("GET", runpath)
    need(latest.get("id") == rid and latest.get("run_attempt") == attempt and
         latest.get("head_sha") == run.get("head_sha"), "origin attempt superseded")
    need(run.get("actor", {}).get("id") == owner and run.get("triggering_actor", {}).get("id") == owner,
         "origin actor not permitted")
    need(run.get("workflow_id") == lane.origin_workflow_id and run.get("path") == lane.origin_path,
         "origin workflow differs")
    origin = f"{root}/actions/runs/{rid}"
    name = f"ci-request-{rid}-{attempt}"
    art = listed_unique(gh, f"{origin}/artifacts?name={name}", "artifacts", lambda item: item.get("name") == name)
    aid = ident(art["id"])
    art = gh._request("GET", f"{root}/actions/artifacts/{aid}")
    need(art.get("id") == aid, "artifact lookup returned another ID")
    need(art.get("name") == name and art.get("expired") is False, "origin artifact unavailable")
    stamp = datetime.fromisoformat(art["created_at"].replace("Z", "+00:00"))
    need(stamp.utcoffset() == timezone.utc.utcoffset(stamp), "timestamp not UTC")
    job = None
    if lane.receipt_job_name:
        job = listed_unique(gh, runpath + "/jobs", "jobs", lambda item: item.get("name") == lane.receipt_job_name)
    def ref(name):
        row = gh._request("GET", root + "/git/ref/" + quote(name.removeprefix("refs/"), safe="/"))
        need(row.get("ref") == name and row["object"].get("type") == "commit", "origin ref not exact")
        return sha(row["object"]["sha"])
    def parents(commit):
        row = gh._request("GET", root + "/git/commits/" + sha(commit))
        need(row.get("sha") == commit, "wrong merge commit")
        return [sha(p["sha"]) for p in row["parents"]]
    def blob(commit, path):
        row = gh._request("GET", root + "/contents/" + quote(path, safe="/") + "?ref=" + sha(commit))
        need(row.get("path") == path and row.get("type") == "file", "wrong emitter file")
        return sha(row["sha"])
    def resolve(repo, requested):
        row = gh._request("GET", f"/repos/{repo}/commits/" + quote(requested, safe=""))
        return sha(row["sha"])
    gh.read_workflow(lane.worker_workflow_id, worker_path)
    return prepare(lane, run, art, fetch(gh, art["id"], art["size_in_bytes"]),
        worker_path=worker_path, actor=owner, now=int(clock()), emitted=int(stamp.timestamp()),
        read_ref=ref, read_pr=lambda n: gh._request("GET", root + "/pulls/" + str(ident(n))),
        read_parents=parents, read_blob=blob, receipt_job=job,
        read_worker_ref=lambda name: gh.worker_tag(name.removeprefix("refs/tags/")),
        resolve_runtime_source=lambda requested: resolve(PRODUCT, requested),
        resolve_runtime_packager=lambda requested: resolve("jsdfhasuh/python_build_scripts", requested))


def accept_native(gh, store, journal, lane, worker_path, owner, rid, attempt, *, budget_seconds):
    """Call only from reviewed private deployment after real owner-coordination preflight."""
    need(gh._authorized and gh._writer_id == owner, "private preflight missing")
    bound, request = bind_native(gh, lane, worker_path, owner, rid, attempt)
    now=int(time())
    need(type(budget_seconds) is int and 0<budget_seconds<=86400, "bad private settlement budget")
    return admit_and_dispatch(gh, store, journal, bound, request, now=now, deadline=now+budget_seconds)
