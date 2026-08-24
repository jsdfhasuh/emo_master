from __future__ import annotations

import json
from pathlib import Path
import threading

from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.apps.runtime.artifacts.store import ArtifactStore
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.apps.runtime.jobs.models import JobProcessSpec


def runJobProcess(spec: JobProcessSpec, cancelEvent, eventQueue) -> None:
    heartbeatStop = threading.Event()
    heartbeatThread = threading.Thread(
        target=heartbeatLoop,
        args=(spec.jobId, spec.projectId, spec.workflowId, eventQueue, heartbeatStop, spec.heartbeatIntervalMs),
        name=f"runtime-heartbeat-{spec.jobId}",
        daemon=True,
    )
    try:
        _put(eventQueue, {"eventType": "job.process.started", "jobId": spec.jobId, "projectId": spec.projectId, "workflowId": spec.workflowId, "pid": _pid()})
        heartbeatThread.start()
        snapshot = json.loads(Path(spec.projectSnapshotPath).read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            raise RuntimeError("project snapshot must be an object")
        document = ProjectDocument.model_validate(snapshot)
        registry: dict[str, object] = {}
        for root in spec.pluginRootPaths:
            result = PluginRegistry(coreVersion="0.2.0").scan(Path(root))
            registry.update(result.activeOperators)
        compiler = WorkflowCompiler(operatorRegistry=registry)
        compiled = compiler.compile(document, pluginRootPaths=tuple(spec.pluginRootPaths))
        inputs = json.loads(spec.inputsJson or "{}")
        if not isinstance(inputs, dict):
            raise ValueError("inputs_json must contain an object")
        token = CancellationToken(cancelEvent)
        publisher = _eventPublisher(spec.jobId, spec.projectId, eventQueue)
        runner = WorkflowRunner(
            compiledProject=compiled,
            operatorRegistry=registry,
            eventPublisher=publisher,
            artifactStore=ArtifactStore(Path(spec.jobWorkspacePath)),
        )
        _put(eventQueue, {"eventType": "job.started", "jobId": spec.jobId, "projectId": spec.projectId, "pid": _pid(), "workflowId": spec.workflowId})
        context = RunContext.root(
            spec.jobId,
            spec.workflowId,
            spec.jobWorkspacePath,
            projectId=spec.projectId,
        )
        runner.run(spec.workflowId, inputs, context, token)
        _put(eventQueue, {"eventType": "job.completed", "jobId": spec.jobId, "projectId": spec.projectId, "pid": _pid(), "workflowId": spec.workflowId})
    except CancellationRequested as err:
        _put(eventQueue, {"eventType": "job.aborted", "jobId": spec.jobId, "projectId": spec.projectId, "workflowId": spec.workflowId, "pid": _pid(), "code": "E_CANCELLED", "message": str(err)})
    except BaseException as err:
        _put(
            eventQueue,
            {
                "eventType": "job.failed",
                "jobId": spec.jobId,
                "projectId": spec.projectId,
                "workflowId": spec.workflowId,
                "pid": _pid(),
                "code": getattr(err, "code", "E_WORKER_CRASHED"),
                "message": str(err),
            },
        )
        raise
    finally:
        heartbeatStop.set()
        if heartbeatThread.is_alive():
            heartbeatThread.join(timeout=1.0)


def heartbeatLoop(jobId: str, projectId: str, workflowId: str, eventQueue, stopEvent: threading.Event, intervalMs: int) -> None:
    interval = max(0.05, intervalMs / 1000.0)
    _put(eventQueue, {"eventType": "process.heartbeat", "jobId": jobId, "projectId": projectId, "workflowId": workflowId, "pid": _pid()})
    while not stopEvent.wait(interval):
        _put(eventQueue, {"eventType": "process.heartbeat", "jobId": jobId, "projectId": projectId, "workflowId": workflowId, "pid": _pid()})


def _eventPublisher(jobId: str, projectId: str, eventQueue):
    def publish(eventType, context, message, level="INFO", code="", payload=None):
        _put(
            eventQueue,
            {
                "eventType": eventType,
                "jobId": jobId,
                "projectId": context.projectId or projectId,
                "workflowId": context.workflowId,
                "workflowRunId": context.workflowRunId,
                "parentWorkflowRunId": context.parentWorkflowRunId,
                "nodeId": context.callerNodeId,
                "nodeRunId": context.nodeRunId,
                "iterationPath": list(context.iterationPath),
                "message": message,
                "level": level,
                "code": code,
                "payload": payload or {},
            },
        )

    return publish


def _put(eventQueue, event: dict[str, object]) -> None:
    eventQueue.put(_jsonSafe(event))


def _jsonSafe(value):
    if isinstance(value, dict):
        return {str(key): _jsonSafe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonSafe(item) for item in value]
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _pid() -> int:
    import os

    return os.getpid()
