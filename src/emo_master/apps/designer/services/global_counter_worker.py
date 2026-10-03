from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Callable


@dataclass(frozen=True)
class GlobalCounterWorkerResult:
    operation: str
    generation: int
    payload: object


@dataclass(frozen=True)
class GlobalCounterWorkerFailure:
    operation: str
    generation: int
    code: str
    message: str


try:
    from PySide2.QtCore import QThread, Signal

    class GlobalCounterWorker(QThread):
        resultReady: Any = Signal(object)
        failed: Any = Signal(object)

        def __init__(
            self,
            runtimeClient,
            operation: str,
            projectId: str,
            generation: int,
            *,
            name: str = "",
            value: int = 0,
        ) -> None:
            super().__init__()
            self.runtimeClient = runtimeClient
            self.operation = operation
            self.projectId = projectId
            self.generation = generation
            self.name = name
            self.value = value
            self._stopEvent = threading.Event()
            self.result: GlobalCounterWorkerResult | None = None
            self.error: GlobalCounterWorkerFailure | None = None

        def run(self) -> None:  # type: ignore[override]
            _runGlobalCounterWorker(self)

        def requestStop(self) -> None:
            self._stopEvent.set()

        def stopRequested(self) -> bool:
            return self._stopEvent.is_set()

except Exception:  # pragma: no cover

    class _Signal:
        def __init__(self) -> None:
            self._callbacks: list[Callable[..., object]] = []

        def connect(self, callback: Callable[..., object]) -> None:
            self._callbacks.append(callback)

        def emit(self, *args: object) -> None:
            for callback in list(self._callbacks):
                callback(*args)

    class GlobalCounterWorker:  # type: ignore[no-redef]
        def __init__(
            self,
            runtimeClient,
            operation: str,
            projectId: str,
            generation: int,
            *,
            name: str = "",
            value: int = 0,
        ) -> None:
            self.runtimeClient = runtimeClient
            self.operation = operation
            self.projectId = projectId
            self.generation = generation
            self.name = name
            self.value = value
            self._stopEvent = threading.Event()
            self.resultReady = _Signal()
            self.failed = _Signal()
            self.finished = _Signal()
            self._thread: threading.Thread | None = None
            self.result: GlobalCounterWorkerResult | None = None
            self.error: GlobalCounterWorkerFailure | None = None

        def start(self) -> None:
            self._thread = threading.Thread(target=self.run, daemon=True)
            self._thread.start()

        def run(self) -> None:
            try:
                _runGlobalCounterWorker(self)
            finally:
                self.finished.emit()

        def requestStop(self) -> None:
            self._stopEvent.set()

        def stopRequested(self) -> bool:
            return self._stopEvent.is_set()

        def wait(self, timeoutMs: int = -1) -> bool:
            if self._thread is None:
                return True
            self._thread.join(None if timeoutMs < 0 else timeoutMs / 1000.0)
            return not self._thread.is_alive()

        def isRunning(self) -> bool:
            return self._thread is not None and self._thread.is_alive()


def _runGlobalCounterWorker(worker: GlobalCounterWorker) -> None:
    if worker.stopRequested():
        return
    try:
        payload = _executeGlobalCounterRequest(worker)
    except Exception as err:
        failure = GlobalCounterWorkerFailure(
            operation=worker.operation,
            generation=worker.generation,
            code=str(getattr(err, "code", "E_RUNTIME_STATE_UNAVAILABLE")),
            message=str(err) or "global counter request failed",
        )
        worker.error = failure
        if not worker.stopRequested():
            worker.failed.emit(failure)
        return
    result = GlobalCounterWorkerResult(
        operation=worker.operation,
        generation=worker.generation,
        payload=payload,
    )
    worker.result = result
    if not worker.stopRequested():
        worker.resultReady.emit(result)


def _executeGlobalCounterRequest(worker: GlobalCounterWorker) -> object:
    if worker.operation == "list":
        return worker.runtimeClient.listGlobalCounters(worker.projectId)
    if worker.operation == "get":
        return worker.runtimeClient.getGlobalCounter(worker.projectId, worker.name)
    if worker.operation == "set":
        return worker.runtimeClient.setGlobalCounter(
            worker.projectId,
            worker.name,
            worker.value,
        )
    if worker.operation == "reset":
        return worker.runtimeClient.resetGlobalCounter(worker.projectId, worker.name)
    raise ValueError(f"unsupported global counter operation: {worker.operation}")
