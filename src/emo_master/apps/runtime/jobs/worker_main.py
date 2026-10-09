from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import queue
import threading
import time

from emo_master import __version__
from emo_master.apps.runtime.context.global_counters import ProjectGlobalCounters
from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.apps.runtime.artifacts.store import ArtifactStore
from emo_master.apps.runtime.preview.store import PreviewSnapshotWriter
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.contracts.legacy_snapshots import normalizeLegacySnapshotPolicy
from emo_master.apps.runtime.jobs.models import JobProcessSpec
from emo_master.apps.runtime.jobs.heartbeat import HeartbeatCell


def runJobProcess(spec: JobProcessSpec, cancelEvent, eventQueue) -> None:
    heartbeatStop = threading.Event()
    heartbeatEventsStopped = threading.Event()
    heartbeatEmissionLock = threading.Lock()

    def publishTerminal(event):
        # Once terminal is enqueued, no heartbeat can follow it or race queue
        # close. Cell pulses continue until the existing feeder flush finishes.
        with heartbeatEmissionLock:
            heartbeatEventsStopped.set()
        _put(eventQueue, event)

    heartbeatThread = threading.Thread(
        target=heartbeatLoop,
        args=(spec.jobId, spec.projectId, spec.workflowId, eventQueue, heartbeatStop,
              spec.heartbeatIntervalMs, spec.heartbeatCell, heartbeatEventsStopped,
              heartbeatEmissionLock),
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
        scanResult = PluginRegistry(coreVersion=__version__).scanRoots(
            Path(root) for root in spec.pluginRootPaths
        )
        registry: dict[str, object] = dict(scanResult.activeOperators)
        compiler = WorkflowCompiler(operatorRegistry=registry)
        compiled = compiler.compile(document, pluginRootPaths=tuple(spec.pluginRootPaths))
        inputs = json.loads(spec.inputsJson or "{}")
        if not isinstance(inputs, dict):
            raise ValueError("inputs_json must contain an object")
        token = CancellationToken(cancelEvent)
        publisher = _eventPublisher(spec.jobId, spec.projectId, eventQueue)
        globalCounters = (
            ProjectGlobalCounters(SqliteStore(Path(spec.runtimeDbPath)), spec.projectId)
            if spec.runtimeDbPath and spec.projectId
            else None
        )
        globalVariables = None
        if spec.runtimeDbPath and spec.projectId:
            globalVariables = ProjectGlobalVariables(SqliteStore(Path(spec.runtimeDbPath)), spec.projectId,
                                                     document.globalVariables, spec.jobId)
            globalVariables.synchronize()
            globalVariables.initializeJob()
        runner = WorkflowRunner(
            compiledProject=compiled,
            operatorRegistry=registry,
            eventPublisher=publisher,
            artifactStore=ArtifactStore(Path(spec.jobWorkspacePath)) if spec.copyArtifacts else None,
            previewSnapshotStore=(PreviewSnapshotWriter(Path(spec.jobWorkspacePath),
                jobId=spec.jobId, projectRevision=document.project.revision)
                if normalizeLegacySnapshotPolicy(spec.legacySnapshotPolicy) == "ALL" else None),
            globalCounters=globalCounters,
            globalVariables=globalVariables,
            resultCollector=_collector(spec, eventQueue),
            retainOperators=spec.continuous,
        )
        _put(eventQueue, {"eventType": "job.started", "jobId": spec.jobId, "projectId": spec.projectId, "pid": _pid(), "workflowId": spec.workflowId})
        if spec.continuous:
            _runContinuous(runner, spec, inputs, token, cancelEvent)
        else:
            context = RunContext.root(spec.jobId, spec.workflowId, spec.jobWorkspacePath,
                                      projectId=spec.projectId)
            runner.run(spec.workflowId, inputs, context, token)
        publishTerminal({"eventType": "job.completed", "jobId": spec.jobId, "projectId": spec.projectId, "pid": _pid(), "workflowId": spec.workflowId})
    except CancellationRequested as err:
        publishTerminal({"eventType": "job.aborted", "jobId": spec.jobId, "projectId": spec.projectId, "workflowId": spec.workflowId, "pid": _pid(), "code": "E_CANCELLED", "message": str(err)})
    except BaseException as err:
        publishTerminal(
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
        try:
            with heartbeatEmissionLock:
                heartbeatEventsStopped.set()
            close = getattr(eventQueue, "close", None)
            join = getattr(eventQueue, "join_thread", None)
            if spec.heartbeatCell is not None and callable(close) and callable(join):
                # multiprocessing otherwise waits here implicitly after this
                # target returns. Keep liveness through that same ownership.
                # Legacy direct callers without a cell may consume only after
                # runJobProcess returns, so must not acquire this join behavior.
                close()
                join()
        finally:
            heartbeatStop.set()
            if heartbeatThread.is_alive():
                heartbeatThread.join(timeout=1.0)


def _runContinuous(runner, spec, inputs, token, cancelEvent):
    context = RunContext.root(spec.jobId, spec.workflowId, spec.jobWorkspacePath,
                              projectId=spec.projectId)
    primaryError = None
    try:
        while True:
            token.raise_if_cancelled()
            started = time.monotonic()
            context = RunContext.root(spec.jobId, spec.workflowId, spec.jobWorkspacePath,
                                      projectId=spec.projectId)
            runner.run(spec.workflowId, deepcopy(inputs), context, token)
            delay = max(0.0, spec.cycleIntervalMs / 1000.0 - (time.monotonic() - started))
            if cancelEvent.wait(delay):
                token.raise_if_cancelled()
    except Exception as error:
        primaryError = error
        raise
    finally:
        runner.closeSession(context, primaryError)


def heartbeatLoop(jobId: str, projectId: str, workflowId: str, eventQueue, stopEvent: threading.Event, intervalMs: int, heartbeatCell: HeartbeatCell | None = None, eventsStopped: threading.Event | None = None, emissionLock=None) -> None:
    interval = max(0.05, intervalMs / 1000.0)
    if eventsStopped is None:
        eventsStopped = threading.Event()
    if emissionLock is None:
        emissionLock = threading.Lock()
    while True:
        if heartbeatCell is not None:
            heartbeatCell.publish()
        with emissionLock:
            if not eventsStopped.is_set():
                event = {"eventType": "process.heartbeat", "jobId": jobId, "projectId": projectId, "workflowId": workflowId, "pid": _pid()}
                if heartbeatCell is None:
                    _put(eventQueue, event)
                else:
                    # Diagnostic backpressure must not pause authoritative cell pulses.
                    try:
                        eventQueue.put_nowait(event)
                    except queue.Full:
                        pass
        if stopEvent.wait(interval):
            return


def _collector(spec, eventQueue):
    if spec.presentation is None:
        return None
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    return ResultCollector(spec.presentation, spec.presentation["queue"].put_nowait)


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
