from __future__ import annotations

from threading import Event
import time


TERMINAL_STATUSES = {"COMPLETED", "FAILED", "ABORTED"}


def waitForTerminal(runtimeService, jobId: str, timeoutSeconds: float = 10.0):
    deadline = time.monotonic() + timeoutSeconds
    while time.monotonic() < deadline:
        status = runtimeService.GetJobStatus(
            type("Request", (), {"job_id": jobId})(), None
        )
        if getattr(status, "status", "") in TERMINAL_STATUSES:
            return status
        Event().wait(0.01)
    raise AssertionError(f"job did not reach a terminal state: {jobId}")
