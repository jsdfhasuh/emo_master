"""Current managed-device API. Contexts contain identities, never native handles."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol


class DeviceError(RuntimeError):
    def __init__(self, code, message, *, outcome="NOT_EXECUTED", unhealthy=False, diagnostics=None):
        super().__init__(message)
        self.code, self.outcome, self.unhealthy = code, outcome, unhealthy
        self.diagnostics = diagnostics or {}


class DeviceEventSink(Protocol):
    def __call__(self, eventType: str, message: str, **fields) -> None: ...


@dataclass(frozen=True)
class DeviceRuntimeContext:
    runtimeInstanceId: str
    projectId: str
    projectLoadGeneration: str
    deviceId: str
    generation: str
    events: DeviceEventSink


@dataclass(frozen=True)
class DeviceOperationContext:
    ownerId: str
    cycleId: str
    planRevision: int
    slot: str = ""
    selectors: dict = field(default_factory=dict)
    workflowId: str = ""
    nodeId: str = ""
    jobId: str = ""
    requestId: str = ""
    source: dict = field(default_factory=dict)


class DeviceDriverProtocol(Protocol):
    def bindRuntime(self, context: DeviceRuntimeContext) -> None: ...
    @staticmethod
    def physicalIdentity(definition) -> str | None: ...
    def connect(self, params, cancelled: Callable, deadline: float) -> None: ...
    def validateAction(self, action: str, arguments: dict) -> None: ...
    def execute(self, action, params, cancelled: Callable, deadline: float, *, arguments,
                operationContext: DeviceOperationContext): ...
    def revokeOwner(self, ownerId: str) -> None: ...
    def requestStop(self, reason: str) -> None: ...
    def close(self, deadline: float) -> None: ...
    def dispose(self, deadline: float) -> None: ...
    def snapshot(self) -> dict: ...


class DeviceDriver:
    """Shared hooks for the current synchronous built-ins, not an old-API adapter."""
    runtimeContext: DeviceRuntimeContext | None = None
    disposed = False

    def bindRuntime(self, context):
        self.runtimeContext = context

    def validateAction(self, action, arguments):
        if arguments:
            raise DeviceError("E_DEVICE_PARAMETERS", "this action does not accept arguments")

    def revokeOwner(self, ownerId):
        pass

    def requestStop(self, reason):
        # Synchronous SDK drivers obey the operation cancellation/deadline;
        # their session is released on its owning FIFO thread, not here.
        pass

    def dispose(self, deadline=None):
        self.close(deadline)
        self.disposed = True
        self.runtimeContext = None

    def testConnection(self, params, cancelled, deadline):
        self.connect(params, cancelled, deadline)
        return {"connected": True}
