from __future__ import annotations

from threading import Event
import json
import time


TERMINAL_STATUSES = {"COMPLETED", "FAILED", "ABORTED"}


def jobFailureDetails(runtimeService, jobId: str, status=None, result=None) -> str:
    """Bounded, best-effort in-memory diagnostics; no new RPC, disk read or lock."""
    def small(value):
        return value if value is None or isinstance(value, (bool, int, float)) else str(value)[:256]

    details = {"job": jobId, "diagnostics": "best-effort, not an atomic snapshot"}
    try:
        record = getattr(getattr(runtimeService, "jobRepository", None), "_jobs", {}).get(jobId)
        details["record"] = {key: small(getattr(record, key, None)) for key in
            ("status", "errorCode", "message", "pid", "acceptedAtMs", "startedAtMs", "endedAtMs")}
        if status is not None:
            details["last_status_reply"] = {key: small(getattr(status, key, None)) for key in
                ("status", "error_code", "message", "pid")}
        events = getattr(getattr(runtimeService, "eventStore", None), "_events", {}).get(jobId, [])
        details["event_tail"] = [{key: small(getattr(event, key, None)) for key in
            ("sequence", "eventType", "workflowId", "nodeId", "code", "message", "timestampMs")}
            for event in events[-32:]]
        supervisor = getattr(runtimeService, "jobSupervisor", None)
        details["heartbeat_seen"] = jobId in getattr(supervisor, "_heartbeatSeen", ())
        details["last_heartbeat_ms"] = getattr(supervisor, "_heartbeat", {}).get(jobId)
        handles = getattr(supervisor, "_handles", {}).get(jobId)
        bridge = getattr(supervisor, "_bridges", {}).get(jobId)
        details["bridge_alive"] = bridge.is_alive() if bridge is not None else False
        if handles:
            try:
                details["process"] = {"alive": handles[0].is_alive(), "exitcode": handles[0].exitcode}
            except (ValueError, OSError) as error:
                details["process"] = small(error)
        details["presentation_errors"] = [small(error) for error in
            list(getattr(supervisor, "presentationErrors", ()))[-8:]]
        owner = getattr(runtimeService, "_presentationOwner", None)
        exporter = getattr(owner, "exporter", None)
        details["export_errors"] = [small(error) for error in list(getattr(exporter, "errors", ()))[-8:]]
        if result is not None:
            details["result"] = {"status": result.status, "executionTerminal": result.executionTerminal,
                "sources": [{"id": small(source.sourceId), "state": source.state,
                             "reason": small(source.reasonCode)} for source in result.sources[:16]]}
    except Exception as error:
        details["diagnostic_error"] = small(error)
    def shorten(value, limit):
        if isinstance(value, str):
            return value if len(value) <= limit else value[:limit] + "..."
        if isinstance(value, dict):
            return {key: shorten(item, limit) for key, item in value.items()}
        if isinstance(value, list):
            return [shorten(item, limit) for item in value]
        return value
    for limit in (256, 128, 64, 32, 16):
        rendered = json.dumps(shorten(details, limit), ensure_ascii=True)
        if len(rendered) <= 16384:
            return rendered
    return json.dumps({"job": jobId[:128], "diagnostic_error": "diagnostic budget exceeded"})


def waitForTerminal(runtimeService, jobId: str, timeoutSeconds: float = 10.0):
    deadline = time.monotonic() + timeoutSeconds
    status = None
    while time.monotonic() < deadline:
        status = runtimeService.GetJobStatus(
            type("Request", (), {"job_id": jobId})(), None
        )
        if getattr(status, "status", "") in TERMINAL_STATUSES:
            return status
        Event().wait(0.01)
    raise AssertionError(f"job did not reach a terminal state: {jobId}; "
                         + jobFailureDetails(runtimeService, jobId, status))
