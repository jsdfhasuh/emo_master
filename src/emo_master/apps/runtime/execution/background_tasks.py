"""One FIFO I/O worker per execution owner. Workflow objects stay on the caller thread."""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
import threading
import time
from typing import TYPE_CHECKING, Callable, Collection
from uuid import uuid4

from emo_master.apps.runtime.execution.errors import WorkflowExecutionError

if TYPE_CHECKING:
    from emo_master.apps.runtime.workflow.cancellation import CancellationToken
    from emo_master.apps.runtime.workflow.context import RunContext


_DRAIN_TIMEOUT_SECONDS = 30.0
_CANCEL_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class TaskReference:
    id: str


@dataclass
class _Record:
    id: str
    source: dict[str, str]
    size: int
    state: str = "QUEUED"
    result: object = None
    error: BaseException | None = None
    observed: bool = False
    pins: int = 0


def _detachTracebacks(error: BaseException | None) -> None:
    # File writers often wrap an OSError. An inner traceback can still
    # reference the full input image after the outer traceback is detached.
    if error is not None and error.__traceback__ is not None:
        error.__traceback__ = None
        _detachTracebacks(error.__cause__)
        _detachTracebacks(error.__context__)


class TaskControl:
    def __init__(self, service: BackgroundTaskService) -> None:
        self._service = service

    def raiseIfCancelled(self) -> None:
        with self._service._condition:
            self._service._checkPublication()

    def publish(self, finalize: Callable[[], object]) -> object:
        # Only the short final commit runs under the close/publication gate.
        with self._service._condition:
            self._service._checkPublication()
            return finalize()


class BackgroundTaskClient:
    def __init__(self, service: BackgroundTaskService, context: RunContext, nodeId: str) -> None:
        self._service = service
        self._source = dict(jobId=context.jobId, workflowId=context.workflowId,
                            workflowRunId=context.workflowRunId, nodeId=nodeId, nodeRunId=context.nodeRunId)

    def submit(self, fn: Callable, *args, taskName: str, estimatedBytes: int) -> TaskReference:
        return self._service._submit(self._source, fn, args, taskName, estimatedBytes)

    def wait(self, taskId: str) -> object:
        return self._service.wait(taskId)

    def status(self, taskId: str) -> str:
        return self._service.status(taskId)


class BackgroundTaskService:
    def __init__(self, cancellation: CancellationToken | None = None, *, maxPending: int = 32,
                 maxBytes: int = 128 * 1024 * 1024, maxRecords: int = 128) -> None:
        from emo_master.apps.runtime.workflow.cancellation import CancellationToken

        if min(maxPending, maxBytes, maxRecords) < 1:
            raise ValueError("background task limits must be positive")
        self._token = cancellation if cancellation is not None else CancellationToken()
        self._maxPending, self._maxBytes, self._maxRecords = maxPending, maxBytes, maxRecords
        self._condition = threading.Condition()
        self._queue: deque[tuple[_Record, Callable, tuple]] = deque()
        self._records: dict[str, _Record] = {}
        self._recent: OrderedDict[str, None] = OrderedDict()
        self._expiredFailure: _Record | None = None
        self._hasUnobserved = False
        self._outstanding = self._bytes = 0  # Includes the active task.
        self._closing = self._publicationClosed = False
        self._thread = threading.Thread(target=self._run, name="runtime-background-io", daemon=True)
        self._thread.start()

    def client(self, context: RunContext, nodeId: str) -> BackgroundTaskClient:
        return BackgroundTaskClient(self, context, nodeId)

    def _submit(self, source: dict[str, str], fn: Callable, args: tuple, name: str, size: int) -> TaskReference:
        if not isinstance(size, int) or size < 0:
            raise ValueError("estimatedBytes must be a non-negative integer")
        if size > self._maxBytes:
            raise WorkflowExecutionError("E_BACKGROUND_TASK_TOO_LARGE",
                                         f"{name}: {size} bytes exceeds {self._maxBytes}", source["nodeId"])
        with self._condition:
            while True:
                self._token.raise_if_cancelled()
                if self._closing:
                    raise WorkflowExecutionError("E_BACKGROUND_TASK_CLOSED", "background task service closed",
                                                 source["nodeId"])
                if self._outstanding < self._maxPending and self._bytes + size <= self._maxBytes:
                    break
                self._condition.wait(_CANCEL_POLL_SECONDS)
            taskId = str(uuid4())
            record = _Record(taskId, dict(source, taskId=taskId, taskName=name), size)
            self._records[taskId] = record
            self._outstanding += 1
            self._bytes += size
            self._queue.append((record, fn, args))
            self._condition.notify_all()
            return TaskReference(taskId)

    def _record(self, taskId: str) -> _Record:
        record = self._records.get(taskId)
        if record is None:
            raise WorkflowExecutionError("E_BACKGROUND_TASK_EXPIRED", f"unknown or expired background task {taskId}")
        return record

    def status(self, taskId: str) -> str:
        with self._condition:
            return self._record(taskId).state

    def pin(self, taskId: str) -> None:
        with self._condition:
            self._record(taskId).pins += 1

    def release(self, taskId: str) -> None:
        with self._condition:
            record = self._record(taskId)
            if record.pins < 1:
                raise ValueError(f"background task has no pinned reference: {taskId}")
            record.pins -= 1
            if not record.pins and taskId not in self._recent and record.state not in {"QUEUED", "RUNNING"}:
                self._discardRecord(taskId)

    def wait(self, taskId: str) -> object:
        from emo_master.apps.runtime.workflow.cancellation import CancellationRequested

        with self._condition:
            record = self._record(taskId)
            while record.state in {"QUEUED", "RUNNING"}:
                self._checkPublication()
                self._condition.wait(_CANCEL_POLL_SECONDS)
            if record.state == "FAILED":
                self._raiseFailure(record)
            if record.state == "CANCELLED":
                raise CancellationRequested(f"background task cancelled: {taskId}")
            return record.result

    def raiseUnobservedFailure(self, exclude: Collection[str] = ()) -> None:
        """A pending FILE_REFERENCE has its own error consumer until its binding is released."""
        with self._condition:
            if not self._hasUnobserved:
                return
            if self._expiredFailure is not None:
                # Expired records cannot have pinned bindings.
                self._raiseFailure(self._expiredFailure)
            deferred = False
            for record in self._records.values():
                if record.state == "FAILED" and not record.observed:
                    if record.id not in exclude:
                        self._raiseFailure(record)
                    deferred = True
            self._hasUnobserved = deferred

    def _raiseFailure(self, record: _Record) -> None:
        record.observed = True
        if self._expiredFailure is record:
            self._expiredFailure = None
        error = record.error
        code = getattr(error, "code", None)
        if not isinstance(code, str) or not code:
            code = "E_BACKGROUND_TASK_FAILED"  # e.g. cv2.error.code is an integer.
        raise WorkflowExecutionError(code,
                                     f"{record.source['taskName']}: {error}", record.source["nodeId"],
                                     diagnostics={"backgroundTask": dict(record.source)}) from error

    def _checkPublication(self) -> None:
        from emo_master.apps.runtime.workflow.cancellation import CancellationRequested

        self._token.raise_if_cancelled()
        if self._publicationClosed:
            raise CancellationRequested("background task publication closed")

    def _discardRecord(self, taskId: str) -> None:
        record = self._records.pop(taskId)
        if record.state == "FAILED" and not record.observed and self._expiredFailure is None:
            self._expiredFailure = record

    def _finish(self, record: _Record, state: str) -> None:
        record.state = state
        self._outstanding -= 1
        self._bytes -= record.size
        self._recent[record.id] = None
        if len(self._recent) > self._maxRecords:
            oldest, _ = self._recent.popitem(last=False)
            if not self._records[oldest].pins:
                self._discardRecord(oldest)
        if state == "FAILED":
            self._hasUnobserved = True
        self._condition.notify_all()

    def _abandon(self) -> None:
        self._closing = self._publicationClosed = True
        while self._queue:
            record, _, _args = self._queue.popleft()
            self._finish(record, "CANCELLED")
        self._condition.notify_all()

    def _run(self) -> None:
        from emo_master.apps.runtime.workflow.cancellation import CancellationRequested

        control = TaskControl(self)
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._queue or self._closing)
                if self._token.is_cancelled():
                    self._abandon()
                if not self._queue:
                    self._publicationClosed = True
                    return
                record, fn, args = self._queue.popleft()
                record.state = "RUNNING"
            try:
                control.raiseIfCancelled()
                record.result = fn(control, *args)
                state = "SUCCEEDED"
            except CancellationRequested:
                state = "CANCELLED"
            except BaseException as error:
                _detachTracebacks(error)
                record.error = error
                state = "FAILED"
            del fn, args
            with self._condition:
                self._finish(record, state)
            del record

    def close(self, *, drain: bool = True, waitForActive: bool = False) -> None:
        if not drain:
            with self._condition:
                self._abandon()
            if waitForActive:
                # The Job Supervisor enforces the existing user-stop deadline.
                self._thread.join()
            return
        deadline = time.monotonic() + _DRAIN_TIMEOUT_SECONDS
        with self._condition:
            self._closing = True
            self._condition.notify_all()
            while self._outstanding and not self._publicationClosed:
                if self._token.is_cancelled():
                    self._abandon()
                    self._token.raise_if_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._abandon()
                    raise WorkflowExecutionError("E_BACKGROUND_TASK_DRAIN_TIMEOUT",
                                                 "background tasks exceeded the normal drain deadline")
                self._condition.wait(min(remaining, _CANCEL_POLL_SECONDS))
            if self._outstanding:  # Another caller abandoned the active operation.
                self._checkPublication()
            self._publicationClosed = True
        self._thread.join()
        self._token.raise_if_cancelled()
