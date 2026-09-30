from __future__ import annotations

import time


def monotonicMs() -> int:
    # Python 3.10's monotonic clock is shared by processes on supported hosts.
    return time.monotonic_ns() // 1_000_000


class HeartbeatCell:
    """One shared timestamp, independent of the durable event queue's backlog."""

    def __init__(self, context) -> None:
        self._value = context.Value("q", -1)

    def publish(self) -> None:
        lock = self._value.get_lock()
        if not lock.acquire(False):
            return
        try:
            self._value.get_obj().value = monotonicMs()
        finally:
            lock.release()

    def read(self) -> int | None:
        # A terminated worker may own this lock. Never wait for it or refresh
        # the watchdog on contention: its last good sample must still expire.
        lock = self._value.get_lock()
        try:
            if not lock.acquire(False):
                return None
            try:
                return int(self._value.get_obj().value)
            finally:
                lock.release()
        except (OSError, ValueError):
            return None
