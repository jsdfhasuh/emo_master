"""Fixed daemon workers; pending tasks and completed DTOs share a bounded budget."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import inspect
import threading
import time
from typing import Any, Hashable
from uuid import uuid4

from emo_master.apps.designer.services.display_calls import DisplayCallContext, DisplayCallError
from emo_master.apps.designer.services.operator_icon_cache import IconRequest, validateIconReply


@dataclass(frozen=True)
class DisplayResult:
    key: Hashable
    payload: Any = None
    code: str = ""
    message: str = ""


@dataclass
class _Task:
    key: Hashable
    operation: str
    timeoutMs: int
    priority: int
    context: DisplayCallContext = field(default_factory=DisplayCallContext)
    started: bool = False
    done: bool = False


def _invoke(method, context: DisplayCallContext, owner: str, timeoutMs: int, *args):
    # Old embedded test doubles can expose only a synchronous, no-keyword API.
    parameters = inspect.signature(method).parameters
    keywords = {"timeoutMs": timeoutMs, "cancellationToken": context, "owner": owner}
    if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        keywords = {k: v for k, v in keywords.items() if k in parameters}
    return method(*args, **keywords)


class OperatorIconWorker:
    def __init__(self, runtimeClient, *, workers: int = 4, capacity: int = 128) -> None:
        self.runtimeClient = runtimeClient
        self.owner = "display-" + uuid4().hex
        self.capacity = max(1, capacity)
        self._condition = threading.Condition(threading.RLock())
        self._tasks: dict[Hashable, _Task] = {}
        self._pending: list[_Task] = []
        self._results: deque[DisplayResult] = deque()
        self._closed = False
        self._threads = [threading.Thread(target=self._run, daemon=True, name=f"{self.owner}-{i}") for i in range(workers)]
        for thread in self._threads:
            thread.start()

    def request(self, key: Hashable, *, operation: str = "icon", timeoutMs: int = 2000, priority: int = 1) -> bool:
        with self._condition:
            if self._closed:
                return False
            if key in self._tasks:
                self._tasks[key].priority = min(priority, self._tasks[key].priority)
                return True
            if len(self._pending) >= self.capacity or len(self._tasks) >= self.capacity + len(self._threads):
                candidates = [t for t in self._pending if t.priority > priority]
                if not candidates:
                    return False
                self.cancel(max(candidates, key=lambda t: t.priority).key)
            task = _Task(key, operation, timeoutMs, priority)
            self._tasks[key] = task
            self._pending.append(task)
            self._condition.notify()
            return True

    def cancel(self, key: Hashable) -> None:
        with self._condition:
            task = self._tasks.pop(key, None)
            if task:
                task.context.cancel()
                if task in self._pending:
                    self._pending.remove(task)
            self._results = deque(result for result in self._results if result.key != key)

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or bool(self._pending))
                if self._closed:
                    return
                task = min(self._pending, key=lambda item: item.priority)
                self._pending.remove(task)
                task.started = True
            try:
                task.context.start(task.timeoutMs)
                if task.operation == "catalog":
                    payload = _invoke(self.runtimeClient.listOperators, task.context, self.owner, task.timeoutMs)
                else:
                    request = task.key
                    if not isinstance(request, IconRequest):
                        raise ValueError("icon request identity required")
                    method = getattr(self.runtimeClient, "getOperatorIconAsset", None)
                    if not callable(method):
                        raise DisplayCallError("UNIMPLEMENTED", "Runtime does not expose icons")
                    reply = _invoke(method, task.context, self.owner, task.timeoutMs,
                                    request.operatorId, request.version, request.sha256)
                    payload = validateIconReply(request, reply)
                task.context.check()
                result = DisplayResult(task.key, payload)
            except Exception as err:
                result = DisplayResult(task.key, code=str(getattr(err, "code", "E_DISPLAY_FAILED")), message=str(err))
            with self._condition:
                if not self._closed and not task.done and self._tasks.get(task.key) is task:
                    task.done = True
                    self._results.append(result)

    def poll(self, maximum: int = 4) -> list[DisplayResult]:
        with self._condition:
            for task in tuple(self._tasks.values()):
                if task.started and not task.done and task.context.time_remaining() <= 0:
                    task.context.cancel()
                    task.done = True
                    self._results.append(DisplayResult(task.key, code="E_DISPLAY_TIMEOUT", message="display result exceeded deadline"))
            results: list[DisplayResult] = []
            while self._results and len(results) < maximum:
                result = self._results.popleft()
                self._tasks.pop(result.key, None)
                results.append(result)
            return results

    def beginClose(self) -> None:
        with self._condition:
            self._closed = True
            for key in tuple(self._tasks):
                self.cancel(key)
            self._condition.notify_all()
        closeOwner = getattr(self.runtimeClient, "closeDisplayOwner", None)
        if callable(closeOwner):
            closeOwner(self.owner)

    def isRunning(self) -> bool:
        return any(thread.is_alive() for thread in self._threads)

    def wait(self, timeout: float = 3.0) -> bool:
        end = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0.0, end - time.monotonic()))
        return not self.isRunning()
