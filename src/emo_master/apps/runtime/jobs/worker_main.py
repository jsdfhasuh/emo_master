from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
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
from emo_master.apps.runtime.preview.async_writer import AsyncPreviewSnapshotWriter
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.contracts.legacy_snapshots import normalizeLegacySnapshotPolicy
from emo_master.apps.runtime.jobs.models import JobProcessSpec
from emo_master.apps.runtime.jobs.heartbeat import HeartbeatCell


def runJobProcess(spec: JobProcessSpec, cancelEvent, eventQueue, communicationPipe=None, devicePipe=None) -> None:
    from emo_master.apps.runtime.communication.ipc import CommunicationProxy
    communication = CommunicationProxy(communicationPipe, cancelEvent) if communicationPipe is not None else None
    from emo_master.apps.runtime.devices.ipc import DeviceProxy
    devices = DeviceProxy(devicePipe, cancelEvent) if devicePipe is not None else None
    if devices is not None:
        devices.retainFrames = bool(spec.runGroupId)
    heartbeatStop = threading.Event()
    heartbeatEventsStopped = threading.Event()
    heartbeatEmissionLock = threading.Lock()
    runner = None
    token = CancellationToken(cancelEvent)
    previewWriter = None

    def publishTerminal(event):
        if previewWriter is not None:
            # Finish final captures before promotion/retirement; cancellation
            # discards queued diagnostics, never replays business operations.
            try:
                previewWriter.close(drain=event["eventType"] == "job.completed")
            except Exception as error:
                # Optional preview diagnostics must not mask a business error.
                event["payload"] = dict(event.get("payload", {}), previewSnapshotError=str(error))
            event["payload"] = dict(event.get("payload", {}), previewSnapshots=previewWriter.statistics())
        if event["eventType"] == "job.completed":
            token.raise_if_cancelled()  # A stop during final drain is still a stop.
        # Once terminal is enqueued, no heartbeat can follow it or race queue
        # close. Cell pulses continue until the existing feeder flush finishes.
        with heartbeatEmissionLock:
            heartbeatEventsStopped.set()
        # Capture metadata is optional; diagnostic enrichment must not replace
        # the original terminal/error or stop the queue-drain heartbeat fence.
        captureErrors = getattr(runner, "captureErrors", 0)
        if captureErrors:
            event["payload"] = dict(event.get("payload", {}), captureErrors=captureErrors,
                                    captureDiagnostics=list(getattr(runner, "captureDiagnostics", ())))
        _put(eventQueue, event)

    def errorDetails(error):
        return {key: getattr(error, key) for key in ("outcome", "diagnostics") if hasattr(error, key)}

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
        token.raise_if_cancelled()
        snapshot = json.loads(Path(spec.projectSnapshotPath).read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            raise RuntimeError("project snapshot must be an object")
        document = ProjectDocument.model_validate(snapshot)
        scanResult = PluginRegistry(coreVersion=__version__).scanRoots(
            Path(root) for root in spec.pluginRootPaths
        )
        registry: dict[str, object] = dict(scanResult.activeOperators)
        compiler = WorkflowCompiler(operatorRegistry=registry, deviceTypes=scanResult.deviceTypes if devices else None)
        compiled = compiler.compile(document, pluginRootPaths=tuple(spec.pluginRootPaths))
        inputs = json.loads(spec.inputsJson or "{}")
        if not isinstance(inputs, dict):
            raise ValueError("inputs_json must contain an object")
        publisher = _eventPublisher(spec.jobId, spec.projectId, eventQueue,
                                    inspectionCredits=spec.inspectionCredits, cancellation=token)
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
        if normalizeLegacySnapshotPolicy(spec.legacySnapshotPolicy) == "ALL":
            previewWriter = AsyncPreviewSnapshotWriter(Path(spec.jobWorkspacePath), jobId=spec.jobId,
                projectRevision=document.project.revision,
                inspectionPublisher=publisher if spec.inspectionEnabled else None,
                # The encoder thread stages immutable PNGs. Only descriptors
                # share the ordered node-event channel, never large image bytes.
                inlineInspection=False,
                errorPublisher=publisher, cancellation=token,
                minIntervalMs=50 if spec.continuous or spec.targetSlotId else 0)
        runner = WorkflowRunner(
            compiledProject=compiled,
            operatorRegistry=registry,
            eventPublisher=publisher,
            artifactStore=ArtifactStore(Path(spec.jobWorkspacePath)) if spec.copyArtifacts else None,
            previewSnapshotStore=previewWriter,
            globalCounters=globalCounters,
            globalVariables=globalVariables,
            resultCollector=_collector(spec, eventQueue),
            retainOperators=spec.continuous or bool(spec.targetSlotId),
            communication=communication,
            devices=devices,
        )
        runner.prepareResources(spec.workflowId,
            RunContext.root(spec.jobId, spec.workflowId, spec.jobWorkspacePath, projectId=spec.projectId), token)
        token.raise_if_cancelled()
        _put(eventQueue, {"eventType": "job.started", "jobId": spec.jobId, "projectId": spec.projectId, "pid": _pid(), "workflowId": spec.workflowId})
        from emo_master.apps.runtime.project_config.cycle_plan import CyclePlanSession
        cyclePlans = CyclePlanSession(runner, devices, document, compiler, registry,
                                      tuple(spec.pluginRootPaths), spec.planRevision, cancelEvent.is_set)
        if spec.managedConfiguration:
            runner.cycleScope = cyclePlans.iteration
        def runCycle(context, cycleInputs):
            context = replace(context, runGroupId=spec.runGroupId)
            if not spec.managedConfiguration:
                return runner.run(spec.workflowId, cycleInputs, context, token)
            with cyclePlans.scope(context) as context:
                return runner.run(spec.workflowId, cycleInputs, context, token)
        if spec.targetSlotId:
            _runConsumer(runner, spec, devices, cyclePlans, token, cancelEvent, publisher)
        elif spec.runGroupId:
            if devices is None:
                raise RuntimeError("E_DEVICE_RUNTIME_UNAVAILABLE: Dispatch requires managed device IPC")
            deadline = time.monotonic() + 30
            while not devices.framework("dispatchReady"):
                token.raise_if_cancelled()
                if time.monotonic() >= deadline:
                    raise RuntimeError("E_DISPATCH_START_TIMEOUT: consumers did not become ready")
                cancelEvent.wait(.02)
            if spec.continuous:
                _runContinuous(runner, spec, inputs, token, cancelEvent, runCycle=runCycle)
            else:
                runCycle(RunContext.root(spec.jobId, spec.workflowId, spec.jobWorkspacePath,
                                        projectId=spec.projectId), inputs)
        elif spec.continuous:
            _runContinuous(runner, spec, inputs, token, cancelEvent, runCycle=runCycle)
        else:
            context = RunContext.root(spec.jobId, spec.workflowId, spec.jobWorkspacePath,
                                      projectId=spec.projectId)
            runCycle(context, inputs)
        publishTerminal({"eventType": "job.completed", "jobId": spec.jobId, "projectId": spec.projectId, "pid": _pid(), "workflowId": spec.workflowId})
    except CancellationRequested as err:
        publishTerminal({"eventType": "job.aborted", "jobId": spec.jobId, "projectId": spec.projectId, "workflowId": spec.workflowId, "pid": _pid(), "code": "E_CANCELLED", "message": str(err), "payload": errorDetails(err)})
    except BaseException as err:
        if getattr(err, "code", "") == "E_CONFIG_DRAINED":
            publishTerminal({"eventType": "job.aborted", "jobId": spec.jobId, "projectId": spec.projectId,
                             "workflowId": spec.workflowId, "pid": _pid(), "code": "E_CONFIG_DRAINED", "message": str(err)})
            return
        publishTerminal(
            {
                "eventType": "job.failed",
                "jobId": spec.jobId,
                "projectId": spec.projectId,
                "workflowId": spec.workflowId,
                "pid": _pid(),
                "code": (getattr(err, "code", "E_EXEC_FAILED") if isinstance(err, Exception)
                         else "E_WORKER_CRASHED"),
                "message": str(err),
                "payload": errorDetails(err),
            },
        )
        raise
    finally:
        if previewWriter is not None:
            previewWriter.close(drain=False)
        if devices is not None:
            devices.close()
        if communication is not None:
            communication.close()
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


def _runContinuous(runner, spec, inputs, token, cancelEvent, runCycle=None):
    context = RunContext.root(spec.jobId, spec.workflowId, spec.jobWorkspacePath,
                              projectId=spec.projectId)
    primaryError = None
    try:
        while True:
            token.raise_if_cancelled()
            started = time.monotonic()
            context = RunContext.root(spec.jobId, spec.workflowId, spec.jobWorkspacePath,
                                      projectId=spec.projectId)
            if runCycle is None:
                runner.run(spec.workflowId, deepcopy(inputs), context, token)
            else:
                runCycle(context, deepcopy(inputs))
            delay = max(0.0, spec.cycleIntervalMs / 1000.0 - (time.monotonic() - started))
            if cancelEvent.wait(delay):
                token.raise_if_cancelled()
    except Exception as error:
        primaryError = error
        raise
    finally:
        runner.closeSession(context, primaryError)


def _runConsumer(runner, spec, devices, cyclePlans, token, cancelEvent, publish):
    context = RunContext.root(spec.jobId, spec.workflowId, spec.jobWorkspacePath, projectId=spec.projectId)
    primary = None
    try:
        while True:
            token.raise_if_cancelled()
            invocation = devices.framework("dispatchNext")
            if invocation["status"] == "CLOSED":
                return
            if invocation["status"] == "IDLE":
                cancelEvent.wait(.02)
                continue
            context = replace(RunContext.root(spec.jobId, spec.workflowId, spec.jobWorkspacePath,
                projectId=spec.projectId), **{key: invocation[key] for key in (
                    "runGroupId", "dispatchId", "deliveryId", "invocationId", "targetSlotId", "cycleId", "planRevision")})
            identity = {key: invocation[key] for key in (
                "runGroupId", "dispatchId", "deliveryId", "invocationId", "cycleId", "planRevision")}
            cyclePlans.install(invocation["cycle"])
            context = replace(context, documentRevision=invocation["cycle"].get("documentRevision", 0))
            try:
                values = {key: devices.decode(value) for key, value in invocation["inputs"].items()}
                result = runner.run(spec.workflowId, values, context, token)
                outputs = devices.encode(result.outputs, context)
            except Exception as error:
                if cancelEvent.is_set():
                    raise
                devices.framework("dispatchComplete", **identity, status="FAILED", outputs=None,
                             code=getattr(error, "code", "E_DISPATCH_CONSUMER"), message=str(error))
                publish("dispatch.invocation.failed", context, str(error), level="ERROR",
                        code=getattr(error, "code", "E_DISPATCH_CONSUMER"))
                raise
            devices.framework("dispatchComplete", **identity, status="COMPLETED", outputs=outputs)
            devices.frameCopies.clear()
            publish("dispatch.invocation.completed", context, "consumer invocation completed",
                    payload=dict(frameIds=list(invocation["frames"])))
    except BaseException as error:
        primary = error
        raise
    finally:
        runner.closeSession(context, primary)


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


def _eventPublisher(jobId: str, projectId: str, eventQueue, *, inspectionCredits=None, cancellation=None):
    def publish(eventType, context, message, level="INFO", code="", payload=None):
        reserved = eventType == 'inspection.snapshot' and inspectionCredits is not None
        if reserved:
            while not inspectionCredits.acquire(timeout=.05):
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
        try:
            if reserved and cancellation is not None:
                cancellation.raise_if_cancelled()
            _put(eventQueue, {
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
                "payload": dict(payload or {}, cycleId=context.cycleId, planRevision=context.planRevision,
                    runGroupId=context.runGroupId, dispatchId=context.dispatchId, deliveryId=context.deliveryId,
                    invocationId=context.invocationId, targetSlotId=context.targetSlotId,
                    documentRevision=context.documentRevision),
            })
        except BaseException:
            if reserved:
                inspectionCredits.release()
            raise

    return publish


def _put(eventQueue, event: dict[str, object]) -> None:
    # Inspection is a private IPC barrier, not a persisted JSON event. Preserve
    # its bounded immutable PNG bytes; ordinary diagnostic events stay JSON-safe.
    eventQueue.put(event if event.get("eventType") == "inspection.snapshot" else _jsonSafe(event))


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
