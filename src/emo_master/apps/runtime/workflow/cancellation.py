from __future__ import annotations

from threading import Event


class CancellationRequested(RuntimeError):
    code = "E_CANCELLED"


class CancellationToken:
    def __init__(self, event: object | None = None) -> None:
        self._event = event if event is not None else Event()

    def cancel(self) -> None:
        setter = getattr(self._event, "set", None)
        if callable(setter):
            setter()

    @property
    def isCancellationRequested(self) -> bool:
        checker = getattr(self._event, "is_set", None)
        return bool(checker()) if callable(checker) else False

    def is_cancelled(self) -> bool:
        return self.isCancellationRequested

    def raise_if_cancelled(self) -> None:
        if self.isCancellationRequested:
            raise CancellationRequested("execution cancelled")
