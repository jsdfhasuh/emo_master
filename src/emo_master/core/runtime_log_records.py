"""Structured desktop log entries and Runtime ownership classification."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
import re
import time

from emo_master.core.runtime_log_messages import frameworkLogMessage


_LINE_PATTERN = re.compile(r"^\[(?P<time>[^]]+)]\[(?P<level>[^]]+)]\s?(?P<message>.*)$")
WORKFLOW_LIFECYCLE_EVENTS = frozenset({"workflow.started", "workflow.completed", "workflow.failed"})


@dataclass(frozen=True)
class RuntimeLogCaller:
    workflowId: str
    workflow: str
    node: str

    def label(self) -> str:
        return f"调用节点 {self.workflow} / {self.node}"


@dataclass(frozen=True)
class StructuredLogEntry:
    timestampMs: int
    level: str
    message: str
    source: str = "designer"
    eventType: str = ""
    jobId: str = ""
    workflowId: str = ""
    workflowRunId: str = ""
    nodeId: str = ""
    nodeRunId: str = ""
    iterationPath: tuple[int, ...] = ()
    code: str = ""
    payload: dict[str, object] = field(default_factory=dict)
    projectId: str = ""
    # Local provenance, assigned only to events from an accepted Job. Never
    # infer this from a node ID or from whichever project happens to be open.
    projectInstanceToken: str = ""
    workflowName: str = ""
    nodeName: str = ""
    originApp: str = ""
    component: str = ""
    runtimeInstanceId: str = ""
    sequence: int = 0
    eventId: int | None = None
    parentWorkflowRunId: str = ""
    parentWorkflowRunIdRecorded: bool = False
    iterationPathRecorded: bool = False
    rawTimestampMs: int | None = None
    projectName: str = ""
    targetWorkflowId: str = ""
    targetWorkflowName: str = ""
    _runtimeRecord: dict[str, object] | None = field(default=None, repr=False, compare=False)
    caller: RuntimeLogCaller | None = field(default=None, repr=False, compare=False)

    @classmethod
    def fromLine(cls, line: str) -> "StructuredLogEntry":
        match = _LINE_PATTERN.match(str(line))
        if match is None:
            return cls(_nowMs(), "INFO", str(line))
        now = datetime.now()
        try:
            parsed = datetime.strptime(match.group("time"), "%H:%M:%S").replace(
                year=now.year, month=now.month, day=now.day
            )
            timestampMs = int(parsed.timestamp() * 1000.0)
        except ValueError:
            timestampMs = _nowMs()
        return cls(timestampMs, _level(match.group("level")), match.group("message"))

    @classmethod
    def fromRuntimeEvent(cls, event: dict[str, object]) -> "StructuredLogEntry":
        eventType = str(event.get("eventType", ""))
        rawPayload = event.get("payload", {})
        payload = dict(rawPayload) if isinstance(rawPayload, dict) else {}

        def name(field: str) -> str:
            for value in (payload.get(field), event.get(field)):
                if isinstance(value, str) and value:
                    return value
            return ""

        rawIteration = event.get("iterationPath")
        iteration: tuple[int, ...] = ()
        validIteration = False
        if isinstance(rawIteration, (list, tuple)) and all(type(index) is int and index >= 0 for index in rawIteration):
            iteration = tuple(rawIteration)
            validIteration = True
        # Older native device rows used an empty transport path even when the
        # cache row itself supplied no iteration attribution.
        if (not iteration and eventType.startswith("device.") and isinstance(rawPayload, dict)
                and type(rawPayload.get("cursor")) is int and "iterationPath" not in rawPayload):
            validIteration = False
        rawTimestamp = event.get("timestampMs")
        timestampMs = rawTimestamp if type(rawTimestamp) is int and rawTimestamp > 0 else None
        parentWorkflowRunId = event.get("parentWorkflowRunId")
        from emo_master.core.contracts.diagnostic_logging import componentForEvent
        return cls(
            timestampMs=timestampMs if timestampMs is not None else _nowMs(),
            level=_level(event.get("level", "INFO")),
            message=str(event.get("message", "")),
            source="runtime",
            eventType=eventType,
            jobId=str(event.get("jobId", "")),
            workflowId=str(event.get("workflowId", "")),
            workflowRunId=str(event.get("workflowRunId", "")),
            nodeId=str(event.get("nodeId", "")),
            nodeRunId=str(event.get("nodeRunId", "")),
            iterationPath=iteration,
            code=str(event.get("code", "")),
            payload=payload,
            projectId=str(event.get("projectId", "")),
            workflowName=name("workflowName"),
            nodeName=name("nodeName"),
            originApp=str(event.get("originApp", "runtime") or "runtime"),
            component=str(event.get("component") or componentForEvent(eventType)),
            runtimeInstanceId=str(event.get("runtimeInstanceId", "")),
            sequence=_integer(event.get("sequence"), 0),
            eventId=_integer(event.get("eventId"), 0) or None,
            parentWorkflowRunId=parentWorkflowRunId if isinstance(parentWorkflowRunId, str) else "",
            parentWorkflowRunIdRecorded=isinstance(parentWorkflowRunId, str),
            iterationPathRecorded=validIteration,
            rawTimestampMs=timestampMs,
            projectName=name("projectName"),
            targetWorkflowId=name("targetWorkflowId"),
            targetWorkflowName=name("targetWorkflowName"),
            _runtimeRecord=dict(event),
        )

    @property
    def isRuntime(self) -> bool:
        return self.originApp.lower() == "runtime" if self.originApp else self.source == "runtime"

    @property
    def effectiveJobId(self) -> str:
        if not self.isRuntime:
            return ""
        if self.jobId != "__runtime__":
            return self.jobId
        jobId = self.payload.get("jobId")
        return jobId if isinstance(jobId, str) and jobId not in {"", "__runtime__"} else ""

    @property
    def isRuntimeSystem(self) -> bool:
        return self.isRuntime and self.jobId == "__runtime__" and not self.effectiveJobId

    @property
    def isWorkflowCaller(self) -> bool:
        return bool(self.nodeId and self.eventType in WORKFLOW_LIFECYCLE_EVENTS)

    def displayTime(self) -> str:
        return datetime.fromtimestamp(self.timestampMs / 1000.0).strftime("%H:%M:%S.%f")[:-3]

    def displayLine(self) -> str:
        node = f"[{self.nodeId}]" if self.nodeId else ""
        event = f"[{self.eventType}]" if self.eventType else ""
        return f"[{self.displayTime()}][{self.level}]{node}{event} {self.message}"

    def workflowKey(self) -> tuple[str, str]:
        return self.projectId, self.workflowId

    def nodeKey(self) -> tuple[str, str, str]:
        return self.projectId, self.workflowId, self.nodeId

    def displayWorkflow(self) -> str:
        return self.workflowName or self.workflowId

    def displayNode(self) -> str:
        if self.isWorkflowCaller:
            return ""
        return self.nodeName or self.nodeId

    def designArea(self) -> str:
        if self.source == "page_designer" or self.eventType.startswith("presentation."):
            return "page"
        if self.source in {"editor", "flow_designer"} or self.eventType.startswith(
                ("node.", "workflow.", "loop.", "job.", "artifact.", "preview.")):
            return "flow"
        return "general"

    def displayMessage(self) -> str:
        return frameworkLogMessage(self.eventType, self.message, nodeId=self.nodeId, workflowId=self.workflowId,
                                   code=self.code, iterationPath=self.iterationPath, payload=self.payload)

    def rawRecord(self) -> dict[str, object]:
        if self._runtimeRecord is not None and self.isRuntime:
            return dict(self._runtimeRecord)
        return self.detailPayload()

    def detailPayload(self) -> dict[str, object]:
        return {
            "timestampMs": self.rawTimestampMs if self._runtimeRecord is not None and self.isRuntime else self.timestampMs,
            "level": self.level,
            "source": self.source,
            "eventType": self.eventType,
            "jobId": self.jobId,
            "projectId": self.projectId,
            "workflowId": self.workflowId,
            "workflowName": self.workflowName,
            "workflowRunId": self.workflowRunId,
            "parentWorkflowRunId": self.parentWorkflowRunId,
            "nodeId": self.nodeId,
            "nodeName": self.nodeName,
            "nodeRunId": self.nodeRunId,
            "iterationPath": list(self.iterationPath),
            "code": self.code,
            "message": self.message,
            "payload": self.payload,
            "originApp": self.originApp or ("runtime" if self.source == "runtime" else "designer"),
            "component": self.component or (self.source if self.source in {
                "editor", "flow_designer", "page_designer", "runtime_client"} else "application"),
            "runtimeInstanceId": self.runtimeInstanceId,
            "sequence": self.sequence,
            "eventId": self.eventId,
        }


def resolveWorkflowCaller(entry: StructuredLogEntry,
                          entries: Iterable[StructuredLogEntry]) -> RuntimeLogCaller | None:
    """Resolve the recorded parent run against loaded records from this same Job."""
    parent = entry.parentWorkflowRunId
    if not (entry.isWorkflowCaller and parent and parent != entry.workflowRunId
            and entry.projectId and entry.effectiveJobId):
        return None
    candidates = [row for row in entries
                  if row.workflowRunId == parent and row.projectId == entry.projectId
                  and row.effectiveJobId == entry.effectiveJobId and row.runtimeInstanceId == entry.runtimeInstanceId]
    workflowIds = {row.workflowId for row in candidates if row.workflowId}
    if len(workflowIds) != 1:
        return None
    workflowId, = workflowIds
    workflowName = next((row.workflowName for row in candidates if row.workflowId == workflowId and row.workflowName), "")
    nodeName = next((row.nodeName for row in candidates if row.workflowId == workflowId and row.nodeId == entry.nodeId
                     and not row.isWorkflowCaller and row.nodeName), "")
    return RuntimeLogCaller(workflowId, workflowName or workflowId, nodeName or entry.nodeId)


def _integer(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _level(value: object) -> str:
    normalized = str(value).strip().upper()
    if normalized == "WARNING":
        normalized = "WARN"
    return normalized if normalized in {"DEBUG", "INFO", "WARN", "ERROR"} else "INFO"


def _nowMs() -> int:
    return int(time.time() * 1000.0)
