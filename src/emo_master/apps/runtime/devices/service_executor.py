"""Managed service calls run on their visiting IPC thread, sharing one device gate."""
from concurrent.futures import Future
from dataclasses import dataclass, field
from threading import Event
from time import monotonic
from uuid import uuid4

from .executor import DeviceError


@dataclass
class ServiceOperation:
    owner: str
    cycleId: str
    requestId: str = field(default_factory=lambda: uuid4().hex)
    cancellation: Event = field(default_factory=Event)
    future: Future = field(default_factory=Future)


class ServiceExecutor:
    def __init__(self, executor):
        self.executor = executor
        self.condition = executor.condition
        self.operations = {}

    def admit(self, owner, cycleId):
        with self.condition:
            if self.executor.closed or self.executor.quarantined:
                raise DeviceError('E_DEVICE_UNAVAILABLE', 'device is closed or quarantined')
            if len(self.operations) >= 64:
                raise DeviceError('E_DEVICE_QUEUE_FULL', 'managed service action limit reached')
            operation = ServiceOperation(owner, cycleId)
            self.operations[operation.requestId] = operation
            return operation

    def run(self, operation, action, cancelled, deadline, generation):
        try:
            result = action(lambda: cancelled() or operation.cancellation.is_set(), deadline, generation)
        except BaseException as error:
            with self.condition:
                if not isinstance(error, DeviceError) or error.unhealthy:
                    self.executor.quarantined = True
                    self.executor.lastError = str(error)
            operation.future.set_exception(error)
            raise
        else:
            operation.future.set_result(result)
            return result
        finally:
            with self.condition:
                self.operations.pop(operation.requestId, None)
                self.condition.notify_all()

    def cancelOwner(self, owner=None):
        with self.condition:
            for operation in self.operations.values():
                if owner is None or operation.owner == owner:
                    operation.cancellation.set()

    def busy(self, owner=None, cycleId=None):
        with self.condition:
            return any((owner is None or op.owner == owner) and (cycleId is None or op.cycleId == cycleId)
                       for op in self.operations.values())

    def wait(self, owner=None, cycleId=None, deadline=None):
        with self.condition:
            if not self.condition.wait_for(lambda: not self.busy(owner, cycleId),
                    timeout=max(0, (deadline or monotonic() + 5) - monotonic())):
                raise DeviceError('E_DEVICE_RELEASE_PENDING', 'service actions are still exiting', outcome='UNKNOWN')
