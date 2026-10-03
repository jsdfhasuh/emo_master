"""Failure-only evidence for the three image-demand initial-result waits.

Sections are separate, best-effort observations, never an atomic pipeline view.
The existing job diagnostic is the explicit exception to the new block's rules:
it already samples registered owner stacks and process liveness. Keep its import
alias here so the opt-in SQL diagnostic's runtime_test_utils patch cannot add IO.
"""
from collections import deque
from contextlib import contextmanager
from ctypes import c_ulonglong
from itertools import islice
import json
from queue import Queue
from _thread import RLock
from types import SimpleNamespace

from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.jobs.models import JobRecord
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_master.apps.runtime.presentation.service import PresentationService
from emo_master.apps.runtime.presentation.store import ResultStore
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.core.presentation.results import ClosedResult, ResultIdentity
from emo_master.core.project.models import ProjectDocument, ProjectMetadata
from tests.runtime.runtime_test_utils import jobFailureDetails as _jobFailureDetails


MAX_OUTPUT_BYTES = 24 * 1024
PREFIX = "FIRST_RESULT_TIMEOUT "


def _fields(value, kind):
    if type(value) is not kind:
        raise ValueError("unknown object")
    return _dict(object.__getattribute__(value, "__dict__"))


def _dict(value):
    if type(value) is not dict:
        raise ValueError("unknown mapping")
    return value


def _scalar(value):
    if value is None or type(value) is bool:
        return value
    if type(value) is int and value.bit_length() <= 64:
        return value
    if type(value) is str:
        return value[:160]
    raise ValueError("unknown scalar")


def _sameId(value, expected):
    return type(value) is str and value == expected


def _section(lock, read):
    if type(lock) is not RLock or not lock.acquire(blocking=False):
        return {"unknown": "lock_unavailable"}
    try:
        return read()
    except Exception:
        return {"unknown": "unavailable_or_changed"}
    finally:
        lock.release()


def _result(value, runtimeId, jobId):
    if value is None:
        return None
    fields = _fields(value, ClosedResult)
    identity = _fields(fields["identity"], ResultIdentity)
    if any(not _sameId(identity[key], expected) for key, expected in
           (("runtimeInstanceId", runtimeId), ("jobId", jobId), ("resultScopeId", "root"))):
        raise ValueError("result identity mismatch")
    return {key: _scalar(identity[key]) for key in ("resultKey", "resultOrdinal")}


def firstResultFailureDetails(backend, session):
    details = {"diagnostics": "best-effort, non-atomic; unknown is not absence"}
    try:
        fixture = _fields(backend, SimpleNamespace)
        runtimeObject, ownerObject, projectObject = fixture["runtime"], fixture["presentation"], fixture["project"]
        runtime = _fields(runtimeObject, RuntimeService)
        owner = _fields(ownerObject, PresentationService)
        client = _fields(session, DisplaySession)
        repository = _fields(runtime["jobRepository"], JobRepository)
        jobs = _dict(repository["_jobs"])
        project = _fields(_fields(projectObject, ProjectDocument)["project"], ProjectMetadata)
        runtimeId, jobId, projectId = runtime["runtimeInstanceId"], fixture["jobId"], project["projectId"]
        if any(type(value) is not str or not 0 < len(value) <= 160 for value in (runtimeId, jobId, projectId)):
            raise ValueError("unknown identity")
        record = jobs.get(jobId)
        job = _fields(record, JobRecord)

        def sameIdentity():
            return (fixture.get("runtime") is runtimeObject and fixture.get("presentation") is ownerObject
                    and fixture.get("project") is projectObject and _sameId(fixture.get("jobId"), jobId)
                    and _sameId(project.get("projectId"), projectId)
                    and repository.get("_jobs") is jobs
                    and runtime.get("_presentationOwner") is ownerObject
                    and owner.get("runtime") is runtimeObject
                    and _sameId(owner.get("runtimeInstanceId"), runtimeId)
                    and _sameId(runtime.get("runtimeInstanceId"), runtimeId)
                    and jobs.get(jobId) is record and _sameId(job.get("jobId"), jobId)
                    and _sameId(job.get("projectId"), projectId) and _sameId(client.get("jobId"), jobId)
                    and all(_sameId(client.get(key), "") or _sameId(client.get(key), expected) for key, expected in
                            (("instanceId", runtimeId), ("expectedRuntimeInstanceId", runtimeId), ("projectId", projectId))))

        if not sameIdentity():
            raise ValueError("identity mismatch")
        details["identity"] = {"runtime": runtimeId, "project": projectId, "job": jobId, "scope": "root"}

        def producer():
            config = _dict(_dict(owner["jobs"])[jobId])
            identity = _dict(config["identity"])
            if not _sameId(identity.get("runtimeInstanceId"), runtimeId) or not _sameId(identity.get("jobId"), jobId):
                raise ValueError("capture identity mismatch")
            scopes, ordinals = config["scopeIds"], config["ordinals"]
            if (type(scopes) is not list or len(scopes) > 16
                    or any(type(scope) is not str for scope in scopes)
                    or type(ordinals) is not c_ulonglong * 16):
                raise ValueError("unknown capture layout")
            return {"root_ordinal": _scalar(ordinals[scopes.index("root")])}

        details["producer"] = _section(owner["lock"], producer)
        store = _fields(owner["store"], ResultStore)

        def server():
            address = (jobId, "root")
            value = {"cursor": _scalar(store["cursor"]),
                     "latest": _result(_dict(store["latest"]).get(address), runtimeId, jobId)}
            for key in ("high", "closedHigh", "expired"):
                value[key] = _scalar(_dict(store[key]).get(address, 0))
            opened, pending = _dict(store["open"]), _dict(owner["pending"])
            if len(opened) > 16 or len(pending) > 16:
                raise ValueError("open budget")
            counts = (len(opened), len(pending))
            rows = []
            for key, item in islice(opened.items(), 16):
                identity = _dict(_dict(item)["identity"])
                if not _sameId(identity.get("jobId"), jobId):
                    continue
                if (not _sameId(identity.get("runtimeInstanceId"), runtimeId)
                        or not _sameId(identity.get("resultScopeId"), "root")
                        or type(key) is not str or not 0 < len(key) <= 160):
                    raise ValueError("open identity mismatch")
                entry = _dict(pending[key])
                if not _sameId(entry.get("job"), jobId):
                    raise ValueError("pending identity mismatch")
                rows.append({"key": _scalar(key), "ordinal": _scalar(identity["resultOrdinal"]),
                             "has_seal": "seal" in entry, "export_count": len(_dict(entry["exports"]))})
                if len(rows) > 8:
                    raise ValueError("job open budget")
            if counts != (len(opened), len(pending)):
                raise ValueError("changed containers")
            value["open"] = rows
            return value

        details["server"] = _section(store["lock"], server)

        def consumer():
            value = {key: _scalar(client[key]) for key in
                     ("connection", "connectionDetail", "generation", "cursor", "_acceptedCursor")}
            value["limitations"] = ("stats are session cumulative; queued/scheduled may include older generations; "
                                    "received > 0 does not establish delivery of the current root result")
            value["stats"] = {key: _scalar(_dict(client["stats"])[key]) for key in
                              ("received", "dropped", "resets")}
            value["high"] = _scalar(_dict(client["high"]).get("root", 0))
            value["loading"] = _result(_dict(client["loading"]).get("root"), runtimeId, jobId)
            latest = _dict(client["latest"]).get("root")
            if latest is not None and (type(latest) is not tuple or len(latest) != 3):
                raise ValueError("unknown live result")
            value["latest"] = _result(latest[0] if latest is not None else None, runtimeId, jobId)
            queue = _fields(client["pending"], Queue)["queue"]
            errors, records = client["errors"], client["records"]
            if any(type(items) is not deque for items in (queue, errors, records)):
                raise ValueError("unknown queue")
            if len(queue) > 8 or len(errors) > 64 or len(records) > 128:
                raise ValueError("queue budget")
            counts = (len(errors), len(records))
            # Queue length is explicitly best effort: do not acquire its mutex.
            value["queued"] = len(queue)
            scheduled = client["_scheduled"]
            if type(scheduled) is not set:
                raise ValueError("unknown admissions")
            value["scheduled"] = len(scheduled)
            history = {"identity": "unverified session history; may predate current job/generation", "errors": []}
            for error in islice(reversed(errors), 8):
                if type(error) is not tuple or len(error) != 2:
                    raise ValueError("unknown error")
                history["errors"].append([_scalar(part) for part in error])
            history["records"] = [{key: _scalar(_dict(row)[key]) for key in
                                   ("key", "ordinal", "applied_to_live")}
                                  for row in islice(reversed(records), 8)]
            if counts != (len(errors), len(records)):
                raise ValueError("changed containers")
            value["history"] = history
            return value

        details["session"] = _section(client["lock"], consumer)
        if not sameIdentity():
            raise ValueError("identity changed")
        try:
            text = _jobFailureDetails(fixture["runtime"], jobId)
            if type(text) is not str or len(text) > 16384 or len(text.encode("utf-8")) > 16384:
                raise ValueError("job diagnostic budget")
            details["job_details"] = json.loads(text)
        except Exception:
            details["job_details"] = {"unknown": "job_diagnostic_unavailable"}
        if not sameIdentity():
            raise ValueError("identity changed")
    except Exception:
        details = {"diagnostics": details["diagnostics"], "unknown": "identity_or_owner_unavailable"}
    text = json.dumps(details, ensure_ascii=True, separators=(",", ":"))
    if len((PREFIX + text + "\n").encode("utf-8")) > MAX_OUTPUT_BYTES:
        return '{"unknown":"output_budget_exceeded"}'
    return text


@contextmanager
def firstResultFailureEvidence(backend, session):
    try:
        yield
    except AssertionError:
        try:
            print(PREFIX + firstResultFailureDetails(backend, session))
        except BaseException:
            pass  # Diagnostics must never replace the original failed wait.
        raise
