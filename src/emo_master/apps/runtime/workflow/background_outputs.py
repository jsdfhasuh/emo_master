"""Execution-thread ownership of submitted files and SQLite input deliveries."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from emo_master.apps.runtime.execution.errors import WorkflowExecutionError
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.core.workflow.models import CompiledNode
from emo_master.core.workflow.parameter_bindings import BoundValue, CompiledBinding, captureBoundValue


def pendingTaskId(value) -> str | None:
    if (isinstance(value, dict) and value.get("state") == "SUBMITTED"
            and value.get("saved") is False and isinstance(value.get("path"), str)
            and isinstance(value.get("taskId"), str) and value["taskId"]):
        return value["taskId"]
    return None


@dataclass(frozen=True)
class _PendingBinding:
    taskId: str
    path: str
    binding: CompiledBinding
    source: RunContext


class BackgroundOutputs:
    def __init__(self, service, publishArtifact):
        self.service = service
        self.publishArtifact = publishArtifact
        self._artifacts: dict[str, list[tuple[CompiledNode, str, RunContext]]] = {}
        # Multiple SQLite rows or consumers can depend on a single task.
        self._bindingRefs: Counter[str] = Counter()

    def _pin(self, taskId):
        if self.service is None:
            raise WorkflowExecutionError("E_BACKGROUND_TASK_EXPIRED", f"Unknown background task: {taskId}")
        self.service.pin(taskId)

    def artifact(self, node, value, context):
        taskId = pendingTaskId(value)
        if taskId is None:
            self.publishArtifact(node, value, context)
            return
        if taskId not in self._artifacts:
            self._pin(taskId)
            self._artifacts[taskId] = []
        self._artifacts[taskId].append((node, value["path"], context))

    def deferBinding(self, taskId, path, binding, context):
        self._pin(taskId)
        self._bindingRefs[taskId] += 1
        return _PendingBinding(taskId, path, binding, context)

    def _finishArtifacts(self, taskId):
        if taskId not in self._artifacts:
            return
        state = self.service.status(taskId)
        if state in ("QUEUED", "RUNNING"):
            return
        files = self._artifacts.pop(taskId)
        try:
            if state == "SUCCEEDED":
                for node, path, source in files:
                    if not Path(path).is_file():
                        raise WorkflowExecutionError("E_BACKGROUND_TASK_FAILED",
                            f"Background task completed without publishing its file: {path}", node.nodeId)
                    self.publishArtifact(node, {"saved": True, "path": path}, source)
        finally:
            self.service.release(taskId)

    def poll(self):
        if self.service is not None:
            for taskId in tuple(self._artifacts):
                self._finishArtifacts(taskId)
            # An outstanding SQLite consumer owns the decision to stop or continue.
            self.service.raiseUnobservedFailure(exclude=self._bindingRefs)

    def prepareBindings(self, values, *, enabled):
        if not any(isinstance(value, _PendingBinding) for value in values.values()):
            return values
        resolved = {}
        try:
            for index, value in values.items():
                if not isinstance(value, _PendingBinding):
                    resolved[index] = value
                elif enabled:
                    resolved[index] = self._resolve(value)
            return resolved
        finally:
            self.releaseBindings(values)

    def _resolve(self, pending):
        source = pending.source
        try:
            self.service.wait(pending.taskId)
        except WorkflowExecutionError as error:
            if error.code == "E_CANCELLED":
                raise
            message = (f"{error} (taskId={pending.taskId}, sourceNodeId={source.callerNodeId}, "
                       f"sourceNodeRunId={source.nodeRunId})")
            return BoundValue(source.workflowRunId, source.nodeRunId,
                              errorCode=error.code, errorMessage=message)
        self._finishArtifacts(pending.taskId)
        return captureBoundValue({"saved": True, "path": pending.path}, pending.binding, source)

    def releaseBindings(self, values):
        for value in values.values():
            if isinstance(value, _PendingBinding):
                taskId = value.taskId
                self.service.release(taskId)
                self._bindingRefs[taskId] -= 1
                if not self._bindingRefs[taskId]:
                    del self._bindingRefs[taskId]
