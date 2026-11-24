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


@dataclass
class _FilePublication:
    taskId: str
    path: str
    registered: bool = False

    def matches(self, receipt):
        return self.taskId == receipt["taskId"] and self.path == receipt["path"]

    def __deepcopy__(self, memo):
        # Copies of one receipt refer to the same publication on this execution thread.
        return self


class _SubmittedFile(dict):
    """Keep the public JSON receipt unchanged while local dataflow owns its proof."""

    def __init__(self, value):
        super().__init__(value)
        self.publication = _FilePublication(value["taskId"], value["path"])


@dataclass(frozen=True)
class _PendingBinding:
    taskId: str
    path: str
    binding: CompiledBinding
    source: RunContext


class BackgroundOutputs:
    def __init__(self, service, publishArtifact, onPendingArtifactsChanged=None):
        self.service = service
        self.publishArtifact = publishArtifact
        self.onPendingArtifactsChanged = onPendingArtifactsChanged
        self._artifacts: dict[str, list[tuple[CompiledNode, _FilePublication, RunContext]]] = {}
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
            return value
        receipt = (value if isinstance(value, _SubmittedFile) and value.publication.matches(value)
                   else _SubmittedFile(value))
        publication = receipt.publication
        if publication.registered:
            self.publishArtifact(node, {"saved": True, "path": publication.path}, context)
            return receipt
        if taskId not in self._artifacts:
            self._pin(taskId)
            if not self._artifacts and self.onPendingArtifactsChanged is not None:
                self.onPendingArtifactsChanged(True)
            self._artifacts[taskId] = []
        self._artifacts[taskId].append((node, publication, context))
        return receipt

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
                result = self.service.wait(taskId)
                completedPath = result.get("path") if isinstance(result, dict) and result.get("saved") is True else None
                for node, publication, source in files:
                    if completedPath is not None and completedPath != publication.path:
                        raise WorkflowExecutionError("E_BACKGROUND_TASK_FAILED",
                            f"Background task {taskId} did not publish the submitted file: {publication.path}", node.nodeId)
                    if not Path(publication.path).is_file():
                        raise WorkflowExecutionError("E_BACKGROUND_TASK_FAILED",
                            f"Background task completed without publishing its file: {publication.path}", node.nodeId)
                    self.publishArtifact(node, {"saved": True, "path": publication.path}, source)
                    publication.registered = True
        finally:
            self.service.release(taskId)
            if not self._artifacts and self.onPendingArtifactsChanged is not None:
                self.onPendingArtifactsChanged(False)

    def poll(self):
        if self.service is not None:
            for taskId in tuple(self._artifacts):
                self._finishArtifacts(taskId)
            # An outstanding SQLite consumer owns the decision to stop or continue.
            self.service.raiseUnobservedFailure(exclude=self._bindingRefs)

    def forTransfer(self, value):
        """Send completed file receipts across Jobs; task IDs belong to their source process."""
        if isinstance(value, dict):
            taskId = pendingTaskId(value)
            if taskId is not None:
                return self._transferFile(value, taskId)
            return {key: self.forTransfer(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.forTransfer(item) for item in value]
        return value

    def _transferFile(self, receipt, taskId):
        path = receipt["path"]
        publication = (receipt.publication
                       if isinstance(receipt, _SubmittedFile) and receipt.publication.matches(receipt) else None)
        if publication is None or not publication.registered:
            self._pin(taskId)
            try:
                result = self.service.wait(taskId)
                self._finishArtifacts(taskId)
            finally:
                self.service.release(taskId)
            # A plain JSON copy has no local artifact registration. Require the
            # task's own completed result to confirm the exact submitted path.
            confirmed = (publication.registered if publication is not None else
                         isinstance(result, dict) and result.get("saved") is True
                         and result.get("path") == path and Path(path).is_file())
            if not confirmed:
                raise WorkflowExecutionError("E_BACKGROUND_TASK_FAILED",
                    f"Background task {taskId} did not publish the submitted file: {path}")
        return {"saved": True, "path": path}

    def waitForIdle(self):
        if self.service is not None:
            self.service.waitForIdle()
            self.poll()

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
