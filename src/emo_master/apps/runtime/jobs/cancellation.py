"""One-way cancellation that cannot retain a terminated worker's lock."""
from __future__ import annotations

import time


class JobCancellation:
    def __init__(self, context, stagingWorkspace: str | None = None) -> None:
        self.stagingWorkspace = stagingWorkspace
        # A single shared byte only moves from 0 to 1. There is no compound
        # update or payload publication, and every writer stores the same value.
        # Spawn passes the shared-memory handle, not a process-local copy.
        self._requested = context.Value("b", 0, lock=False)
        # The worker updates this byte under its task-service lock. Configuration
        # drain can read it even while the workflow is parked at beginCycle.
        self._backgroundPending = context.Value("b", 0, lock=False)
        # Only the workflow execution thread updates this flag. A finished
        # write still belongs to the Job until its artifact has been published.
        self._artifactsPending = context.Value("b", 0, lock=False)

    def setBackgroundPending(self, pending: bool) -> None:
        self._backgroundPending.value = pending

    def hasPendingBackgroundTasks(self) -> bool:
        return bool(self._artifactsPending.value or self._backgroundPending.value)

    def setArtifactsPending(self, pending: bool) -> None:
        self._artifactsPending.value = pending

    def canPollParkedArtifacts(self) -> bool:
        return bool(self._artifactsPending.value and not self._backgroundPending.value)

    def set(self) -> None:
        self._requested.value = 1

    def is_set(self) -> bool:
        return bool(self._requested.value)

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        while not self.is_set():
            remaining = .02 if deadline is None else min(.02, deadline - time.monotonic())
            if remaining <= 0:
                return False
            # Do not busy-spin or hold a process-shared lock while waiting.
            time.sleep(remaining)
        return True
