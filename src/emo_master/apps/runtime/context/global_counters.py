from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


MAX_GLOBAL_COUNTER_VALUE = (1 << 63) - 1

E_COUNTER_NAME_INVALID = "E_COUNTER_NAME_INVALID"
E_COUNTER_VALUE_RANGE = "E_COUNTER_VALUE_RANGE"
E_COUNTER_BUSY = "E_COUNTER_BUSY"
E_RUNTIME_STATE_UNAVAILABLE = "E_RUNTIME_STATE_UNAVAILABLE"


@dataclass(frozen=True)
class GlobalCounterRecord:
    name: str
    value: int
    updatedAtMs: int


class GlobalCounterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class _GlobalCounterStore(Protocol):
    def applyGlobalCounter(
        self,
        projectId: str,
        name: str,
        *,
        increment: bool = False,
        reset: bool = False,
    ) -> GlobalCounterRecord: ...

    def getGlobalCounter(self, projectId: str, name: str) -> GlobalCounterRecord: ...

    def listGlobalCounters(self, projectId: str) -> list[GlobalCounterRecord]: ...

    def setGlobalCounter(
        self, projectId: str, name: str, value: int
    ) -> GlobalCounterRecord: ...

    def resetGlobalCounter(self, projectId: str, name: str) -> GlobalCounterRecord: ...


class ProjectGlobalCounters:
    """Counter access restricted to one project for a Runtime Job process."""

    def __init__(self, store: _GlobalCounterStore, projectId: str) -> None:
        if not isinstance(projectId, str) or projectId == "":
            raise GlobalCounterError(
                E_RUNTIME_STATE_UNAVAILABLE,
                "global counter project context is unavailable",
            )
        self._store = store
        self._projectId = projectId

    def apply(
        self,
        name: str,
        *,
        increment: bool = False,
        reset: bool = False,
    ) -> GlobalCounterRecord:
        return self._store.applyGlobalCounter(
            self._projectId,
            name,
            increment=increment,
            reset=reset,
        )

    def get(self, name: str) -> GlobalCounterRecord:
        return self._store.getGlobalCounter(self._projectId, name)

    def list(self) -> list[GlobalCounterRecord]:
        return self._store.listGlobalCounters(self._projectId)

    def set(self, name: str, value: int) -> GlobalCounterRecord:
        return self._store.setGlobalCounter(self._projectId, name, value)

    def reset(self, name: str) -> GlobalCounterRecord:
        return self._store.resetGlobalCounter(self._projectId, name)


def validateGlobalCounterName(name: object) -> str:
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 128
        or name.strip() != name
    ):
        raise GlobalCounterError(
            E_COUNTER_NAME_INVALID,
            "counter name must contain 1 to 128 characters without leading or trailing whitespace",
        )
    return name


def validateGlobalCounterValue(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > MAX_GLOBAL_COUNTER_VALUE
    ):
        raise GlobalCounterError(
            E_COUNTER_VALUE_RANGE,
            f"counter value must be an integer between 0 and {MAX_GLOBAL_COUNTER_VALUE}",
        )
    return value
