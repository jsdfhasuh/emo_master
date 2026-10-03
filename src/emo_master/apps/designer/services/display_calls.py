"""Cancellation/deadlines for read-only presentation calls, without Qt ownership."""

from __future__ import annotations

import threading
import time
from typing import Any


class DisplayCallError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class DisplayCallContext:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cancelled = False
        self._deadline: float | None = None
        self._call: Any = None

    def start(self, timeoutMs: int) -> None:
        with self._lock:
            if self._deadline is None:
                self._deadline = time.monotonic() + max(1, timeoutMs) / 1000.0
        self.check()

    def attach(self, call: Any) -> None:
        with self._lock:
            self._call = call
            if not self.is_active():
                call.cancel()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self._call is not None:
                self._call.cancel()

    def time_remaining(self) -> float:
        with self._lock:
            return (
                max(0.0, self._deadline - time.monotonic())
                if self._deadline is not None
                else float("inf")
            )

    def is_active(self) -> bool:
        with self._lock:
            return not self._cancelled and self.time_remaining() > 0

    def check(self) -> None:
        with self._lock:
            if self._cancelled:
                raise DisplayCallError(
                    "E_DISPLAY_CANCELLED", "display request cancelled"
                )
            if self.time_remaining() <= 0:
                raise DisplayCallError(
                    "E_DISPLAY_TIMEOUT", "display request deadline exceeded"
                )
