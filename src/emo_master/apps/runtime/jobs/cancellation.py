"""One-way cancellation that cannot retain a terminated worker's lock."""
from __future__ import annotations

import time


class JobCancellation:
    def __init__(self, context) -> None:
        # A single shared byte only moves from 0 to 1. There is no compound
        # update or payload publication, and every writer stores the same value.
        # Spawn passes the shared-memory handle, not a process-local copy.
        self._requested = context.Value("b", 0, lock=False)

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
