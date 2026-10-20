"""Bounded per-device FIFO with cancellation fenced by real driver exit."""
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from threading import Condition, Event, Thread
import time
from typing import Callable
from uuid import uuid4


from emo_master.core.contracts.device_driver import DeviceError


@dataclass
class Operation:
    requestId: str
    owner: str
    action: Callable
    waitDeadline: float
    operationTimeout: float
    generation: str
    future: Future = field(default_factory=Future)
    cancellation: Event = field(default_factory=Event)
    state: str = "QUEUED"
    operationDeadline: float = 0.0
    cleanup: bool = False


class DeviceExecutor:
    def __init__(self, identity, *, capacity=64, idleAction=None):
        if capacity < 1:
            raise ValueError("device queue capacity must be positive")
        self.identity, self.capacity = identity, capacity
        self.generation = uuid4().hex
        self.condition = Condition()
        self.queue = deque()
        self.active = None
        self.closed = False
        self.quarantined = False
        self.idleAction = idleAction
        self.lastError = ""
        self.thread = Thread(target=self._run, name="device-io-" + identity, daemon=True)
        self.thread.start()

    def submit(self, owner, action, *, waitTimeout=5.0, operationTimeout=5.0, requestId=None, cancellableWait=False, cleanup=False):
        if not 0 < waitTimeout <= 600 or (not 0 < operationTimeout <= 600 and not
                (cancellableWait and operationTimeout == float("inf"))):
            raise DeviceError("E_DEVICE_BUDGET", "device budgets must be within (0, 600] seconds")
        with self.condition:
            if self.closed or self.quarantined and not cleanup:
                raise DeviceError("E_DEVICE_UNAVAILABLE", "device is closed or quarantined")
            if len(self.queue) >= self.capacity:
                raise DeviceError("E_DEVICE_QUEUE_FULL", "device operation queue is full")
            operation = Operation(requestId or uuid4().hex, owner, action, time.monotonic() + waitTimeout,
                                  operationTimeout, self.generation, cleanup=cleanup)
            self.queue.append(operation)
            self.condition.notify_all()
            return operation

    def cancel(self, operation):
        with self.condition:
            operation.cancellation.set()
            if operation.state == "QUEUED":
                self.queue.remove(operation)
                self._fail(operation, DeviceError("E_DEVICE_CANCELLED", "queued operation cancelled"))
            self.condition.notify_all()

    def cancelOwner(self, owner):
        with self.condition:
            for operation in list(self.queue):
                if operation.owner == owner:
                    self.cancel(operation)
            if self.active is not None and self.active.owner == owner:
                self.active.cancellation.set()

    @staticmethod
    def _fail(operation, error):
        operation.state = "FAILED"
        operation.future.set_exception(error)

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.queue, timeout=.05 if self.idleAction else None)
                if self.closed and not self.queue:
                    return
                if self.queue:
                    operation = self.queue.popleft()
                else:
                    action = self.idleAction() if not self.quarantined else None
                    if action is None:
                        continue
                    operation = Operation(uuid4().hex, "__device_idle__", action,
                                          time.monotonic() + 5, 5, self.generation)
                if self.quarantined and not operation.cleanup:
                    self._fail(operation, DeviceError("E_DEVICE_UNAVAILABLE", "device requires explicit recovery"))
                    continue
                if time.monotonic() >= operation.waitDeadline:
                    self._fail(operation, DeviceError("E_DEVICE_WAIT_TIMEOUT", "device queue wait expired"))
                    continue
                operation.state = "EXECUTING"
                operation.operationDeadline = time.monotonic() + operation.operationTimeout
                self.active = operation
            deadline = operation.operationDeadline
            try:
                result = operation.action(operation.cancellation.is_set, deadline, operation.generation)
            except BaseException as error:
                with self.condition:
                    self.quarantined |= not isinstance(error, DeviceError) or error.unhealthy
                    self.lastError = str(error)
                    self._fail(operation, error)
            else:
                with self.condition:
                    # A completed side effect is never relabeled NOT_EXECUTED.
                    operation.state = "COMPLETED"
                    operation.future.set_result(result)
            finally:
                with self.condition:
                    self.active = None
                    self.condition.notify_all()

    def wait(self, operation, cancelled=lambda: False):
        # Caller abandonment does not release the device thread or native resources.
        while not operation.future.done():
            if cancelled():
                self.cancel(operation)
                if operation.future.done():
                    return operation.future.result()
                raise DeviceError("E_DEVICE_CANCELLED", "operation cancellation requested; awaiting driver exit",
                                  outcome="UNKNOWN")
            with self.condition:
                queuedExpired = operation.state == "QUEUED" and time.monotonic() >= operation.waitDeadline
                activeExpired = operation.state == "EXECUTING" and time.monotonic() >= operation.operationDeadline
                if queuedExpired:
                    self.queue.remove(operation)
                    self._fail(operation, DeviceError("E_DEVICE_WAIT_TIMEOUT", "device queue wait expired"))
            if queuedExpired:
                return operation.future.result()
            if activeExpired:
                self.cancel(operation)
                if operation.future.done():
                    return operation.future.result()
                raise DeviceError("E_DEVICE_OPERATION_TIMEOUT", "operation deadline expired; driver still owns device",
                                  outcome="UNKNOWN")
            operation.cancellation.wait(.01) if not operation.cancellation.is_set() else time.sleep(.01)
        return operation.future.result()

    def close(self, timeout=2.0):
        with self.condition:
            self.closed = True
            for operation in list(self.queue):
                self.cancel(operation)
            if self.active:
                self.active.cancellation.set()
            self.condition.notify_all()
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise DeviceError("E_DEVICE_RELEASE_PENDING", "native operation has not exited", outcome="UNKNOWN")

    def snapshot(self):
        with self.condition:
            return dict(identity=self.identity, generation=self.generation, queued=len(self.queue),
                        lastError=self.lastError,
                        activeRequestId=self.active.requestId if self.active else None,
                        activeOwner=self.active.owner if self.active else None,
                        state="CLOSED" if self.closed else "QUARANTINED" if self.quarantined else
                              "BUSY" if self.active else "IDLE")
