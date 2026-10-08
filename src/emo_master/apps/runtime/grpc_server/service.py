from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict
from functools import wraps
from pathlib import Path
import shutil
import threading
import time
from typing import Any
from uuid import uuid4

import grpc

from emo_master import __version__
from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
from emo_master.apps.runtime.events.models import RuntimeEvent
from emo_master.apps.runtime.context.global_counters import (
    E_RUNTIME_STATE_UNAVAILABLE,
    GlobalCounterError,
    GlobalCounterRecord,
)
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.context.runtime_lock import RuntimeDataLock
from emo_master.apps.runtime.jobs.manager import JobManager
from emo_master.apps.runtime.jobs.finalization import faultSummary
from emo_master.apps.runtime.jobs.models import JobProcessSpec, JobStatus, nowMs
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_master.apps.runtime.jobs.supervisor import JobSupervisor
from emo_master.apps.runtime.preview.executor import PurePreviewExecutor, parsePreviewParams
from emo_master.apps.runtime.preview.live import LivePreviewManager
from emo_master.apps.runtime.preview.draft import draftPreviewProjectId
from emo_master.apps.runtime.preview.store import PreviewAssetStore
from emo_master.apps.runtime.preview.run_inspection import RunInspectionStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as _runtime_pb2
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.core.contracts.port_types import canonicalPortTypes
from emo_master.core.contracts.legacy_snapshots import normalizeLegacySnapshotPolicy
from emo_master.core.plugin.models import RegistryScanResult
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError

runtime_pb2: Any = _runtime_pb2


def _withProjectStateLock(method: Any) -> Any:
    @wraps(method)
    def locked(self: Any, *args: Any, **kwargs: Any) -> Any:
        self.jobSupervisor.assertMutationAllowed()
        with self._projectStateLock:
            if self._closing and method.__name__ in {
                "LoadProject", "StartJob", "UploadPreviewImage", "RunOperatorPreview", "OpenOperatorPreviewSession",
                "OpenDraftOperatorPreviewSession",
                "OpenRunInspectionSession", "RenewRunInspectionSession"
            }:
                raise RuntimeError("E_RUNTIME_CLOSING")
            return method(self, *args, **kwargs)

    return locked


class RuntimeService(runtime_pb2_grpc.RuntimeServiceServicer):
    @property
    def supportsLegacySnapshotPolicy(self) -> bool:
        # The opt-in normal-Run policy must not widen restricted test hosts.
        owner = getattr(self, "_presentationOwner", None)
        return bool(getattr(owner, "supportsNormalCapture", True))

    def __init__(
        self,
        dbPath: Path | None = None,
        pluginRootPaths: tuple[str, ...] | None = None,
        maxConcurrentJobs: int = 2,
        workspaceRoot: Path | None = None,
        logDirectory: Path | None = None,
        productionMode: bool = False,
    ) -> None:
        explicitDbPath = dbPath is not None
        if dbPath is None:
            dbPath = _defaultDbPath()
        if workspaceRoot is None:
            workspaceRoot = (
                _defaultWorkspaceRoot()
                if not explicitDbPath
                else dbPath.parent / "jobs"
            )
        self.workspaceRoot = workspaceRoot
        self.productionMode = productionMode
        self.workspaceRoot.mkdir(parents=True, exist_ok=True)
        lockName = self.workspaceRoot.name or "runtime"
        self._runtimeDataLock = RuntimeDataLock(
            self.workspaceRoot.parent / f".{lockName}.runtime.lock"
        )
        self._runtimeDataLockIsPrimary = self._runtimeDataLock.acquire()
        self._closed = False
        self._closing = False
        self._closeStages: dict[str, str] = {}
        from emo_master.apps.runtime.business_sqlite.backend import SqliteManagement
        self.sqliteManagement = SqliteManagement()
        try:
            self.sqliteStore = SqliteStore(dbPath)
            self.sqliteStore.initialize()
            if self._runtimeDataLockIsPrimary:
                self.sqliteStore.markOrphanedJobsFailed()
            self.jobRepository = JobRepository(self.sqliteStore)
            self.eventStore = EventStore(self.sqliteStore)
            from emo_master.apps.runtime.business_sqlite.outcomes import SqliteOutcomes
            self.sqliteOutcomes = SqliteOutcomes(self.eventStore)
            self.eventStore.beforeTerminal = self.sqliteOutcomes.beforeTerminal
            self.eventStore.addSink(self.sqliteOutcomes.observe)
        except BaseException:
            self._runtimeDataLock.release()
            raise
        if self._runtimeDataLockIsPrimary:
            try:
                self.sqliteStore.pruneTerminalJobEvents(
                    retentionDays=30,
                    minimumJobsPerProject=100,
                )
                self.sqliteStore.checkpointWal()
                self.sqliteStore.vacuumIfNeeded(0.25)
            except BaseException as err:
                self._recordMaintenanceFailure(
                    f"runtime event startup maintenance failed: {err}"
                )
        if logDirectory is None:
            logDirectory = _defaultLogDirectory(
                dbPath,
                explicitDbPath=explicitDbPath,
            )
        self.operationalLogWriter = RuntimeJsonlLogWriter(
            logDirectory,
            failureCallback=self._recordLogFileFailure,
        )
        self._operationalLogSink = self.operationalLogWriter.enqueue
        self.eventStore.addSink(self._operationalLogSink)
        self._maintenanceStop = threading.Event()
        self._maintenanceThread = threading.Thread(
            target=self._eventMaintenanceLoop,
            name="runtime-event-maintenance",
            daemon=True,
        )
        self._maintenanceThread.start()
        self._workspacePaths: dict[str, Path] = {}
        self._cleanupStaleWorkspaces()
        self.pluginScanResult = self._scanBuiltins(pluginRootPaths)
        self.pluginRootPaths = pluginRootPaths or (str(self._builtinsRoot()),)
        self.previewAssetStore = PreviewAssetStore(
            self.workspaceRoot.parent / "preview-cache"
        )
        self.runInspectionStore = RunInspectionStore(self.previewAssetStore)
        self.eventStore.addSink(self.runInspectionStore.observe)
        self.previewExecutor = PurePreviewExecutor(
            self.pluginScanResult.activeOperators,
            self.previewAssetStore,
        )
        self.livePreviewManager = LivePreviewManager(
            self.pluginScanResult.activeOperators,
            eventPublisher=self.eventStore.append,
        )
        self._jobPreviewKeys: dict[str, str] = {}
        self._loadedProjectPreviewKey = ""
        self._projectStateLock = threading.RLock()
        self._previewJobLock = threading.RLock()
        self.jobSupervisor = JobSupervisor(
            self.jobRepository,
            self.eventStore,
            maxConcurrentJobs=maxConcurrentJobs,
            heartbeatTimeoutMs=5000,
            terminalCallback=self._onJobTerminal,
            retiredCallback=self._onJobRetired,
        )
        self.jobSupervisor.retirementRepairFactory = self._retirementRepair
        self.jobManager = JobManager(self.jobRepository, self.eventStore, self.jobSupervisor)
        self.loadedProjectPath: str | None = None
        self.loadedProjectId: str = ""
        self.loadedDocument: ProjectDocument | None = None
        self.loadedPayload: dict[str, object] | None = None
        self.jobMessages: dict[str, str] = {}
        self.runtimeInstanceId = str(uuid4())
        # Never evict a request and then treat a delayed retry as a fresh run.
        # Bounded admission is reset only by a new Runtime generation.
        self._startRequests: dict[str, tuple[str, object]] = {}
        self._jobStartRequestIds: dict[str, str] = {}
        # Open only after the Runtime's owners are fully initialized, so a
        # failed acquisition can use the normal, retryable shutdown path.
        try:
            self.sqliteStore.retainIdleConnection()
        except BaseException:
            self.close()
            raise

    @_withProjectStateLock
    def LoadProject(self, request, context):  # type: ignore[override]
        _ = context
        # A draft preview can exist before any formal project has been loaded.
        with self._previewJobLock:
            cleanupErrors = self.livePreviewManager.closeAll(timeoutSeconds=3.0)
        if cleanupErrors:
            return runtime_pb2.LoadProjectReply(
                ok=False, status="FAILED",
                message="E_PREVIEW_RELEASE_FAILED: " + "; ".join(cleanupErrors),
            )
        projectPathRaw = str(getattr(request, "project_path", ""))
        projectFile = self._resolveProjectFile(Path(projectPathRaw))
        if projectFile is None:
            self._clearLoadedProject("project.json not found; please load project folder")
            return runtime_pb2.LoadProjectReply(
                ok=False,
                status="FAILED",
                message="project.json not found; please load project folder",
            )
        try:
            rawPayload = json.loads(projectFile.read_text(encoding="utf-8"))
            if not isinstance(rawPayload, dict):
                raise ValueError("project file must contain an object")
            canonical = migrateProjectPayload(rawPayload)
            document = ProjectDocument.model_validate(canonical)
            if self.productionMode:
                from emo_master.core.project.runtime_directory import prepareRuntimeProject
                document = prepareRuntimeProject(document, projectFile.parent,
                    self.pluginScanResult.activeOperators,
                    protectedPaths=(*self.sqliteProtectedPaths(), self.operationalLogWriter.directory))
            WorkflowCompiler(operatorRegistry=self.pluginScanResult.activeOperators).compile(
                document,
                pluginRootPaths=self.pluginRootPaths,
            )
        except WorkflowCompileError as err:
            self._clearLoadedProject(str(err))
            return runtime_pb2.LoadProjectReply(ok=False, status="FAILED", message=str(err))
        except Exception as err:
            self._clearLoadedProject(str(err))
            return runtime_pb2.LoadProjectReply(
                ok=False, status="FAILED", message=f"invalid project file: {err}"
            )

        self.loadedProjectPath = str(projectFile.parent)
        self.loadedPayload = document.model_dump(mode="json")
        self.loadedDocument = document
        self.loadedProjectId = document.project.projectId
        self._loadedProjectPreviewKey = self.previewAssetStore.projectKey(
            self.loadedProjectPath, self.loadedProjectId
        )
        self.eventStore.retentionPerJob = document.runtime.eventRetentionPerJob
        if self.productionMode:
            self.sqliteStore.jobEventRetention = document.runtime.eventRetentionPerJob
        self.jobSupervisor.maxConcurrentJobs = document.runtime.maxConcurrentJobs
        self.jobSupervisor.gracefulStopTimeoutMs = document.runtime.gracefulStopTimeoutMs
        self.jobSupervisor.heartbeatTimeoutMs = document.runtime.heartbeatTimeoutMs
        return runtime_pb2.LoadProjectReply(ok=True, status="READY", message="project loaded")

    def sqliteProtectedPaths(self):
        """One owner-defined boundary for management, normal runs and preparation."""
        paths = (self.sqliteStore.dbPath, self.workspaceRoot, self.previewAssetStore.root)
        owner = getattr(self, "_presentationOwner", None)
        return (*paths, owner.root) if owner is not None else paths

    def InspectSqliteTarget(self, request, context):  # type: ignore[override]
        from emo_master.apps.runtime.business_sqlite.rpc import managementRpc
        return managementRpc(self, request, context)

    def InitializeSqliteTarget(self, request, context):  # type: ignore[override]
        from emo_master.apps.runtime.business_sqlite.rpc import managementRpc
        return managementRpc(self, request, context, creating=True)

    @_withProjectStateLock
    def ValidateProject(self, request, context):  # type: ignore[override]
        _ = context
        requested = str(getattr(request, "project_id", ""))
        if self.loadedDocument is None or not self._projectMatches(requested):
            return runtime_pb2.ValidateProjectReply(ok=False, errors=["project not loaded"])
        try:
            WorkflowCompiler(operatorRegistry=self.pluginScanResult.activeOperators).compile(
                self.loadedDocument,
                pluginRootPaths=self.pluginRootPaths,
            )
        except WorkflowCompileError as err:
            return runtime_pb2.ValidateProjectReply(
                ok=False, errors=[issue.message for issue in err.issues]
            )
        return runtime_pb2.ValidateProjectReply(ok=True, errors=[])

    @_withProjectStateLock
    def StartJob(self, request, context):  # type: ignore[override]
        requestId = str(getattr(request, "start_request_id", ""))
        expectedInstance = str(getattr(request, "expected_runtime_instance_id", ""))
        # Invalid policy is still fingerprinted/recorded as a conclusive rejection.
        # Empty and explicit ALL are the same immutable request semantics.
        policy = str(getattr(request, "legacy_snapshot_policy", "") or "ALL")
        if requestId:
            if expectedInstance != self.runtimeInstanceId:
                return runtime_pb2.StartJobReply(ok=False, status="RESET_REQUIRED",
                    message="Runtime generation changed; reconcile the earlier run before starting again",
                    runtime_instance_id=self.runtimeInstanceId, start_request_id=requestId)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", requestId):
                return runtime_pb2.StartJobReply(ok=False, status="REJECTED", message="invalid start request ID")
            arguments = {key: getattr(request, key, default) for key, default in (
                ("project_id", ""), ("workflow_id", ""), ("inputs_json", ""),
                ("capture_presentation", False))}
            arguments["legacy_snapshot_policy"] = policy
            inspectionId = str(getattr(request, "inspection_session_id", ""))
            if inspectionId:
                arguments["inspection_session_id"] = inspectionId
            fingerprint = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()
            previous = self._startRequests.get(requestId)
            if previous:
                if previous[0] != fingerprint:
                    return runtime_pb2.StartJobReply(ok=False, status="REJECTED",
                        message="start request ID was already used with different arguments",
                        runtime_instance_id=self.runtimeInstanceId, start_request_id=requestId)
                return self._startReply(requestId)
            if len(self._startRequests) >= 1024:
                return runtime_pb2.StartJobReply(ok=False, status="REJECTED",
                    message="start request ledger quota exceeded; no request IDs were forgotten",
                    runtime_instance_id=self.runtimeInstanceId, start_request_id=requestId)
            # Reserve before any consequential work, including if it raises.
            self._startRequests[requestId] = (fingerprint, runtime_pb2.StartJobReply(
                ok=False, status="UNKNOWN", message="start outcome requires reconciliation"))
        reply = self._startJob(request, context)
        reply.runtime_instance_id = self.runtimeInstanceId
        reply.start_request_id = requestId
        reply.legacy_snapshot_policy = policy if policy in {"ALL", "NONE"} else ""
        if reply.job_id and str(getattr(request, "inspection_session_id", "")):
            reply.project_revision = self.jobRepository.get(reply.job_id).projectRevision
            reply.inspection_session_id = str(getattr(request, "inspection_session_id", ""))
        if requestId:
            if not reply.ok and not reply.job_id:
                reply.status = "REJECTED"
            savedReply = runtime_pb2.StartJobReply()
            savedReply.CopyFrom(reply)
            self._startRequests[requestId] = (fingerprint, savedReply)
            if reply.job_id:
                self._jobStartRequestIds[reply.job_id] = requestId
        return reply

    def _startReply(self, requestId):
        previous = self._startRequests.get(requestId)
        if previous is None:
            return runtime_pb2.StartJobReply(ok=False, status="UNKNOWN",
                message="request not recorded; an in-flight start must not be retried with a new ID",
                runtime_instance_id=self.runtimeInstanceId, start_request_id=requestId)
        reply = runtime_pb2.StartJobReply()
        reply.CopyFrom(previous[1])
        reply.runtime_instance_id = self.runtimeInstanceId
        reply.start_request_id = requestId
        if reply.job_id:
            job = self.jobRepository.get(reply.job_id)
            if job is not None:
                reply.status = job.status
                reply.legacy_snapshot_policy = job.legacySnapshotPolicy
        return reply

    @_withProjectStateLock
    def GetStartRequest(self, request, context):  # type: ignore[override]
        if str(getattr(request, "runtime_instance_id", "")) != self.runtimeInstanceId:
            return runtime_pb2.StartJobReply(ok=False, status="RESET_REQUIRED",
                message="Runtime generation changed; earlier start outcome is unknown",
                runtime_instance_id=self.runtimeInstanceId,
                start_request_id=str(getattr(request, "start_request_id", "")))
        return self._startReply(str(getattr(request, "start_request_id", "")))

    def _startJob(self, request, context):
        _ = context
        try:
            legacyPolicy = normalizeLegacySnapshotPolicy(getattr(request, "legacy_snapshot_policy", ""))
        except ValueError as error:
            return runtime_pb2.StartJobReply(ok=False, status="REJECTED", message=str(error))
        if legacyPolicy == "NONE" and not self.supportsLegacySnapshotPolicy:
            return runtime_pb2.StartJobReply(ok=False, status="REJECTED",
                message="test-release host does not accept normal legacy snapshot policy changes")
        document = self.loadedDocument
        if document is None or not self._projectMatches(str(getattr(request, "project_id", ""))):
            return runtime_pb2.StartJobReply(
                ok=False, job_id="", status="FAILED", message="project not loaded"
            )
        workflowId = str(getattr(request, "workflow_id", "")) or document.entryWorkflowId
        if workflowId not in document.workflows:
            return runtime_pb2.StartJobReply(
                ok=False, job_id="", status="FAILED", message="workflow not found"
            )
        inspectionId = str(getattr(request, "inspection_session_id", ""))
        if inspectionId:
            try:
                self.runInspectionStore.require(inspectionId, self._loadedProjectPreviewKey)
            except ValueError as error:
                return runtime_pb2.StartJobReply(ok=False, status="REJECTED", message=str(error))
        inputsJson = str(getattr(request, "inputs_json", "") or "{}")
        try:
            inputs = json.loads(inputsJson)
            if not isinstance(inputs, dict):
                raise ValueError("inputs_json must contain an object")
        except Exception as err:
            return runtime_pb2.StartJobReply(
                ok=False, job_id="", status="FAILED", message=f"invalid inputs_json: {err}"
            )

        try:
            from emo_master.apps.runtime.business_sqlite.backend import freezeTargets
            if any(n.operatorId == "vision.io.sqlite_writer" for w in document.workflows.values() for n in w.nodes):
                document = self.sqliteManagement.run(lambda cancelled: freezeTargets(document, Path(self.loadedProjectPath),
                    self.sqliteProtectedPaths(), cancelled=cancelled), context)
        except Exception as error:
            return runtime_pb2.StartJobReply(ok=False, status="REJECTED", message=str(error))

        capture = None
        presentation = getattr(self, "_presentationOwner", None)
        if bool(getattr(request, "capture_presentation", False)):
            try:
                if presentation is None:
                    raise ValueError("Runtime does not expose normal presentation capture")
                presentation.checkNormalAdmission()
                from emo_master.apps.runtime.presentation.normal_capture import freezeNormalCapture
                capture = freezeNormalCapture(document, self.pluginScanResult.activeOperators,
                    Path(self.loadedProjectPath), workflowId)
            except (ValueError, OSError) as error:
                return runtime_pb2.StartJobReply(ok=False, status="FAILED", message=str(error))

        with self._previewJobLock:
            previewCleanupErrors = self.livePreviewManager.closeAll(timeoutSeconds=3.0)
            if previewCleanupErrors:
                return runtime_pb2.StartJobReply(
                    ok=False,
                    job_id="",
                    status="FAILED",
                    message=(
                        "E_PREVIEW_RELEASE_FAILED: "
                        + "; ".join(previewCleanupErrors)
                    ),
                )

            record = self.jobManager.createJob(
                projectId=document.project.projectId,
                projectRevision=document.project.revision,
                workflowId=workflowId,
                legacySnapshotPolicy=legacyPolicy,
                previewProjectKey=self._loadedProjectPreviewKey,
            )
            requestId = str(getattr(request, "start_request_id", ""))
            if requestId:
                fingerprint, _ = self._startRequests[requestId]
                self._startRequests[requestId] = (fingerprint, runtime_pb2.StartJobReply(
                    ok=True, job_id=record.jobId, status=record.status, message="job accepted",
                    legacy_snapshot_policy=legacyPolicy))
                self._jobStartRequestIds[record.jobId] = requestId
            self.jobMessages[record.jobId] = "job accepted"
            logFailure = self.operationalLogWriter.failureMessage
            if logFailure:
                self.eventStore.append(
                    jobId=record.jobId,
                    eventType="runtime.logfile.failed",
                    message=logFailure,
                    level="ERROR",
                    code="E_OUTPUT_WRITE_FAILED",
                    projectId=document.project.projectId,
                    workflowId=workflowId,
                    payload={"status": "FAILED", "message": logFailure},
                    notifySinks=False,
                )
            try:
                if inspectionId:
                    self.runInspectionStore.attach(inspectionId, self._loadedProjectPreviewKey, record.jobId, document,
                                                   accepted=False)
                snapshotPath, workspacePath = self._createSnapshot(record.jobId, document)
                self._workspacePaths[record.jobId] = workspacePath
                self._jobPreviewKeys[record.jobId] = self._loadedProjectPreviewKey
                spec = JobProcessSpec(
                    jobId=record.jobId,
                    projectSnapshotPath=str(snapshotPath),
                    workflowId=workflowId,
                    projectId=document.project.projectId,
                    inputsJson=json.dumps(inputs, ensure_ascii=True),
                    pluginRootPaths=tuple(self.pluginRootPaths),
                    jobWorkspacePath=str(workspacePath),
                    heartbeatTimeoutMs=document.runtime.heartbeatTimeoutMs,
                    runtimeDbPath=str(self.sqliteStore.dbPath),
                    legacySnapshotPolicy=legacyPolicy,
                    continuous=(self.productionMode and document.production is not None
                                and document.production.mode == "continuous"),
                    cycleIntervalMs=(document.production.cycleIntervalMs if document.production else 100),
                    copyArtifacts=not self.productionMode,
                )
                if capture is not None:
                    from dataclasses import replace
                    spec = replace(spec, presentation=presentation.attachNormal(record.jobId, capture))
                self.jobManager.start(record, spec)
                if inspectionId:
                    self.runInspectionStore.confirm(inspectionId, self._loadedProjectPreviewKey, record.jobId)
            except Exception as err:
                self.eventStore.append(
                    record.jobId,
                    "job.failed",
                    str(err),
                    level="ERROR",
                    code=(
                        "E_MAX_CONCURRENT_JOBS"
                        if "MAX_CONCURRENT" in str(err)
                        else "E_JOB_START_FAILED"
                    ),
                    projectId=document.project.projectId,
                    workflowId=workflowId,
                )
                errorCode = (
                    "E_MAX_CONCURRENT_JOBS"
                    if "MAX_CONCURRENT" in str(err)
                    else "E_JOB_START_FAILED"
                )
                self.jobRepository.update(
                    record.jobId,
                    status=JobStatus.FAILED.value,
                    endedAtMs=nowMs(),
                    errorCode=errorCode,
                    message=str(err),
                )
                self.jobMessages.pop(record.jobId, None)
                if inspectionId:
                    self.runInspectionStore.abandon(inspectionId, self._loadedProjectPreviewKey, record.jobId)
                self._jobPreviewKeys.pop(record.jobId, None)
                self._removeWorkspace(record.jobId)
                if capture is not None:
                    presentation.terminal(record.jobId, "FAILED")
                return runtime_pb2.StartJobReply(
                    ok=False,
                    job_id=record.jobId,
                    status="FAILED",
                    message=str(err),
                )
        return runtime_pb2.StartJobReply(
            ok=True,
            job_id=record.jobId,
            status=JobStatus.ACCEPTED.value,
            message="job accepted",
        )

    def StopJob(self, request, context):  # type: ignore[override]
        jobId = str(getattr(request, "job_id", ""))
        mode = str(getattr(request, "mode", "graceful")) or "graceful"
        outcome = self.jobManager.stopOutcome(jobId, mode, context)
        return runtime_pb2.StopJobReply(ok=outcome.ok, status=outcome.status, message=outcome.message)

    def GetJobStatus(self, request, context):  # type: ignore[override]
        _ = context
        jobId = str(getattr(request, "job_id", ""))
        record = self.jobRepository.get(jobId)
        if record is None:
            return runtime_pb2.GetJobStatusReply(
                ok=False,
                status="UNKNOWN",
                error_code="E_JOB_NOT_FOUND",
                message="job not found",
            )
        return runtime_pb2.GetJobStatusReply(
            ok=True,
            status=record.status,
            error_code=record.errorCode,
            message=record.message or self.jobMessages.get(jobId, "ok"),
            project_id=record.projectId,
            workflow_id=record.workflowId,
            pid=record.pid or 0,
            accepted_at_ms=record.acceptedAtMs,
            started_at_ms=record.startedAtMs,
            ended_at_ms=record.endedAtMs,
            legacy_snapshot_policy=record.legacySnapshotPolicy,
        )

    def StreamJobEvents(self, request, context):  # type: ignore[override]
        jobId = str(getattr(request, "job_id", ""))
        afterSequence = int(getattr(request, "after_sequence", 0))
        follow = bool(getattr(request, "follow", False))
        events = (
            self.eventStore.follow(
                jobId,
                afterSequence,
                cancellation=context,
                isTerminal=lambda value: (
                    (record := self.jobRepository.get(value)) is not None
                    and record.isTerminal
                ),
            )
            if follow
            else self._eventsAfter(jobId, afterSequence)
        )
        cursor = afterSequence
        terminalSequence = 0
        for event in events:
            if context is not None and hasattr(context, "is_active") and not context.is_active():
                return
            cursor = max(cursor, event.sequence)
            if event.eventType in {"job.completed", "job.failed", "job.aborted"}:
                terminalSequence = event.sequence
            yield self._toProtoEvent(event)
        if not follow:
            return
        if terminalSequence <= 0:
            terminalSequence = self.eventStore.terminalSequence(jobId)
        if terminalSequence <= 0:
            return
        if context is not None and hasattr(context, "is_active") and not context.is_active():
            return
        self.operationalLogWriter.waitUntilProcessed(
            jobId,
            terminalSequence,
            timeoutSeconds=3.0,
        )
        for event in self._eventsAfter(jobId, cursor):
            if context is not None and hasattr(context, "is_active") and not context.is_active():
                return
            cursor = max(cursor, event.sequence)
            yield self._toProtoEvent(event)

    def ListOperators(self, request, context):  # type: ignore[override]
        _ = request
        _ = context
        operators = []
        for operatorId, descriptor in self.pluginScanResult.activeOperators.items():
            if context is not None and not context.is_active():
                break
            icon = descriptor.iconAsset if descriptor.iconStatus == "ready" else None
            editorSpec = (
                asdict(descriptor.manifest.editor)
                if descriptor.manifest.editor is not None
                and not descriptor.editorIssues
                else {}
            )
            operators.append(
                runtime_pb2.OperatorInfo(
                    operator_id=operatorId,
                    display_name=descriptor.manifest.displayName,
                    version=descriptor.manifest.version,
                    input_ports=canonicalPortTypes(descriptor.manifest.inputPorts),
                    output_ports=canonicalPortTypes(descriptor.manifest.outputPorts),
                    input_port_specs_json=json.dumps(
                        descriptor.manifest.inputPorts, ensure_ascii=True
                    ),
                    output_port_specs_json=json.dumps(
                        descriptor.manifest.outputPorts, ensure_ascii=True
                    ),
                    param_schema_json=json.dumps(descriptor.manifest.paramSchema, ensure_ascii=True),
                    category=descriptor.manifest.category,
                    icon_key=descriptor.manifest.iconKey,
                    icon=runtime_pb2.OperatorIconInfo(
                        status=descriptor.iconStatus,
                        mime_type=icon.mimeType if icon else "",
                        sha256=icon.sha256 if icon else "",
                        byte_size=len(icon.content) if icon else 0,
                    ),
                    icon_issues=[runtime_pb2.OperatorIconIssue(
                        rule_id=issue.ruleId, code=issue.code, message=issue.message,
                    ) for issue in descriptor.iconIssues],
                    summary=descriptor.manifest.summary,
                    editor_spec_json=json.dumps(editorSpec, ensure_ascii=True),
                    editor_issues_json=json.dumps(
                        [
                            {
                                "code": issue.code,
                                "message": issue.message,
                                "ruleId": issue.ruleId,
                            }
                            for issue in descriptor.editorIssues
                        ],
                        ensure_ascii=True,
                    ),
                )
            )
        return runtime_pb2.ListOperatorsReply(operators=operators)

    def GetOperatorIconAsset(self, request, context):  # type: ignore[override]
        operatorId = request.operator_id
        version = request.version
        digest = request.expected_sha256
        code = ""
        if (not operatorId or any(c.isspace() or ord(c) < 32 for c in operatorId)
                or re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){0,2}", version) is None
                or re.fullmatch(r"[a-fA-F0-9]{64}", digest) is None):
            code = "E_ICON_REQUEST_INVALID"
        descriptor = self.pluginScanResult.activeOperators.get(operatorId)
        if not code:
            if descriptor is None:
                code = "E_ICON_OPERATOR_NOT_FOUND"
            elif descriptor.manifest.version != version:
                code = "E_ICON_VERSION_MISMATCH"
            elif descriptor.iconStatus != "ready":
                code = "E_ICON_ASSET_UNAVAILABLE"
            elif descriptor.iconAsset.sha256 != digest.lower():
                code = "E_ICON_DIGEST_MISMATCH"
        if code:
            return runtime_pb2.GetOperatorIconAssetReply(ok=False, code=code, message=code)
        asset = descriptor.iconAsset
        return runtime_pb2.GetOperatorIconAssetReply(
            ok=True, content=asset.content, mime_type=asset.mimeType,
            sha256=asset.sha256, version=descriptor.manifest.version,
        )

    def GetOperatorEditorAsset(self, request, context):  # type: ignore[override]
        _ = context
        operatorId = str(getattr(request, "operator_id", ""))
        requestedVersion = str(getattr(request, "version", ""))
        descriptor = self.pluginScanResult.activeOperators.get(operatorId)
        if descriptor is None:
            return runtime_pb2.GetOperatorEditorAssetReply(
                ok=False, message="operator not found"
            )
        editor = descriptor.manifest.editor
        if (
            editor is None
            or descriptor.editorIssues
            or descriptor.editorUiContent is None
            or requestedVersion not in {"", descriptor.manifest.version}
        ):
            return runtime_pb2.GetOperatorEditorAssetReply(
                ok=False, message="operator editor is unavailable"
            )
        content = descriptor.editorUiContent
        return runtime_pb2.GetOperatorEditorAssetReply(
            ok=True,
            content=content,
            sha256=descriptor.editorUiSha256,
            message="ok",
        )

    @_withProjectStateLock
    def OpenRunInspectionSession(self, request, context):
        return self._inspectionSessionReply(request, "open")

    @_withProjectStateLock
    def RenewRunInspectionSession(self, request, context):
        return self._inspectionSessionReply(request, "renew")

    @_withProjectStateLock
    def CloseRunInspectionSession(self, request, context):
        return self._inspectionSessionReply(request, "close")

    def _inspectionSessionReply(self, request, action):
        sessionId = str(getattr(request, "session_id", ""))
        try:
            if action == "open":
                if self.loadedDocument is None or not self._projectMatches(str(request.project_id)):
                    raise ValueError("E_PROJECT_NOT_LOADED: requested project copy is not loaded")
                references = (self.loadedProjectId, self.loadedProjectPath,
                              str(Path(self.loadedProjectPath or "") / "project.json"))
                sessionId = self.runInspectionStore.open(self._loadedProjectPreviewKey,
                    (self._inspectionReference(value) for value in references if value))
            else:
                key = self.runInspectionStore.keyForReference(sessionId, self._inspectionReference(request.project_id))
                if action == "renew":
                    self.runInspectionStore.renew(sessionId, key)
                else:
                    self.runInspectionStore.closeSession(sessionId, key)
            return runtime_pb2.RunInspectionSessionReply(ok=True, session_id=sessionId,
                runtime_instance_id=self.runtimeInstanceId, ttl_ms=30000)
        except ValueError as error:
            return runtime_pb2.RunInspectionSessionReply(ok=False, code=str(error).split(":")[0], message=str(error))

    @staticmethod
    def _inspectionReference(value):
        if not str(value):
            raise ValueError("E_INSPECTION_IDENTITY: an explicit project copy reference is required")
        return os.path.normcase(str(Path(str(value)).resolve()))

    def ListNodePreviewSources(self, request, context):  # type: ignore[override]
        _ = context
        projectId = str(getattr(request, "project_id", ""))
        workflowId = str(getattr(request, "workflow_id", ""))
        nodeId = str(getattr(request, "node_id", ""))
        jobId = str(getattr(request, "job_id", ""))
        inspectionId = str(getattr(request, "inspection_session_id", ""))
        if inspectionId:
            # Frozen Job definitions may differ from the currently edited draft.
            with self._projectStateLock:
                try:
                    key = self.runInspectionStore.keyForReference(inspectionId, self._inspectionReference(projectId))
                    assets, state, message = self.runInspectionStore.list(
                        inspectionId, key, jobId, workflowId, nodeId)
                except ValueError as error:
                    return runtime_pb2.ListNodePreviewSourcesReply(capture_state="EXPIRED", message=str(error))
                job = self.jobRepository.get(jobId)
                sources = [runtime_pb2.PreviewSourceInfo(source_id=asset.assetId,
                    label=asset.port, source_kind="current", workflow_id=asset.workflowId, node_id=asset.nodeId,
                    port=asset.port, width=asset.width, height=asset.height, mime_type=asset.mimeType,
                    origin_job_id=asset.originJobId, origin_project_revision=asset.originProjectRevision,
                    capture_id=asset.captureId, workflow_run_id=asset.workflowRunId, node_run_id=asset.nodeRunId,
                    created_at_ms=asset.createdAtMs, iteration_path_json=json.dumps(list(asset.iterationPath)),
                    snapshot_state="CURRENT") for asset in assets]
                return runtime_pb2.ListNodePreviewSourcesReply(sources=sources, job_id=jobId,
                    legacy_snapshot_policy=job.legacySnapshotPolicy if job else "UNKNOWN",
                    capture_state=state, message=message)
        if (self.loadedDocument is None or not self._projectMatches(projectId)
                or workflowId not in self.loadedDocument.workflows):
            return runtime_pb2.ListNodePreviewSourcesReply(capture_state="INVALID_JOB",
                message="工程或流程不可用，未选择当前任务")
        workflow = self.loadedDocument.workflows[workflowId]
        if not any(node.nodeId == nodeId for node in workflow.nodes):
            return runtime_pb2.ListNodePreviewSourcesReply(capture_state="INVALID_JOB",
                message="节点不属于当前工程")
        job = self.jobRepository.get(jobId) if jobId else None
        if jobId and (job is None or job.projectId != self.loadedDocument.project.projectId
                      or (job.previewProjectKey and job.previewProjectKey != self._loadedProjectPreviewKey)):
            return runtime_pb2.ListNodePreviewSourcesReply(job_id=jobId, capture_state="INVALID_JOB",
                message="所选任务不存在或不属于当前工程副本")
        verified = job is not None and job.previewProjectKey == self._loadedProjectPreviewKey
        policy = job.legacySnapshotPolicy if job is not None else "UNKNOWN"
        incoming = [(edge.fromNode, edge.fromPort) for edge in workflow.edges
                    if edge.toNode == nodeId and edge.toPort == "image"]
        assets = self.previewAssetStore.listSources(self._loadedProjectPreviewKey, workflowId, nodeId, incoming)
        sources = []
        for asset in assets:
            state = "UNKNOWN" if not asset.originJobId or not asset.captureId else "PREVIOUS"
            if (verified and policy == "ALL" and asset.originJobId == jobId
                    and asset.originProjectRevision == job.projectRevision and asset.captureId):
                state = "CURRENT"
            location = f"当前节点 {asset.port}" if asset.nodeId == nodeId else f"上游 {asset.nodeId}.{asset.port}"
            origin = (f"本次任务 {jobId[:8]}" if state == "CURRENT" else
                      f"历史快照 · 任务 {asset.originJobId[:8]} · 工程修订 {asset.originProjectRevision}"
                      if state == "PREVIOUS" else "历史快照 · 来源未知")
            sources.append(runtime_pb2.PreviewSourceInfo(
                source_id=asset.assetId, label=f"{location} · {origin}",
                source_kind="current" if asset.nodeId == nodeId else "upstream",
                workflow_id=asset.workflowId, node_id=asset.nodeId, port=asset.port,
                width=asset.width, height=asset.height, mime_type=asset.mimeType,
                iteration_path_json=json.dumps(list(asset.iterationPath)),
                origin_job_id=asset.originJobId, origin_project_revision=asset.originProjectRevision,
                capture_id=asset.captureId, created_at_ms=asset.createdAtMs, snapshot_state=state,
                workflow_run_id=asset.workflowRunId, node_run_id=asset.nodeRunId))
        if not jobId:
            state, message = "NO_JOB_SELECTED", "尚未选择任务；历史快照需明确选择"
        elif not verified or policy not in {"ALL", "NONE"}:
            state, message = "UNKNOWN", "任务快照策略或工程副本身份无法核实；仅提供历史快照"
        elif policy == "NONE":
            state, message = "DISABLED_THIS_RUN", "本次运行未采集节点调试快照；运行页面的绑定采集不受影响"
        elif any(source.snapshot_state == "CURRENT" for source in sources):
            state, message = "CURRENT_AVAILABLE", "已提供所选任务的节点调试快照"
        else:
            state, message = "NO_CURRENT_SNAPSHOT", "本次尚无可用快照；可能未执行、尚未发布或执行失败"
        return runtime_pb2.ListNodePreviewSourcesReply(sources=sources, job_id=jobId,
            legacy_snapshot_policy=policy, capture_state=state, message=message)

    @_withProjectStateLock
    def UploadPreviewImage(self, request_iterator, context):  # type: ignore[override]
        _ = context
        chunks: list[bytes] = []
        total = 0
        filename = ""
        requestedProjectId = ""
        try:
            for chunk in request_iterator:
                data = bytes(getattr(chunk, "content", b""))
                total += len(data)
                if total > 64 * 1024 * 1024:
                    raise ValueError("preview upload exceeds 64 MiB")
                chunks.append(data)
                if not filename:
                    filename = str(getattr(chunk, "filename", ""))
                chunkProjectId = str(getattr(chunk, "project_id", ""))
                if chunkProjectId:
                    if requestedProjectId and requestedProjectId != chunkProjectId:
                        raise ValueError("preview upload project_id changed between chunks")
                    requestedProjectId = chunkProjectId
            if self.loadedDocument is None or not self._projectMatches(requestedProjectId):
                raise ValueError("preview upload project is not loaded")
            asset = self.previewAssetStore.addUploadedImage(
                b"".join(chunks),
                filename,
                projectKey=self._loadedProjectPreviewKey,
            )
        except Exception as err:
            return runtime_pb2.PreviewAssetReply(
                ok=False,
                code="E_PREVIEW_ASSET_INVALID",
                message=str(err),
            )
        return runtime_pb2.PreviewAssetReply(
            ok=True,
            asset_id=asset.assetId,
            message="ok",
            width=asset.width,
            height=asset.height,
            mime_type=asset.mimeType,
        )

    def StreamPreviewAsset(self, request, context):  # type: ignore[override]
        assetId = str(getattr(request, "asset_id", ""))
        requestedProjectId = str(getattr(request, "project_id", ""))
        inspectionId = str(getattr(request, "inspection_session_id", ""))
        if inspectionId:
            active = getattr(context, "is_active", lambda: True)
            try:
                with self._projectStateLock:
                    projectKey = self.runInspectionStore.keyForReference(inspectionId, self._inspectionReference(requestedProjectId))
                yield from (runtime_pb2.PreviewDownloadChunk(asset_id=assetId, content=chunk, mime_type=mime)
                            for chunk, mime in self.runInspectionStore.stream(inspectionId, projectKey, assetId, active))
            except (KeyError, ValueError, OSError) as error:
                if context is not None:
                    context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(error))
                raise
            return
        with self._projectStateLock:
            if self.loadedDocument is None or not self._projectMatches(requestedProjectId):
                return
            projectKey = self._loadedProjectPreviewKey
            try:
                content, mimeType = self.previewAssetStore.readBytes(
                    assetId, projectKey=projectKey
                )
            except (KeyError, OSError):
                return
        for offset in range(0, len(content), 256 * 1024):
            isActive = getattr(context, "is_active", None)
            if callable(isActive) and not isActive():
                return
            yield runtime_pb2.PreviewDownloadChunk(
                asset_id=assetId,
                mime_type=mimeType,
                content=content[offset : offset + 256 * 1024],
            )

    @_withProjectStateLock
    def RunOperatorPreview(self, request, context):  # type: ignore[override]
        projectId = str(getattr(request, "project_id", ""))
        workflowId = str(getattr(request, "workflow_id", ""))
        nodeId = str(getattr(request, "node_id", ""))
        operatorId = str(getattr(request, "operator_id", ""))
        nodeError = self._validatePreviewNode(
            projectId, workflowId, nodeId, operatorId
        )
        if nodeError:
            return runtime_pb2.RunOperatorPreviewReply(
                ok=False, code="E_PREVIEW_CONTEXT_INVALID", message=nodeError
            )
        imageAssetId = str(getattr(request, "image_asset_id", ""))
        if not self.previewAssetStore.isOwnedByProject(
            imageAssetId, self._loadedProjectPreviewKey
        ):
            return runtime_pb2.RunOperatorPreviewReply(
                ok=False,
                code="E_PREVIEW_SOURCE_NOT_FOUND",
                message="preview image is unavailable for the loaded project",
            )
        params, parseError = parsePreviewParams(str(getattr(request, "params_json", "")))
        if params is None:
            return runtime_pb2.RunOperatorPreviewReply(
                ok=False, code="E_PARAM_INVALID", message=parseError or "invalid parameters"
            )
        requestId = str(getattr(request, "request_id", "")) or str(uuid4())
        addCallback = getattr(context, "add_callback", None)
        if callable(addCallback):
            addCallback(lambda: self.previewExecutor.cancel(requestId))
        result = self.previewExecutor.execute(
            operatorId,
            params,
            imageAssetId,
            projectId=self.loadedProjectId,
            projectKey=self._loadedProjectPreviewKey,
            workflowId=workflowId,
            nodeId=nodeId,
            requestId=requestId,
        )
        return runtime_pb2.RunOperatorPreviewReply(
            ok=result.ok,
            code=result.code,
            message=result.message or ("ok" if result.ok else "preview failed"),
            outputs_json=json.dumps(result.outputs or {}, ensure_ascii=True),
            assets=[
                runtime_pb2.PreviewOutputAsset(
                    port=output.port,
                    asset_id=output.asset.assetId,
                    mime_type=output.asset.mimeType,
                    width=output.asset.width,
                    height=output.asset.height,
                )
                for output in result.assets
            ],
        )

    def CancelOperatorPreview(self, request, context):  # type: ignore[override]
        _ = context
        requestId = str(getattr(request, "request_id", ""))
        if not requestId:
            return runtime_pb2.CancelOperatorPreviewReply(
                ok=False,
                code="E_PARAM_INVALID",
                message="request_id is required",
            )
        cancelled = self.previewExecutor.cancel(requestId)
        return runtime_pb2.CancelOperatorPreviewReply(
            ok=True,
            message="cancel requested" if cancelled else "request is not active",
        )

    @_withProjectStateLock
    def OpenOperatorPreviewSession(self, request, context):  # type: ignore[override]
        _ = context
        requestedProjectId = str(getattr(request, "project_id", ""))
        workflowId = str(getattr(request, "workflow_id", ""))
        nodeId = str(getattr(request, "node_id", ""))
        operatorId = str(getattr(request, "operator_id", ""))
        nodeError = self._validatePreviewNode(
            requestedProjectId, workflowId, nodeId, operatorId
        )
        if nodeError:
            return runtime_pb2.OpenOperatorPreviewSessionReply(
                ok=False, code="E_PREVIEW_CONTEXT_INVALID", message=nodeError
            )
        params, parseError = parsePreviewParams(str(getattr(request, "params_json", "")))
        if params is None:
            return runtime_pb2.OpenOperatorPreviewSessionReply(
                ok=False, code="E_PARAM_INVALID", message=parseError or "invalid parameters"
            )
        with self._previewJobLock:
            if any(
                record.projectId == self.loadedProjectId and not record.isTerminal
                for record in self.jobRepository.all()
            ):
                return runtime_pb2.OpenOperatorPreviewSessionReply(
                    ok=False,
                    code="E_RESOURCE_BUSY",
                    message="a project job is active",
                )
            sessionId, error = self.livePreviewManager.open(
                operatorId,
                self.loadedProjectId,
                workflowId,
                nodeId,
                params,
            )
        if sessionId is None:
            return runtime_pb2.OpenOperatorPreviewSessionReply(
                ok=False, code="E_PREVIEW_SESSION_OPEN_FAILED", message=error or "open failed"
            )
        return runtime_pb2.OpenOperatorPreviewSessionReply(
            ok=True, session_id=sessionId, message="ok"
        )

    @_withProjectStateLock
    def OpenDraftOperatorPreviewSession(self, request, context):  # type: ignore[override]
        projectId, error = draftPreviewProjectId(request)
        if projectId is None:
            return runtime_pb2.OpenOperatorPreviewSessionReply(
                ok=False, code="E_PREVIEW_CONTEXT_INVALID", message=error or "当前草稿无效"
            )
        params, error = parsePreviewParams(str(getattr(request, "params_json", "")))
        if params is None:
            return runtime_pb2.OpenOperatorPreviewSessionReply(
                ok=False, code="E_PARAM_INVALID", message=error or "预览参数无效"
            )
        isActive = getattr(context, "is_active", lambda: True)
        with self._previewJobLock:
            if any(not record.isTerminal or self.jobSupervisor.ownsJobResources(record.jobId)
                   for record in self.jobRepository.all()):
                return runtime_pb2.OpenOperatorPreviewSessionReply(
                    ok=False, code="E_RESOURCE_BUSY", message="Runtime 任务仍占用设备，请等待任务及资源释放后再预览"
                )
            if not isActive():
                return runtime_pb2.OpenOperatorPreviewSessionReply(
                    ok=False, code="E_CANCELLED", message="相机预览请求已取消"
                )
            sessionId, error = self.livePreviewManager.open(
                str(request.operator_id), projectId, str(request.workflow_id), str(request.node_id), params,
            )
            if sessionId is not None and not isActive():
                error = self.livePreviewManager.close(sessionId)
                return runtime_pb2.OpenOperatorPreviewSessionReply(
                    ok=False, code="E_PREVIEW_RELEASE_FAILED" if error else "E_CANCELLED",
                    message=error or "相机预览请求已取消，设备已释放",
                )
        if sessionId is None:
            return runtime_pb2.OpenOperatorPreviewSessionReply(
                ok=False, code="E_PREVIEW_SESSION_OPEN_FAILED", message=error or "相机预览打开失败"
            )
        return runtime_pb2.OpenOperatorPreviewSessionReply(ok=True, session_id=sessionId, message="ok")


    def StreamOperatorPreviewFrames(self, request, context):  # type: ignore[override]
        sessionId = str(getattr(request, "session_id", ""))
        try:
            for frame in self.livePreviewManager.stream(sessionId, context):
                yield runtime_pb2.OperatorPreviewFrame(
                    session_id=frame.sessionId,
                    jpeg=frame.jpeg,
                    sequence=frame.sequence,
                    block_id=frame.blockId,
                    device_timestamp=frame.deviceTimestamp,
                    actual_exposure_us=frame.actualExposureUs,
                    width=frame.width,
                    height=frame.height,
                )
        except RuntimeError as err:
            abort = getattr(context, "abort", None)
            if callable(abort):
                abort(grpc.StatusCode.FAILED_PRECONDITION, str(err))
            raise

    def CloseOperatorPreviewSession(self, request, context):  # type: ignore[override]
        _ = context
        error = self.livePreviewManager.close(
            str(getattr(request, "session_id", "")), timeoutSeconds=3.0
        )
        return runtime_pb2.CloseOperatorPreviewSessionReply(
            ok=error is None,
            code="" if error is None else "E_PREVIEW_RELEASE_FAILED",
            message="ok" if error is None else error,
        )

    def ListRejectedOperators(self, request, context):  # type: ignore[override]
        _ = request
        _ = context
        rejected = []
        for operatorId, issues in self.pluginScanResult.rejectedOperators.items():
            for issue in issues:
                rejected.append(
                    runtime_pb2.RejectedOperatorInfo(
                        operator_id=operatorId, code=issue.code, message=issue.message
                    )
                )
        return runtime_pb2.ListRejectedOperatorsReply(rejected=rejected)

    @_withProjectStateLock
    def ListWorkflows(self, request, context):  # type: ignore[override]
        _ = context
        if self.loadedDocument is None or not self._projectMatches(str(getattr(request, "project_id", ""))):
            return runtime_pb2.ListWorkflowsReply(workflows=[])
        workflows = []
        for workflowId in self.loadedDocument.workflowOrder:
            workflow = self.loadedDocument.workflows[workflowId]
            workflows.append(
                runtime_pb2.WorkflowInfo(
                    workflow_id=workflowId,
                    name=workflow.name,
                    is_entry=workflowId == self.loadedDocument.entryWorkflowId,
                    inputs_json=json.dumps(workflow.inputs, ensure_ascii=True),
                    outputs_json=json.dumps(workflow.outputs, ensure_ascii=True),
                )
            )
        return runtime_pb2.ListWorkflowsReply(workflows=workflows)

    @_withProjectStateLock
    def ListGlobalCounters(self, request, context):  # type: ignore[override]
        _ = context
        projectError = self._globalCounterProjectError(
            str(getattr(request, "project_id", ""))
        )
        if projectError is not None:
            return runtime_pb2.ListGlobalCountersReply(
                ok=False,
                code=projectError[0],
                message=projectError[1],
            )
        try:
            records = self.sqliteStore.listGlobalCounters(self.loadedProjectId)
        except Exception as err:
            code, message = _globalCounterFailure(err)
            return runtime_pb2.ListGlobalCountersReply(
                ok=False,
                code=code,
                message=message,
            )
        return runtime_pb2.ListGlobalCountersReply(
            ok=True,
            message="ok",
            counters=[_globalCounterInfo(record) for record in records],
        )

    @_withProjectStateLock
    def GetGlobalCounter(self, request, context):  # type: ignore[override]
        _ = context
        projectError = self._globalCounterProjectError(
            str(getattr(request, "project_id", ""))
        )
        if projectError is not None:
            return runtime_pb2.GetGlobalCounterReply(
                ok=False,
                code=projectError[0],
                message=projectError[1],
            )
        try:
            record = self.sqliteStore.getGlobalCounter(
                self.loadedProjectId,
                str(getattr(request, "name", "")),
            )
        except Exception as err:
            code, message = _globalCounterFailure(err)
            return runtime_pb2.GetGlobalCounterReply(
                ok=False,
                code=code,
                message=message,
            )
        return runtime_pb2.GetGlobalCounterReply(
            ok=True,
            message="ok",
            counter=_globalCounterInfo(record),
        )

    @_withProjectStateLock
    def SetGlobalCounter(self, request, context):  # type: ignore[override]
        _ = context
        projectError = self._globalCounterProjectError(
            str(getattr(request, "project_id", ""))
        )
        if projectError is not None:
            return runtime_pb2.SetGlobalCounterReply(
                ok=False,
                code=projectError[0],
                message=projectError[1],
            )
        try:
            record = self.sqliteStore.setGlobalCounter(
                self.loadedProjectId,
                str(getattr(request, "name", "")),
                int(getattr(request, "value", 0)),
            )
        except Exception as err:
            code, message = _globalCounterFailure(err)
            return runtime_pb2.SetGlobalCounterReply(
                ok=False,
                code=code,
                message=message,
            )
        return runtime_pb2.SetGlobalCounterReply(
            ok=True,
            message="ok",
            counter=_globalCounterInfo(record),
        )

    @_withProjectStateLock
    def ResetGlobalCounter(self, request, context):  # type: ignore[override]
        _ = context
        projectError = self._globalCounterProjectError(
            str(getattr(request, "project_id", ""))
        )
        if projectError is not None:
            return runtime_pb2.ResetGlobalCounterReply(
                ok=False,
                code=projectError[0],
                message=projectError[1],
            )
        try:
            record = self.sqliteStore.resetGlobalCounter(
                self.loadedProjectId,
                str(getattr(request, "name", "")),
            )
        except Exception as err:
            code, message = _globalCounterFailure(err)
            return runtime_pb2.ResetGlobalCounterReply(
                ok=False,
                code=code,
                message=message,
            )
        return runtime_pb2.ResetGlobalCounterReply(
            ok=True,
            message="ok",
            counter=_globalCounterInfo(record),
        )

    @_withProjectStateLock
    def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        self.jobSupervisor.beginClosing()
        presentation = getattr(self, "_presentationOwner", None)
        if presentation is not None:
            presentation.beginClosing()
        self._maintenanceStop.set()
        errors = list(self.livePreviewManager.closeAll())
        self._closeStep("preview-producers", self.previewExecutor.close)
        self._closeStep("sqlite-management", self.sqliteManagement.close, retryable=True)
        deadline = time.monotonic() + 2.0
        primary = None
        try:
            self.jobSupervisor.shutdown(deadline=deadline)
        except BaseException as error:
            primary = error
        errors.extend(self.jobSupervisor.recoverFinalizations())
        self.jobSupervisor.waitForRetirement(deadline=deadline)
        errors.extend(self.jobSupervisor.finishRetirements())
        with self.previewExecutor._stateLock:
            if self.previewExecutor._cancellations:
                errors.append("Preview work still owns Runtime assets")
        with self.jobSupervisor._lock:
            if self.jobSupervisor._ownedJobs():
                errors.append("Worker still owns Runtime resources; shutdown is incomplete")
        if primary is not None:
            raise primary
        if errors:
            raise RuntimeError("; ".join(errors))
        # No callback, producer, process or IPC owner can write these stores now.
        if presentation is not None:
            presentation.close()
        self._closeStep("inspection-sink", lambda: self.eventStore.removeSink(self.runInspectionStore.observe))
        self._closeStep("inspection-assets", self.runInspectionStore.close, retryable=True)
        self._closeStep("preview-assets", self.previewAssetStore.close)
        self._closeStep("event-sink", lambda: self.eventStore.removeSink(self._operationalLogSink))
        def closeWriter():
            writerError = self.operationalLogWriter.close(timeoutSeconds=3.0)
            if writerError:
                self._recordLogFileFailure(None, writerError)
                raise RuntimeError(writerError)
        self._closeStep("log-writer", closeWriter, retryable=True)
        if self._maintenanceThread.is_alive():
            self._maintenanceThread.join(timeout=1.0)
            if self._maintenanceThread.is_alive():
                raise RuntimeError("Runtime maintenance still owns persistence; shutdown is incomplete")
        for jobId in list(self._workspacePaths):
            self._removeWorkspace(jobId)
        self._cleanupStaleWorkspaces()
        self._closeStep("sqlite", self.sqliteStore.releaseIdleConnection, retryable=True)
        self._closeStep("data-lock", self._runtimeDataLock.release)
        self._closed = True

    def _closeStep(self, name, action, *, retryable=False):
        state = self._closeStages.get(name)
        if state == "DONE":
            return
        if state is not None and not retryable:
            raise RuntimeError("incomplete close step with unknown outcome: " + name + ": " + state)
        self._closeStages[name] = "STARTED"
        try:
            action()
        except BaseException as error:
            self._closeStages[name] = faultSummary(error)
            raise
        self._closeStages[name] = "DONE"

    def _eventMaintenanceLoop(self) -> None:
        while not self._maintenanceStop.wait(6 * 60 * 60):
            if not self._runtimeDataLockIsPrimary:
                continue
            try:
                self.sqliteStore.pruneTerminalJobEvents(
                    retentionDays=30,
                    minimumJobsPerProject=100,
                )
                self.sqliteStore.checkpointWal()
            except BaseException as err:
                self._recordMaintenanceFailure(
                    f"runtime event maintenance failed: {err}",
                )

    def _recordMaintenanceFailure(self, message: str) -> None:
        try:
            self.eventStore.append(
                jobId="__runtime__",
                eventType="runtime.event_retention.failed",
                message=str(message),
                level="ERROR",
                code="E_EVENT_PERSISTENCE",
                payload={"status": "FAILED", "message": str(message)},
                notifySinks=False,
            )
        except BaseException:
            pass

    def _recordLogFileFailure(
        self,
        sourceEvent: RuntimeEvent | None,
        message: str,
    ) -> None:
        try:
            self.eventStore.append(
                jobId=sourceEvent.jobId if sourceEvent is not None else "__runtime__",
                eventType="runtime.logfile.failed",
                message=str(message),
                level="ERROR",
                code="E_OUTPUT_WRITE_FAILED",
                nodeId=sourceEvent.nodeId if sourceEvent is not None else "",
                projectId=sourceEvent.projectId if sourceEvent is not None else "",
                workflowId=sourceEvent.workflowId if sourceEvent is not None else "",
                workflowRunId=(
                    sourceEvent.workflowRunId if sourceEvent is not None else ""
                ),
                parentWorkflowRunId=(
                    sourceEvent.parentWorkflowRunId if sourceEvent is not None else ""
                ),
                nodeRunId=sourceEvent.nodeRunId if sourceEvent is not None else "",
                iterationPath=(
                    _safeIterationPath(sourceEvent.iterationPathJson)
                    if sourceEvent is not None
                    else ()
                ),
                payload={"status": "FAILED", "message": str(message)},
                notifySinks=False,
            )
        except BaseException:
            pass

    def _eventsAfter(self, jobId: str, afterSequence: int) -> list[RuntimeEvent]:
        return self.eventStore.readMerged(jobId, afterSequence)

    def _toProtoEvent(self, event: RuntimeEvent):
        return runtime_pb2.JobEvent(
            job_id=event.jobId,
            node_id=event.nodeId,
            event_type=event.eventType,
            level=event.level,
            code=event.code,
            message=event.message,
            payload_json=event.payloadJson,
            timestamp_ms=event.timestampMs,
            sequence=event.sequence,
            project_id=event.projectId,
            workflow_id=event.workflowId,
            workflow_run_id=event.workflowRunId,
            parent_workflow_run_id=event.parentWorkflowRunId,
            node_run_id=event.nodeRunId,
            iteration_path_json=event.iterationPathJson,
        )

    def _createSnapshot(self, jobId: str, document: ProjectDocument) -> tuple[Path, Path]:
        workspace = self.workspaceRoot / jobId
        workspace.mkdir(parents=True, exist_ok=True)
        snapshot = workspace / "project.json"
        snapshot.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        return snapshot, workspace

    def _onJobTerminal(self, jobId: str, status: str) -> None:
        self.jobMessages.pop(jobId, None)
        previewKey = self._jobPreviewKeys.pop(jobId, "")
        workspace = self._workspacePaths.get(jobId, self.workspaceRoot / jobId)
        self.runInspectionStore.terminal(jobId, workspace, status)
        if status == JobStatus.COMPLETED.value and previewKey:
            try:
                self.previewAssetStore.promote(
                    workspace / "preview_staging", previewKey
                )
            except Exception as err:
                self.eventStore.append(
                    jobId,
                    "preview.snapshot.failed",
                    str(err),
                    level="WARN",
                    code="E_PREVIEW_SNAPSHOT_FAILED",
                )
        if status in {JobStatus.FAILED.value, JobStatus.ABORTED.value}:
            self._removeWorkspace(jobId)

    def _removeWorkspace(self, jobId: str) -> None:
        if self.jobSupervisor.ownsJobResources(jobId):
            return
        workspace = self._workspacePaths.get(jobId, self.workspaceRoot / jobId)
        self._removeOwnedWorkspace(jobId, workspace)

    def _removeOwnedWorkspace(self, jobId: str, workspace: Path) -> None:
        if self._workspacePaths.get(jobId) == workspace:
            self._workspacePaths.pop(jobId, None)
        try:
            if workspace.exists():
                shutil.rmtree(workspace)
        except OSError:
            # Preserve existing deferred stale-workspace cleanup semantics.
            return

    def _onJobRetired(self, jobId: str) -> None:
        record = self.jobRepository.get(jobId)
        if record is not None and record.status in {JobStatus.FAILED.value, JobStatus.ABORTED.value}:
            workspace = self._workspacePaths.get(jobId, self.workspaceRoot / jobId)
            self._removeOwnedWorkspace(jobId, workspace)

    def _retirementRepair(self, jobId, callback):
        if callback != self._onJobRetired:
            return None
        record = self.jobRepository.get(jobId)
        if record is None or record.status not in {JobStatus.FAILED.value, JobStatus.ABORTED.value}:
            return None
        workspace = self._workspacePaths.get(jobId, self.workspaceRoot / jobId)
        return lambda: self._removeOwnedWorkspace(jobId, workspace)

    def _cleanupStaleWorkspaces(self) -> None:
        if not self.workspaceRoot.exists():
            return
        for workspace in self.workspaceRoot.iterdir():
            if not workspace.is_dir():
                continue
            record = self.jobRepository.get(workspace.name)
            if record is None or record.isTerminal:
                try:
                    shutil.rmtree(workspace)
                except OSError:
                    continue

    def _scanBuiltins(
        self,
        pluginRootPaths: tuple[str, ...] | None,
    ) -> RegistryScanResult:
        roots = pluginRootPaths or (str(self._builtinsRoot()),)
        return PluginRegistry(coreVersion=__version__).scanRoots(
            Path(root) for root in roots
        )

    def _builtinsRoot(self) -> Path:
        return Path(__file__).resolve().parents[3] / "plugins"

    def _resolveProjectFile(self, projectPath: Path) -> Path | None:
        if projectPath.is_file() and projectPath.name.lower() == "project.json":
            return projectPath
        if projectPath.is_dir():
            candidate = projectPath / "project.json"
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _projectMatches(self, requested: str) -> bool:
        if requested == "":
            return True
        return requested in {
            self.loadedProjectId,
            self.loadedProjectPath or "",
            str(Path(self.loadedProjectPath or "") / "project.json"),
        }

    def _globalCounterProjectError(self, requested: str) -> tuple[str, str] | None:
        if (
            self.loadedDocument is None
            or requested == ""
            or not self._projectMatches(requested)
        ):
            return "E_PROJECT_NOT_LOADED", "requested project is not loaded"
        return None

    def _validatePreviewNode(
        self,
        requestedProjectId: str,
        workflowId: str,
        nodeId: str,
        operatorId: str,
    ) -> str | None:
        document = self.loadedDocument
        if document is None or not self._projectMatches(requestedProjectId):
            return "project is not loaded"
        workflow = document.workflows.get(workflowId)
        if workflow is None:
            return "workflow is not loaded"
        node = next((item for item in workflow.nodes if item.nodeId == nodeId), None)
        if node is None:
            return "node is not part of the workflow"
        if str(node.operatorId) != operatorId:
            return "operator does not match the workflow node"
        return None

    def _clearLoadedProject(self, message: str) -> None:
        if self.loadedProjectId:
            self.livePreviewManager.closeProject(self.loadedProjectId)
        self.loadedProjectPath = None
        self.loadedProjectId = ""
        self._loadedProjectPreviewKey = ""
        self.loadedDocument = None
        self.loadedPayload = None
        self.jobMessages["__load__"] = message


def _defaultDbPath() -> Path:
    configured = os.environ.get("EMO_RUNTIME_DB_PATH") or os.environ.get(
        "EMO_MASTER_RUNTIME_DB_PATH"
    )
    if configured:
        return Path(configured).expanduser()
    return _defaultDataDir() / "emo_master.db"


def _defaultDataDir() -> Path:
    configured = os.environ.get("EMO_RUNTIME_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".emo_master" / "runtime"


def _defaultWorkspaceRoot() -> Path:
    return _defaultDataDir() / "jobs"


def _defaultLogDirectory(dbPath: Path, *, explicitDbPath: bool) -> Path:
    configured = os.environ.get("EMO_RUNTIME_LOG_DIR")
    if configured:
        return Path(configured).expanduser()
    if explicitDbPath:
        return dbPath.parent / "logs"
    return _defaultDataDir() / "logs"


def _safeIterationPath(value: str) -> tuple[int, ...]:
    try:
        parsed = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(
        item
        for item in parsed
        if isinstance(item, int) and not isinstance(item, bool)
    )


def _globalCounterInfo(record: GlobalCounterRecord):
    return runtime_pb2.GlobalCounterInfo(
        name=record.name,
        value=record.value,
        updated_at_ms=record.updatedAtMs,
    )


def _globalCounterFailure(error: Exception) -> tuple[str, str]:
    if isinstance(error, GlobalCounterError):
        return error.code, str(error)
    return (
        E_RUNTIME_STATE_UNAVAILABLE,
        f"global counter state is unavailable: {error}",
    )
