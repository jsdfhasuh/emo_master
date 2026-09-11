from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time


class JobStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"

    @classmethod
    def terminal(cls, value: str) -> bool:
        return value in {cls.COMPLETED.value, cls.FAILED.value, cls.ABORTED.value}


@dataclass
class JobRecord:
    jobId: str
    projectId: str
    projectRevision: int
    workflowId: str
    status: str = JobStatus.ACCEPTED.value
    pid: int | None = None
    acceptedAtMs: int = field(default_factory=lambda: int(time.time() * 1000))
    startedAtMs: int = 0
    endedAtMs: int = 0
    errorCode: str = ""
    message: str = ""
    stopMode: str = ""

    @property
    def isTerminal(self) -> bool:
        return JobStatus.terminal(self.status)


@dataclass(frozen=True)
class JobProcessSpec:
    jobId: str
    projectSnapshotPath: str
    workflowId: str
    inputsJson: str = "{}"
    pluginRootPaths: tuple[str, ...] = ()
    jobWorkspacePath: str = ""
    heartbeatIntervalMs: int = 500
    heartbeatTimeoutMs: int = 5000
    projectId: str = ""
    runtimeDbPath: str = ""

    @property
    def inputs_json(self) -> str:
        return self.inputsJson


def nowMs() -> int:
    return int(time.time() * 1000)
