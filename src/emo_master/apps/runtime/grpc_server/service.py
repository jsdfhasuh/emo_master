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
from typing import Any, Callable
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
from emo_master.apps.runtime.preview.pure_draft import PureDraftPreviewSessions, DraftPreviewError
from emo_master.apps.runtime.preview.store import PreviewAssetStore, PreviewTransientCleanupPending
from emo_master.apps.runtime.preview.run_inspection import RunInspectionStore
from emo_master.apps.runtime.preview.plc_debug import PLC_OPERATORS, PlcDebugManager, parseParams as parsePlcDebugParams
from emo_master.apps.runtime.operator_debug.rpc import OperatorDebugRpcMixin
from emo_master.apps.runtime.communication.manager import CommunicationManager
from emo_master.apps.runtime.communication.rpc import CommunicationRpcMixin
from emo_master.apps.runtime.project_config.rpc import ProjectConfigurationRpcMixin
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as _runtime_pb2
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.core.contracts.port_types import canonicalPortTypes
from emo_master.core.contracts.legacy_snapshots import normalizeLegacySnapshotPolicy
from emo_master.core.plugin.models import RegistryScanResult
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.tcp_dependencies import communicationNodes
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.project.devices import workflowDeviceIds
from emo_master.core.workflow.errors import WorkflowCompileError

runtime_pb2: Any = _runtime_pb2


def _withProjectStateLock(method: Any) -> Any:
    @wraps(method)
    def locked(self: Any, *args: Any, **kwargs: Any) -> Any:
        if method.__name__ == "close":
            configuration = getattr(self, "projectConfiguration", None)
            if configuration is not None:
                configuration.close()
        self.jobSupervisor.assertMutationAllowed()
        with self._projectStateLock:
            debugReplies = {
                "LoadProject": runtime_pb2.LoadProjectReply,
                "RunOperatorPreview": runtime_pb2.RunOperatorPreviewReply,
                "OpenOperatorPreviewSession": runtime_pb2.OpenOperatorPreviewSessionReply,
                "OpenDraftOperatorPreviewSession": runtime_pb2.OpenOperatorPreviewSessionReply,
                "OpenDraftPurePreviewSession": runtime_pb2.OpenOperatorPreviewSessionReply,
            }
            if method.__name__ in debugReplies and self.operatorDebugManager.ownsResources():
                values = dict(ok=False, message="E_RESOURCE_BUSY: operator debug still holds Runtime resources")
                if method.__name__ == "LoadProject":
                    values.update(status="FAILED")
                else:
                    values.update(code="E_RESOURCE_BUSY")
                return debugReplies[method.__name__](**values)
            if self._closing and method.__name__ in {
                "LoadProject", "StartJob", "UploadPreviewImage", "RunOperatorPreview", "OpenOperatorPreviewSession",
                "OpenDraftOperatorPreviewSession", "OpenDraftPurePreviewSession",
                "OpenRunInspectionSession", "RenewRunInspectionSession"
            }:
                raise RuntimeError("E_RUNTIME_CLOSING")
            return method(self, *args, **kwargs)

    return locked


class RuntimeService(ProjectConfigurationRpcMixin, CommunicationRpcMixin, OperatorDebugRpcMixin, runtime_pb2_grpc.RuntimeServiceServicer):
    @property
    def supportsLegacySnapshotPolicy(self) -> bool:
        # The opt-in normal-Run policy must not widen restricted test hosts.
        owner = getattr(self, "_presentationOwner", None)
        return bool(getattr(owner, "supportsNormalCapture", True))

    def __init__(
        self,
        dbPath: Path | None = None,
        pluginRootPaths: tuple[str, ...] | None = None,
        maxConcurrentJobs: int | None = 2,
        workspaceRoot: Path | None = None,
        logDirectory: Path | None = None,
        productionMode: bool = False,
        startupProgress: Callable[[str], None] | None = None,
    ) -> None:
        report = startupProgress or (lambda _stage: None)
        report("runtime-database")
        self._startedMonotonic = time.monotonic()
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
                report("runtime-maintenance")
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
        report("runtime-logs")
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
        try:
            report("runtime-workspaces")
            self._cleanupStaleWorkspaces()
            report("runtime-plugins")
            self.pluginScanResult = self._scanBuiltins(pluginRootPaths)
            self.pluginRootPaths = pluginRootPaths or (str(self._builtinsRoot()),)
            report("runtime-preview")
            self.previewAssetStore = PreviewAssetStore(
                self.workspaceRoot.parent / "preview-cache"
            )
        except BaseException:
            # No Job/device owners or retained SQLite connection exist yet.
            # A normal cache-directory initialization failure must not strand
            # the writer, maintenance thread or data lock before an explicit retry.
            self._maintenanceStop.set()
            self._maintenanceThread.join()
            self.operationalLogWriter.close()
            self._runtimeDataLock.release()
            raise
        report("runtime-managers")
        self.runInspectionStore = RunInspectionStore(self.previewAssetStore,
            maxSessions=None if maxConcurrentJobs is None else max(2, maxConcurrentJobs))
        self.eventStore.addSink(self.runInspectionStore.observe)
        self.previewExecutor = PurePreviewExecutor(
            self.pluginScanResult.activeOperators,
            self.previewAssetStore,
        )
        self.pureDraftPreviewSessions = PureDraftPreviewSessions(
            self.previewAssetStore, self.pluginScanResult.activeOperators)
        self.livePreviewManager = LivePreviewManager(
            self.pluginScanResult.activeOperators,
            eventPublisher=self.eventStore.append,
            journalRoot=self.workspaceRoot.parent / "camera-adjustments",
        )
        self._jobPreviewKeys: dict[str, str] = {}
        self._loadedProjectPreviewKey = ""
        self._projectStateLock = threading.RLock()
        self._previewJobLock = threading.RLock()
        self.runtimeInstanceId = str(uuid4())
        self.communicationManager = CommunicationManager(self.runtimeInstanceId)
        from emo_master.apps.runtime.devices.manager import DeviceManager
        self.deviceManager = DeviceManager(self.pluginScanResult.deviceTypes,
            operatorRequirements={key: descriptor.manifest.deviceRequirements
                for key, descriptor in self.pluginScanResult.activeOperators.items()})
        self.deviceManager.runtimeInstanceId = self.runtimeInstanceId
        self.deviceManager.scope['runtimeInstanceId'] = self.runtimeInstanceId
        self.deviceManager.diagnostics.writer = self.operationalLogWriter
        self.deviceManager.diagnostics.eventPublisher = self.eventStore.append
        self.eventStore.addSink(self.deviceManager.diagnostics.projectEvent)
        from emo_master.apps.runtime.devices.imv_debug import CameraDiagnostics
        self.cameraDiagnostics = CameraDiagnostics(self.deviceManager)
        self._communicationRequests: dict = {}
        self.jobSupervisor = JobSupervisor(
            self.jobRepository,
            self.eventStore,
            maxConcurrentJobs=maxConcurrentJobs,
            heartbeatTimeoutMs=5000,
            terminalCallback=self._onJobTerminal,
            retiredCallback=self._onJobRetired,
            communication=self.communicationManager,
            devices=self.deviceManager,
        )
        self.jobSupervisor.retirementRepairFactory = self._retirementRepair
        self.jobSupervisor.inspectionCallback = self._onInspectionSnapshot
        self.jobManager = JobManager(self.jobRepository, self.eventStore, self.jobSupervisor)
        self.loadedProjectPath: str | None = None
        self.loadedProjectFile: str | None = None
        self.loadedProjectId: str = ""
        self.loadedDocument: ProjectDocument | None = None
        self.loadedPayload: dict[str, object] | None = None
        self.jobMessages: dict[str, str] = {}
        self.plcDebugManager = PlcDebugManager(self.runtimeInstanceId)
        from emo_master.apps.runtime.operator_debug.contracts import trustedEntries
        from emo_master.apps.runtime.operator_debug.manager import OperatorDebugManager
        self.operatorDebugManager = OperatorDebugManager(self.runtimeInstanceId,
            trustedEntries(self.pluginScanResult.activeOperators), resourceBusy=self._operatorDebugResourcesBusy,
            sourceProvider=self._operatorDebugSources, variableProvider=self._operatorDebugVariables)
        # Never evict a request and then treat a delayed retry as a fresh run.
        # Bounded admission is reset only by a new Runtime generation.
        self._startRequests: dict[str, tuple[str, object]] = {}
        self._jobStartRequestIds: dict[str, str] = {}
        # Open only after the Runtime's owners are fully initialized, so a
        # failed acquisition can use the normal, retryable shutdown path.
        try:
            report("runtime-online-configuration")
            self.sqliteStore.retainIdleConnection()
            self._initializeOnlineConfiguration()
        except BaseException:
            self.close()
            raise

    @_withProjectStateLock
    def LoadProject(self, request, context):  # type: ignore[override]
        if self.projectConfiguration.active:
            return runtime_pb2.LoadProjectReply(ok=False, status='FAILED',
                message='E_CONFIG_WRITER_BUSY: another configuration write is pending')
        pins = []
        try:
            return self._loadInitialProject(request, context, pins)
        finally:
            for token in pins:
                self.projectResourceTransfers.unpin(token)

    def InitializeProjectConfiguration(self, request, context):
        """First publication shares the queryable, cancellable configuration writer."""
        from emo_master.apps.runtime.project_config.store import ConfigurationError

        def initialize():
            self.jobSupervisor.assertMutationAllowed()
            payload = json.loads(request.local_payload_json)
            if not isinstance(payload, dict) or payload.get('schemaVersion') not in {'2.6', '2.7'}:
                raise ConfigurationError('E_CONFIG_INVALID', '显式在线配置写入需要当前 2.7 格式工程')
            root = Path(request.resource_root).resolve(strict=True) if request.resource_root else (
                self.workspaceRoot.parent / 'online-projects' / hashlib.sha256(
                    payload['project']['projectId'].encode('utf-8')).hexdigest())
            if request.resource_root and not root.is_dir():
                raise ConfigurationError('E_CONFIG_INVALID', '资源根目录不是文件夹')
            source = Path(request.project_path).resolve() if request.project_path else root / 'project.emoproj'
            if source.parent != root:
                raise ConfigurationError('E_CONFIG_INVALID', '工程来源文件必须与声明的资源目录一致')
            payload = ProjectDocument.model_validate(payload).model_dump(mode='json')
            if request.validate_only:
                self._validateInitialProjectConfiguration(payload, str(source))
                return dict(state='VALIDATED', runtimeInstanceId=self.runtimeInstanceId,
                            message='完整首次候选校验通过；未写入、未连接设备、未启动任务')
            return self.projectConfiguration.start(dict(operation='initialize', requestId=request.request_id,
                expectedRuntimeInstanceId=request.expected_runtime_instance_id, expectedRevision=0,
                localPayload=payload, sourcePath=str(source)))
        return self._configurationReply(initialize)

    def _loadInitialProject(self, request, context, pins):
        _ = context
        if self.projectConfiguration.current is not None:
            return runtime_pb2.LoadProjectReply(ok=False, status="FAILED",
                message="E_CONFIG_ACTIVE: online configuration exists; compare and explicitly write selected changes")
        if any(not record.isTerminal or self.jobSupervisor.ownsJobResources(record.jobId)
               for record in self.jobRepository.all()):
            return runtime_pb2.LoadProjectReply(ok=False, status="FAILED",
                message="stop and release running Jobs before reloading the project")
        # A draft preview can exist before any formal project has been loaded.
        with self._previewJobLock:
            if self.livePreviewManager.hasConfigurationSession():
                return runtime_pb2.LoadProjectReply(ok=False, status="FAILED",
                    message="E_RESOURCE_BUSY: close the camera configuration session before loading a project")
            cleanupErrors = self.livePreviewManager.closeAll(timeoutSeconds=3.0)
            cleanupErrors.extend(self.plcDebugManager.closeSessions())
        if cleanupErrors:
            return runtime_pb2.LoadProjectReply(
                ok=False, status="FAILED",
                message="E_PREVIEW_RELEASE_FAILED: " + "; ".join(cleanupErrors),
            )
        self.pureDraftPreviewSessions.closeAll()
        projectPathRaw = str(getattr(request, "project_path", ""))
        from emo_master.core.project.files import resolveProjectFile
        try:
            projectFile = resolveProjectFile(Path(projectPathRaw))
        except (OSError, ValueError) as err:
            self._failedProjectCandidate(str(err))
            return runtime_pb2.LoadProjectReply(
                ok=False,
                status="FAILED",
                message=str(err),
            )
        try:
            rawPayload = json.loads(projectFile.read_text(encoding="utf-8"))
            if not isinstance(rawPayload, dict):
                raise ValueError("project file must contain an object")
            canonical = migrateProjectPayload(rawPayload)
            document = ProjectDocument.model_validate(canonical)
            authoritativePayload = document.model_dump(mode="json")
            if self.productionMode or document.schemaVersion in {"2.6", "2.7"}:
                from emo_master.core.project.runtime_directory import prepareRuntimeProject
                if document.schemaVersion in {"2.6", "2.7"}:
                    pins.append(self.projectResourceTransfers.pin(document, projectFile.parent))
                document = prepareRuntimeProject(document, projectFile.parent,
                    self.pluginScanResult.activeOperators,
                    deviceTypes=self.pluginScanResult.deviceTypes,
                    resourcePaths=self.projectResourceTransfers.resolve(document),
                    protectedPaths=(*self.sqliteProtectedPaths(), self.operationalLogWriter.directory))
                if document.schemaVersion in {"2.6", "2.7"}:
                    from threading import Event
                    from emo_master.apps.runtime.project_config.resources import prepareResources
                    document, _resources = prepareResources(document, self.pluginScanResult.activeOperators,
                        self.pluginScanResult.deviceTypes, self.workspaceRoot.parent / "configuration-resources", Event(),
                        journal=self.configurationResourceJournal)
                    self._ownedConfigurationResources.update(_resources)
            WorkflowCompiler(operatorRegistry=self.pluginScanResult.activeOperators,
                             deviceTypes=self.pluginScanResult.deviceTypes).compile(
                document,
                pluginRootPaths=self.pluginRootPaths,
            )
            if any(record.projectId == document.project.projectId and not record.isTerminal
                   for record in self.jobRepository.all()):
                if self.loadedDocument is None or self.loadedDocument.globalVariables != document.globalVariables:
                    raise ValueError("E_VARIABLE_DEFINITION_BUSY: variable definitions cannot change during a job")
            from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
            ProjectGlobalVariables(self.sqliteStore, document.project.projectId, document.globalVariables).synchronize()
        except WorkflowCompileError as err:
            self._reclaimConfigurationResources()
            self._failedProjectCandidate(str(err))
            # LoadProject has a textual error contract. Retain the compiler's
            # existing code/location instead of discarding them via str(err),
            # so a startup chooser can explain which legacy node needs repair.
            details = []
            for issue in err.issues:
                location = "/".join(filter(None, (issue.workflowId, issue.nodeId, issue.fieldPath))) or "project"
                details.append(f"{issue.code} [{location}]: {issue.message}")
            return runtime_pb2.LoadProjectReply(ok=False, status="FAILED", message="\n".join(details) or str(err))
        except Exception as err:
            self._reclaimConfigurationResources()
            self._failedProjectCandidate(str(err))
            return runtime_pb2.LoadProjectReply(
                ok=False, status="FAILED", message=f"invalid project file: {err}"
            )

        try:
            self.deviceManager.load(document)
            if document.schemaVersion != '2.7':
                self.communicationManager.load(document, os.path.normcase(str(projectFile.resolve())))
        except Exception as error:
            return runtime_pb2.LoadProjectReply(ok=False, status="FAILED", message=str(error))
        self.loadedProjectPath = str(projectFile.parent)
        self.loadedProjectFile = str(projectFile)
        self.loadedPayload = authoritativePayload if document.schemaVersion in {"2.6", "2.7"} else document.model_dump(mode="json")
        self.loadedDocument = document
        self.loadedProjectId = document.project.projectId
        self._loadedProjectPreviewKey = self.previewAssetStore.projectKey(
            self.loadedProjectFile if projectFile.suffix.lower() == ".emoproj" else self.loadedProjectPath,
            self.loadedProjectId,
        )
        self.eventStore.retentionPerJob = document.runtime.eventRetentionPerJob
        self.sqliteStore.jobEventRetention = document.runtime.eventRetentionPerJob
        self.jobSupervisor.maxConcurrentJobs = document.runtime.maxConcurrentJobs
        # Designer owns one inspection lease per visited workflow (each retains
        # its last two runs). Do not impose a hidden two-Job execution limit.
        # Per-session and aggregate byte/reader budgets remain unchanged.
        self.runInspectionStore.maxSessions = (None if document.runtime.maxConcurrentJobs is None
            else max(2, len(document.workflows), document.runtime.maxConcurrentJobs))
        self.jobSupervisor.gracefulStopTimeoutMs = document.runtime.gracefulStopTimeoutMs
        self.jobSupervisor.heartbeatTimeoutMs = document.runtime.heartbeatTimeoutMs
        if document.schemaVersion in {"2.6", "2.7"}:
            try:
                self.projectConfiguration.current = self.projectConfiguration.store.initialize(
                    authoritativePayload, str(projectFile.resolve()))
            except Exception as error:
                self.onlineRecoveryError = str(error)
                return runtime_pb2.LoadProjectReply(ok=False, status="FAILED",
                    message="E_CONFIG_PERSISTENCE: " + str(error))
        self.deviceManager.activate()
        return runtime_pb2.LoadProjectReply(ok=True, status="READY", message="project loaded")

    def sqliteProtectedPaths(self):
        """One owner-defined boundary for management, normal runs and preparation."""
        paths = (self.sqliteStore.dbPath, self.workspaceRoot, self.previewAssetStore.root)
        if self.loadedProjectFile:
            paths = (*paths, Path(self.loadedProjectFile))
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
            WorkflowCompiler(operatorRegistry=self.pluginScanResult.activeOperators,
                             deviceTypes=self.pluginScanResult.deviceTypes).compile(
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
            if getattr(request, "expected_document_revision", 0):
                arguments["expected_document_revision"] = request.expected_document_revision
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
        expected = getattr(request, "expected_document_revision", 0)
        if expected and (self.projectConfiguration.current is None
                         or expected != self.projectConfiguration.current["documentRevision"]
                         or request.expected_runtime_instance_id != self.runtimeInstanceId):
            return runtime_pb2.StartJobReply(ok=False, status="REJECTED",
                message="E_CONFIG_CONFLICT: online snapshot changed; compare again before starting")
        if (self.onlineRecoveryError or self.projectConfiguration.active or self.projectConfiguration.closed
                or self.projectConfiguration.publicationError):
            return runtime_pb2.StartJobReply(ok=False, status="REJECTED",
                message="E_CONFIG_NOT_READY: configuration write/recovery must finish before starting a new Job")
        # Reject before inspecting production targets or preparing presentation capture.
        with self._previewJobLock:
            if self.livePreviewManager.hasConfigurationSession():
                return runtime_pb2.StartJobReply(ok=False, status="FAILED",
                    message="E_RESOURCE_BUSY: close the camera configuration session before starting a workflow")
            if self.operatorDebugManager.ownsResources():
                return runtime_pb2.StartJobReply(ok=False, status="FAILED",
                    message="E_RESOURCE_BUSY: operator debug still holds Runtime resources")
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
            return runtime_pb2.StartJobReply(ok=False, status="REJECTED", message=str(error),
                diagnostics_json=json.dumps(dict(code=getattr(error, 'code', 'E_SQLITE_TARGET'),
                    projectSource=self.loadedProjectFile, projectDirectory=self.loadedProjectPath), ensure_ascii=False))

        capture = None
        consumerCaptures = {}
        presentation = getattr(self, "_presentationOwner", None)
        if bool(getattr(request, "capture_presentation", False)):
            try:
                if presentation is None:
                    raise ValueError("Runtime does not expose normal presentation capture")
                presentation.checkNormalAdmission()
                from emo_master.apps.runtime.presentation.normal_capture import freezeNormalCapture
                capture = freezeNormalCapture(document, self.pluginScanResult.activeOperators,
                    Path(self.loadedProjectPath), workflowId,
                    resourcePaths=self.projectResourceTransfers.resolve(document),
                    counterNames=frozenset(record.name for record in self.sqliteStore.listGlobalCounters(document.project.projectId)))
                from emo_master.core.workflow.dispatch_contracts import groupSlots
                from emo_master.core.presentation.workflow_view import presentationForWorkflow
                for consumerId, _capacity in set(groupSlots(document, workflowId).values()):
                    if presentationForWorkflow(document, consumerId, capture=True).pages:
                        consumerCaptures[consumerId] = freezeNormalCapture(document, self.pluginScanResult.activeOperators,
                            Path(self.loadedProjectPath), consumerId,
                            resourcePaths=self.projectResourceTransfers.resolve(document),
                            counterNames=frozenset(record.name for record in self.sqliteStore.listGlobalCounters(document.project.projectId)))
            except (ValueError, OSError) as error:
                diagnostics = {}
                if isinstance(error, WorkflowCompileError):
                    diagnostics['issues'] = [asdict(issue) for issue in error.issues]
                    # Use the rejected Runtime document, never the Designer's
                    # possibly different draft, to label the affected files.
                    if document.resources is not None:
                        diagnostics['resources'] = {key: dict(path=item.path, purpose=item.purpose)
                            for key, item in document.resources.items.items()}
                if getattr(error, 'code', ''):
                    diagnostics['code'] = error.code
                return runtime_pb2.StartJobReply(ok=False, status="FAILED", message=str(error),
                    diagnostics_json=json.dumps(diagnostics, ensure_ascii=True) if diagnostics else '')

        with self._previewJobLock:
            if self.operatorDebugManager.ownsResources():
                return runtime_pb2.StartJobReply(ok=False, status="FAILED",
                    message="E_RESOURCE_BUSY: operator debug still holds Runtime resources")
            previewCleanupErrors = self.livePreviewManager.closeAll(timeoutSeconds=3.0)
            previewCleanupErrors.extend(self.plcDebugManager.closeSessions(legacyOnly=True)
                if document.schemaVersion in {"2.6", "2.7"} else self.plcDebugManager.closeSessions())
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

            try:
                from emo_master.core.workflow.dispatch_contracts import groupSlots
                dispatchSlots = groupSlots(document, workflowId)
                with self.jobSupervisor._lock:
                    limit = self.jobSupervisor.maxConcurrentJobs
                    active = len(self.jobSupervisor._ownedJobs())
                    required = 1 + len(dispatchSlots)
                    if limit is not None and active + required > limit:
                        capacity = dict(requiredJobs=required, consumerJobs=len(dispatchSlots),
                            activeJobs=active, maxConcurrentJobs=limit, availableJobs=max(0, limit - active))
                        return runtime_pb2.StartJobReply(ok=False, status="REJECTED",
                            message=(f"E_MAX_CONCURRENT_JOBS: required={required} "
                                     f"(1 entry + {len(dispatchSlots)} consumers), active={active}, "
                                     f"limit={limit}, available={capacity['availableJobs']}"),
                            diagnostics_json=json.dumps(dict(code="E_MAX_CONCURRENT_JOBS", capacity=capacity)))
                self.deviceManager.preflight(document, workflowId)
            except Exception as error:
                return runtime_pb2.StartJobReply(ok=False, status="REJECTED",
                    message=f"{getattr(error, 'code', 'E_TCP_PREFLIGHT')}: {error}")
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
                    usesCommunication=bool(communicationNodes(document, workflowId)),
                    deviceIds=workflowDeviceIds(document, workflowId),
                    managedConfiguration=self.projectConfiguration.current is not None,
                    planRevision=(self.projectConfiguration.current["executionRevision"] if self.projectConfiguration.current else 0),
                    legacySnapshotPolicy=legacyPolicy,
                    inspectionEnabled=bool(inspectionId),
                    inspectionCredits=(self.runInspectionStore.prepareReceiver(record.jobId, workspacePath,
                        lambda error: self._inspectionFailed(record.jobId, error)) if inspectionId else None),
                    continuous=(self.productionMode and document.production is not None
                                and document.production.mode == "continuous"),
                    cycleIntervalMs=(document.production.cycleIntervalMs if document.production else 100),
                    copyArtifacts=not self.productionMode,
                    dispatchSlots=tuple((key, workflow, capacity) for key, (workflow, capacity) in dispatchSlots.items()),
                )
                if capture is not None:
                    from dataclasses import replace
                    spec = replace(spec, presentation=presentation.attachNormal(record.jobId, capture))
                if consumerCaptures:
                    def attachConsumer(child):
                        frozen = consumerCaptures.get(child.workflowId)
                        return presentation.attachNormal(child.jobId, frozen) if frozen is not None else None
                    self.jobManager.start(record, spec, consumerPresentation=attachConsumer)
                else:
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
        presentationSequence = 0
        presentationLogs = {"presentation.source.unavailable", "presentation.source.recovered"}
        for event in events:
            if context is not None and hasattr(context, "is_active") and not context.is_active():
                return
            cursor = max(cursor, event.sequence)
            if event.eventType in {"job.completed", "job.failed", "job.aborted"}:
                terminalSequence = event.sequence
            if event.eventType in presentationLogs:
                presentationSequence = max(presentationSequence, event.sequence)
            yield self._toProtoEvent(event)
        if not follow:
            return
        if terminalSequence <= 0:
            terminalSequence = self.eventStore.terminalSequence(jobId)
        if terminalSequence <= 0:
            return
        if context is not None and hasattr(context, "is_active") and not context.is_active():
            return
        # Terminal events describe execution, not resource release. Keep the
        # follow observer alive until its bridge finishes so Designer cannot
        # offer a new draft-backed Run while LoadProject still rejects the old
        # owner. This never waits for unrelated Jobs or performs cleanup.
        self.jobSupervisor.waitForRetirement(timeoutSeconds=None, jobId=jobId, cancellation=context)
        if context is not None and hasattr(context, "is_active") and not context.is_active():
            return
        # Display results use a separate reader/export path and may close after
        # the worker's terminal event. Include its final diagnostics in this
        # stream, without holding EventStore's append lock or waiting on other Jobs.
        drain = getattr(getattr(self, "_presentationOwner", None), "waitForLogDrain", None)
        if callable(drain):
            drain(jobId, timeoutSeconds=2.0, isActive=lambda: (
                context is None or not hasattr(context, "is_active") or context.is_active()))
        if context is not None and hasattr(context, "is_active") and not context.is_active():
            return
        presentationSequence = max(presentationSequence, max(
            (event.sequence for event in self._eventsAfter(jobId, cursor) if event.eventType in presentationLogs),
            default=0))
        self.operationalLogWriter.waitUntilProcessed(
            jobId,
            max(terminalSequence, presentationSequence),
            timeoutSeconds=3.0,
        )
        for event in self._eventsAfter(jobId, cursor):
            if context is not None and hasattr(context, "is_active") and not context.is_active():
                return
            cursor = max(cursor, event.sequence)
            yield self._toProtoEvent(event)

    def ListDeviceTypes(self, request, context):
        scan = self.pluginScanResult
        return runtime_pb2.ListDeviceTypesReply(
            types_json=json.dumps({key: value.model_dump() for key, value in scan.deviceTypes.items()}),
            requirements_json=json.dumps({key: {slot: value.model_dump() for slot, value in
                descriptor.manifest.deviceRequirements.items()} for key, descriptor in scan.activeOperators.items()
                if descriptor.manifest.deviceRequirements}),
            rejected_json=json.dumps({key: [asdict(issue) for issue in issues]
                for key, issues in scan.rejectedDeviceTypes.items()}))

    @_withProjectStateLock
    def GetDeviceStates(self, request, context):
        uptimeMs = max(0, int((time.monotonic() - self._startedMonotonic) * 1000))
        if request.project_id != self.loadedProjectId or self.loadedDocument is None:
            return runtime_pb2.GetDeviceStatesReply(ok=False, runtime_instance_id=self.runtimeInstanceId,
                project_id=self.loadedProjectId, states_json="{}", message="Runtime 未加载此工程",
                runtime_uptime_ms=uptimeMs,
                code='E_CONFIG_NOT_LOADED' if self.projectConfiguration.current is None else 'E_CONFIG_PROJECT')
        with self.deviceManager.lock:
            from emo_master.core.project.snapshots import revisionOf
            states = {key: dict(self.deviceManager.activitySnapshot(key),
                               definitionRevision=revisionOf(self.deviceManager.definitions[key].model_dump()))
                      for key, executor in self.deviceManager.executors.items()}
            for key, driver in self.deviceManager.drivers.items():
                definition = self.deviceManager.definitions[key]
                states[key].update(deviceTypeId=definition.deviceTypeId, name=definition.name,
                                   enabled=definition.enabled,
                                   connectionLifetime=self.deviceManager.types[definition.deviceTypeId].connectionLifetime,
                                   connectionAddress={field: definition.connection[field]
                                       for field in ('host', 'port', 'bindAddress', 'bindHost', 'cameraKey', 'serial')
                                       if field in definition.connection})
                snapshot = getattr(driver, "snapshot", None)
                if callable(snapshot):
                    states[key]['driverState'] = snapshot()
                states[key].update(self.deviceManager.lifecycles[key].snapshot())
                states[key]['productionOwners'] = self.deviceManager.productionOwners(key)
                discovery = self.deviceNetwork.discovery
                if discovery:
                    selector = self.deviceManager.definitions[key].connection.get('cameraKey')
                    matches = [row for row in discovery[1]['devices'] if selector and row.get('cameraKey') == selector]
                    if len(matches) == 1:
                        states[key]['hardwareInfo'] = matches[0]
                replacement = self.deviceManager.replacement
                if replacement is not None and key in replacement.changed:
                    states[key]["admissionState"] = "REPLACING"
                states[key].update(self.deviceManager.scope)
                states[key]['availableControls'] = self.deviceManager.availableControls(key)
                states[key]['availableManagementActions'] = self.deviceManager.availableManagementActions(key)
                states[key]['rpcBudgetsMs'] = self.deviceManager.rpcBudgetsMs(key)
        return runtime_pb2.GetDeviceStatesReply(ok=True, runtime_instance_id=self.runtimeInstanceId,
            project_id=self.loadedProjectId, states_json=json.dumps(states),
            runtime_uptime_ms=uptimeMs,
            project_load_generation=self.deviceManager.scope['projectLoadGeneration'])

    @_withProjectStateLock
    def GetDeviceDiagnostics(self, request, context):
        scope = self.deviceManager.scope
        if (request.runtime_instance_id != scope['runtimeInstanceId'] or request.project_id != scope['projectId']
                or request.project_load_generation != scope['projectLoadGeneration']
                or request.device_id not in self.deviceManager.definitions):
            return runtime_pb2.GetDeviceDiagnosticsReply(ok=False, code='E_DEVICE_SCOPE',
                message='Runtime、工程或设备已变化，请刷新设备状态')
        return runtime_pb2.GetDeviceDiagnosticsReply(ok=True, diagnostics_json=json.dumps(
            self.deviceManager.diagnostics.query(scope, request.device_id, max(0, request.cursor)), ensure_ascii=True))

    def ControlDevice(self, request, context):
        from emo_master.core.project.snapshots import revisionOf
        try:
            with self._projectStateLock:
                if (not request.confirmed or self._closing or request.runtime_instance_id != self.runtimeInstanceId
                        or request.project_id != self.loadedProjectId
                        or self.projectConfiguration.active and request.action not in {'disconnect', 'maintenance'}
                        or self.onlineRecoveryError or self.projectConfiguration.publicationError):
                    raise ValueError("control is unconfirmed or Runtime/project configuration is not ready")
                definition = self.deviceManager.definitions.get(request.device_id)
                if definition is None or revisionOf(definition.model_dump()) != request.expected_definition_revision:
                    raise ValueError("device definition changed; read state again")
                executor, operation = self.deviceManager.beginControl(
                    request.device_id, request.expected_generation, request.action,
                    expectedControlRevision=request.expected_control_revision)
            # Native cancellation/cleanup must not hold the project lock: stop,
            # status and other devices remain available throughout maintenance.
            executor.wait(operation, lambda: context is not None and not context.is_active())
            return self.GetDeviceStates(runtime_pb2.GetDeviceStatesRequest(project_id=request.project_id), context)
        except (ValueError, RuntimeError) as error:
            return runtime_pb2.GetDeviceStatesReply(ok=False, runtime_instance_id=self.runtimeInstanceId,
                project_id=self.loadedProjectId, states_json="{}", message=getattr(error, "code", "E_DEVICE_CONTROL") + ": " + str(error))

    def ExecuteDeviceAction(self, request, context):
        """A declared device-page action uses the same admission and cycle as Jobs."""
        from uuid import uuid4
        from emo_master.core.project.snapshots import revisionOf
        from emo_master.apps.runtime.devices.diagnostics import compactResult
        manager = self.deviceManager
        owner = 'device-page-' + uuid4().hex
        admitted = False
        try:
            if len(request.arguments_json.encode()) > 64 * 1024 or not 1 <= len(request.request_id) <= 160:
                raise ValueError('device action arguments or request ID exceed limits')
            arguments = json.loads(request.arguments_json or '{}')
            with self._projectStateLock, manager.lock:
                if (not request.confirmed or self._closing or not self._runtimeDataLockIsPrimary
                        or request.runtime_instance_id != self.runtimeInstanceId
                        or request.project_id != self.loadedProjectId or self.projectConfiguration.active
                        or self.onlineRecoveryError or self.projectConfiguration.publicationError):
                    raise ValueError('device action is unconfirmed or Runtime/project is not ready')
                self.jobSupervisor.assertMutationAllowed()
                definition = manager.definitions.get(request.device_id)
                executor = manager.executors.get(request.device_id)
                if (definition is None or executor is None or executor.generation != request.expected_generation
                        or revisionOf(definition.model_dump()) != request.expected_definition_revision
                        or manager.lifecycles[request.device_id].snapshot()['controlRevision'] != request.expected_control_revision):
                    raise ValueError('device identity or control state changed; read online state again')
                if request.action not in manager.availableManagementActions(request.device_id):
                    raise ValueError('device action is not declared or device admission is unavailable')
                manager.authorizeMaintenance(owner, request.device_id)
                admitted = True
                cycle = self.projectConfiguration.beginCycle(owner, -1,
                    lambda: context is not None and not context.is_active())
            result = manager.execute(owner, request.device_id, request.action, definition.defaults,
                cycleId=cycle['cycleId'], arguments=arguments, requestId=request.request_id,
                cancelled=lambda: context is not None and not context.is_active(),
                source=dict(kind='management', client='device-page'))
            return runtime_pb2.ExecuteDeviceActionReply(ok=True, result_json=json.dumps(compactResult(result)))
        except (ValueError, RuntimeError) as error:
            return runtime_pb2.ExecuteDeviceActionReply(ok=False,
                code=getattr(error, 'code', 'E_DEVICE_ACTION'), message=str(error),
                result_json=json.dumps(dict(outcome=getattr(error, 'outcome', 'UNKNOWN'),
                    diagnostics=getattr(error, 'diagnostics', {}))))
        finally:
            if admitted:
                manager.revoke(owner)
                # An abandoned RPC is not proof that its native call exited.
                # Keep the diagnostic owner and configuration lease until it does.
                with executor.condition:
                    executor.condition.wait_for(lambda: (executor.active is None or executor.active.owner != owner)
                        and not any(operation.owner == owner for operation in executor.queue))
                manager.retire(owner)

    @_withProjectStateLock
    def RecoverDevice(self, request, context):
        from emo_master.core.project.snapshots import revisionOf
        try:
            if (self._closing or request.runtime_instance_id != self.runtimeInstanceId
                    or request.project_id != self.loadedProjectId or self.projectConfiguration.active
                    or self.onlineRecoveryError or self.projectConfiguration.publicationError):
                raise ValueError("Runtime/project changed or configuration is not ready")
            definition = self.deviceManager.definitions.get(request.device_id)
            if definition is None or revisionOf(definition.model_dump()) != request.expected_definition_revision:
                raise ValueError("device definition changed; read state again")
            if definition.deviceTypeId == "device.camera.huaray_imv" and not request.hardware_synchronized:
                raise ValueError("confirm external triggers are stopped and camera acquisition is synchronized before recovery")
            self.deviceManager.recover(request.device_id, request.expected_generation)
            return self.GetDeviceStates(runtime_pb2.GetDeviceStatesRequest(project_id=request.project_id), context)
        except (ValueError, RuntimeError) as error:
            return runtime_pb2.GetDeviceStatesReply(ok=False, runtime_instance_id=self.runtimeInstanceId,
                project_id=self.loadedProjectId, states_json="{}", message=str(error))

    def CameraDiagnostic(self, request, context):
        from emo_master.core.project.snapshots import revisionOf
        try:
            if len(request.changes_json.encode()) > 4096:
                raise ValueError('camera trial parameters exceed limit')
            changes = json.loads(request.changes_json or '{}')
            if not isinstance(changes, dict) or (request.action != 'trial_capture' and changes):
                raise ValueError('only trial capture accepts parameter changes')
            with self._projectStateLock:
                if request.runtime_instance_id != self.runtimeInstanceId or request.project_id != self.loadedProjectId:
                    raise ValueError("Runtime/project identity changed")
                session = self.cameraDiagnostics.sessions.get(request.session_id)
                if request.action == "close":
                    if session is not None:
                        session.close()
                    return runtime_pb2.CameraDiagnosticReply(ok=True, session_id=request.session_id)
                if (self._closing or self.projectConfiguration.active or self.onlineRecoveryError
                        or self.projectConfiguration.publicationError):
                    raise ValueError("Runtime configuration is not ready")
                definition = self.deviceManager.definitions.get(request.device_id)
                executor = self.deviceManager.executors.get(request.device_id)
                if (definition is None or definition.deviceTypeId != "device.camera.huaray_imv"
                        or revisionOf(definition.model_dump()) != request.expected_definition_revision
                        or executor is None or executor.generation != request.expected_generation):
                    raise ValueError("camera identity/definition changed; read online state again")
                if request.action == "open":
                    sessionId = self.cameraDiagnostics.open(request.device_id, request.expected_definition_revision,
                                                             request.expected_generation)
                    return runtime_pb2.CameraDiagnosticReply(ok=True, session_id=sessionId,
                        metadata_json='{"cameraTuningVersion":1,"cameraPreviewVersion":1}')
                if request.action not in {"capture", "read_parameters", "trial_capture", 'watch', 'preview_capture'} or session is None:
                    raise ValueError("unknown camera diagnostic session/action")
                if (session.deviceId, session.definitionRevision, session.generation) != (
                        request.device_id, request.expected_definition_revision, request.expected_generation):
                    raise ValueError("camera diagnostic session identity mismatch")
            result = (self.cameraDiagnostics.watch(session, request.sequence) if request.action == 'watch' else
                session.capture(request.sequence, request.confirmed,
                                     lambda: not getattr(context, "is_active", lambda: True)(),
                                     action=request.action, changes=changes))
            return runtime_pb2.CameraDiagnosticReply(ok=result["ok"], code=result.get("code", ""),
                message=result.get("message", ""), session_id=request.session_id,
                metadata_json=json.dumps(result.get("metadata", {})), preview_jpeg=result.get("preview", b""))
        except (ValueError, RuntimeError) as error:
            return runtime_pb2.CameraDiagnosticReply(ok=False, code=getattr(error, "code", "E_DEVICE_DIAGNOSTIC"),
                message=str(error), session_id=request.session_id)

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
                    device_requirements_json=json.dumps({key: value.model_dump() for key, value in
                                                         descriptor.manifest.deviceRequirements.items()}),
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
        return runtime_pb2.ListOperatorsReply(operators=operators,
            camera_recovery_warnings_json=json.dumps(
                getattr(getattr(self, "livePreviewManager", None), "recoveryWarnings", []), ensure_ascii=False))

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
                              self.loadedProjectFile)
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
                        inspectionId, key, jobId, workflowId, nodeId,
                        str(getattr(request, 'workflow_run_id', '')), str(getattr(request, 'node_run_id', '')))
                except ValueError as error:
                    return runtime_pb2.ListNodePreviewSourcesReply(capture_state="EXPIRED", message=str(error))
                job = self.jobRepository.get(jobId)
                sources = [runtime_pb2.PreviewSourceInfo(source_id=asset.assetId,
                    label=asset.port, source_kind="current", workflow_id=asset.workflowId, node_id=asset.nodeId,
                    port=asset.port, width=asset.width, height=asset.height, mime_type=asset.mimeType,
                    origin_job_id=asset.originJobId, origin_project_revision=asset.originProjectRevision,
                    capture_id=asset.captureId, workflow_run_id=asset.workflowRunId, node_run_id=asset.nodeRunId,
                    created_at_ms=asset.createdAtMs, iteration_path_json=json.dumps(list(asset.iterationPath)),
                    original_width=asset.originalWidth, original_height=asset.originalHeight,
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
    def OpenDraftPurePreviewSession(self, request, context):  # type: ignore[override]
        try:
            session = self.pureDraftPreviewSessions.open(request)
            if not getattr(context, "is_active", lambda: True)():
                self.pureDraftPreviewSessions.close(session.sessionId)
                raise DraftPreviewError("纯预览会话请求已取消")
            return runtime_pb2.OpenOperatorPreviewSessionReply(ok=True, session_id=session.sessionId, message="ok")
        except (ValueError, TypeError, KeyError) as error:
            return runtime_pb2.OpenOperatorPreviewSessionReply(
                ok=False, code="E_PREVIEW_CONTEXT_INVALID", message=str(error))

    @_withProjectStateLock
    def CloseDraftPurePreviewSession(self, request, context):  # type: ignore[override]
        self.pureDraftPreviewSessions.close(str(request.session_id))
        return runtime_pb2.CloseOperatorPreviewSessionReply(ok=True, message="closed")

    @_withProjectStateLock
    def UploadPreviewImage(self, request_iterator, context):  # type: ignore[override]
        _ = context
        chunks: list[bytes] = []
        total = 0
        filename = ""
        requestedProjectId = ""
        draftSessionId = ""
        identity = None
        try:
            for chunk in request_iterator:
                chunkIdentity = (str(getattr(chunk, "project_id", "")),
                                 str(getattr(chunk, "draft_session_id", "")),
                                 str(getattr(chunk, "upload_id", "")))
                if identity is not None and chunkIdentity != identity:
                    raise ValueError("preview upload identity changed between chunks")
                identity = chunkIdentity
                requestedProjectId, draftSessionId, _uploadId = identity
                if not getattr(context, "is_active", lambda: True)():
                    raise ValueError("preview upload cancelled")
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
            session = None
            if draftSessionId:
                session = self.pureDraftPreviewSessions.require(draftSessionId, requestedProjectId)
                projectKey = session.projectKey
            else:
                if self.loadedDocument is None or not self._projectMatches(requestedProjectId):
                    raise ValueError("preview upload project is not loaded")
                projectKey = self._loadedProjectPreviewKey
            asset = self.previewAssetStore.addUploadedImage(
                b"".join(chunks),
                filename,
                projectKey=projectKey,
            )
            if session is not None:
                self.pureDraftPreviewSessions.retain(session, [asset])
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
        draftSessionId = str(getattr(request, "draft_session_id", ""))
        if draftSessionId and inspectionId:
            raise DraftPreviewError("预览会话类型不能混用")
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
            if draftSessionId:
                try:
                    session = self.pureDraftPreviewSessions.require(draftSessionId, requestedProjectId)
                    projectKey = session.projectKey
                except DraftPreviewError as error:
                    if context is not None:
                        context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(error))
                    raise
            else:
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
            if draftSessionId:
                with self._projectStateLock:
                    self.pureDraftPreviewSessions.require(draftSessionId, requestedProjectId)
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
        session = None
        draftSessionId = str(getattr(request, "draft_session_id", ""))
        if draftSessionId:
            try:
                session = self.pureDraftPreviewSessions.require(draftSessionId, projectId, request)
                if getattr(request, "job_id", ""):
                    raise DraftPreviewError("草稿纯预览不能借用正式任务变量，请在运行结果检查中查看任务快照")
            except (ValueError, TypeError, KeyError) as error:
                return runtime_pb2.RunOperatorPreviewReply(ok=False, code="E_PREVIEW_CONTEXT_INVALID", message=str(error))
            projectKey = session.projectKey
        else:
            nodeError = self._validatePreviewNode(projectId, workflowId, nodeId, operatorId)
            if nodeError:
                return runtime_pb2.RunOperatorPreviewReply(
                    ok=False, code="E_PREVIEW_CONTEXT_INVALID", message=nodeError)
            projectKey = self._loadedProjectPreviewKey
        imageAssetId = str(getattr(request, "image_asset_id", ""))
        if not self.previewAssetStore.isOwnedByProject(
            imageAssetId, projectKey
        ):
            return runtime_pb2.RunOperatorPreviewReply(
                ok=False,
                code="E_PREVIEW_SOURCE_NOT_FOUND",
                message="preview image is unavailable for the current project/session",
            )
        params, parseError = parsePreviewParams(str(getattr(request, "params_json", "")))
        if params is None:
            return runtime_pb2.RunOperatorPreviewReply(
                ok=False, code="E_PARAM_INVALID", message=parseError or "invalid parameters"
            )
        try:
            if session is not None:
                from emo_master.apps.runtime.preview.global_variables import previewParameters
                descriptor = self.pluginScanResult.activeOperators[operatorId]
                params, variableSnapshot = previewParameters(self.sqliteStore, session.document,
                    workflowId, nodeId, params, descriptor.manifest.paramSchema,
                    withSnapshot=True, initialOnly=True)
            else:
                params, variableSnapshot = self._previewVariableParams(request, params)
        except (ValueError, KeyError, StopIteration) as error:
            return runtime_pb2.RunOperatorPreviewReply(ok=False, code=getattr(error, "code", "E_VARIABLE_BINDING"), message=str(error))
        requestId = str(getattr(request, "request_id", "")) or str(uuid4())
        addCallback = getattr(context, "add_callback", None)
        if callable(addCallback):
            addCallback(lambda: self.previewExecutor.cancel(requestId))
        result = self.previewExecutor.execute(
            operatorId,
            params,
            imageAssetId,
            projectId=projectId if session is not None else self.loadedProjectId,
            projectKey=projectKey,
            workflowId=workflowId,
            nodeId=nodeId,
            requestId=requestId,
            globalVariables=variableSnapshot,
        )
        if session is not None and result.ok:
            try:
                self.pureDraftPreviewSessions.retain(session, [output.asset for output in result.assets], outputs=True)
            except DraftPreviewError as error:
                return runtime_pb2.RunOperatorPreviewReply(ok=False, code=error.code, message=str(error))
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

    def OpenPlcDebugSession(self, request, context):  # type: ignore[override]
        self.jobSupervisor.assertMutationAllowed()
        try:
            params = parsePlcDebugParams(str(request.params_json))
            if not request.request_id or len(request.request_id) > 128:
                raise ValueError("request_id is required (max 128 characters)")
            with self._projectStateLock:
                if self._closing:
                    return runtime_pb2.PlcDebugReply(**asdict(self.plcDebugManager.failure("E_RUNTIME_CLOSING", "Runtime is closing")))
                # Debug parameters are a draft, not a workflow invocation. New
                # unsaved PLC nodes may debug without saving or reloading a project.
                validProject = bool(self.loadedDocument is not None and self.loadedProjectId and request.project_id == self.loadedProjectId)
                validOperator = request.operator_id in PLC_OPERATORS and request.operator_id in self.pluginScanResult.activeOperators
                validAttribution = all(0 < len(value) <= 256 for value in (request.workflow_id, request.node_id))
                if not validProject or not validOperator or not validAttribution:
                    return runtime_pb2.PlcDebugReply(**asdict(self.plcDebugManager.failure(
                        "E_PLC_CONTEXT_INVALID", "PLC debug requires the current loaded project, a registered PLC operator and bounded draft node identity")))
                clientFactory = None
                managed = self.loadedDocument.schemaVersion in {"2.6", "2.7"}
                if managed:
                    from emo_master.core.project.snapshots import revisionOf
                    from emo_master.apps.runtime.devices.slmp_debug import ManagedSlmpDebug
                    if set(params) != {"deviceId", "definitionRevision"}:
                        raise ValueError("managed PLC diagnostics require deviceId and definitionRevision, not endpoint overrides")
                    deviceId = params["deviceId"]
                    if not isinstance(deviceId, str):
                        raise ValueError("invalid managed PLC device identity")
                    definition = self.deviceManager.definitions.get(deviceId)
                    if (definition is None or definition.deviceTypeId != "device.plc.mitsubishi_slmp"
                            or revisionOf(definition.model_dump()) != params["definitionRevision"]):
                        raise ValueError("online PLC definition changed; read current device state before diagnostics")
                    def clientFactory(endpoint):
                        return ManagedSlmpDebug(self.deviceManager, deviceId)
                    params = definition.connection
                with self._previewJobLock:
                    if self.operatorDebugManager.ownsResources():
                        return runtime_pb2.PlcDebugReply(**asdict(self.plcDebugManager.failure(
                            "E_RESOURCE_BUSY", "operator debug still holds Runtime resources")))
                    if not managed and any(not record.isTerminal or self.jobSupervisor.ownsJobResources(record.jobId)
                           for record in self.jobRepository.all()):
                        return runtime_pb2.PlcDebugReply(**asdict(self.plcDebugManager.failure(
                            "E_RESOURCE_BUSY", "a Runtime job is active")))
                    session = self.plcDebugManager.reserve(self.loadedProjectId, params,
                        workflowId=request.workflow_id, nodeId=request.node_id, clientFactory=clientFactory)
            # TCP connect never owns the global project/admission locks.
            return runtime_pb2.PlcDebugReply(**asdict(self.plcDebugManager.connect(session, context)))
        except (ValueError, TypeError) as error:
            return runtime_pb2.PlcDebugReply(**asdict(self.plcDebugManager.failure("E_PARAM_INVALID", str(error))))
        except RuntimeError as error:
            return runtime_pb2.PlcDebugReply(**asdict(self.plcDebugManager.failure("E_RESOURCE_BUSY", str(error))))

    def ExecutePlcDebugCommand(self, request, context):  # type: ignore[override]
        self.jobSupervisor.assertMutationAllowed()
        try:
            params = parsePlcDebugParams(str(request.params_json))
        except (ValueError, TypeError) as error:
            return runtime_pb2.PlcDebugReply(**asdict(self.plcDebugManager.failure("E_PARAM_INVALID", str(error))))
        return runtime_pb2.PlcDebugReply(**asdict(self.plcDebugManager.execute(
            request.session_id, request.runtime_instance_id, request.command, params, request.request_id, context)))

    def ClosePlcDebugSession(self, request, context):  # type: ignore[override]
        if request.runtime_instance_id != self.runtimeInstanceId:
            return runtime_pb2.PlcDebugReply(**asdict(self.plcDebugManager.failure("E_PLC_CONTEXT_INVALID", "Runtime instance changed")))
        error = self.plcDebugManager.close(request.session_id)
        return runtime_pb2.PlcDebugReply(ok=error is None, session_id=request.session_id,
                                        runtime_instance_id=self.runtimeInstanceId, state="closed",
                                        code="E_PLC_RELEASE_FAILED" if error else "", message=error or "PLC debug session closed")

    def OpenCameraConfigurationSession(self, request, context):  # type: ignore[override]
        configurationRequest = runtime_pb2.OpenOperatorPreviewSessionRequest()
        configurationRequest.CopyFrom(request)
        configurationRequest.configuration_only = True
        if configurationRequest.project_json:
            return self.OpenDraftOperatorPreviewSession(configurationRequest, context)
        return self.OpenOperatorPreviewSession(configurationRequest, context)

    @_withProjectStateLock
    def OpenOperatorPreviewSession(self, request, context):  # type: ignore[override]
        if self.loadedDocument is not None and self.loadedDocument.schemaVersion in {"2.6", "2.7"}:
            return runtime_pb2.OpenOperatorPreviewSessionReply(ok=False, code="E_DEVICE_DEBUG_REQUIRED",
                message="managed camera projects must use the device diagnostic entry point")
        isActive = getattr(context, "is_active", lambda: True)
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
        try:
            params, variableSnapshot = self._previewVariableParams(request, params)
        except (ValueError, KeyError, StopIteration) as failure:
            return runtime_pb2.OpenOperatorPreviewSessionReply(ok=False, code=getattr(failure, "code", "E_VARIABLE_BINDING"), message=str(failure))
        with self._previewJobLock:
            if any(not record.isTerminal or self.jobSupervisor.ownsJobResources(record.jobId)
                   for record in self.jobRepository.all()):
                return runtime_pb2.OpenOperatorPreviewSessionReply(
                    ok=False,
                    code="E_RESOURCE_BUSY",
                    message="a project job is active",
                )
            if not isActive():
                return runtime_pb2.OpenOperatorPreviewSessionReply(
                    ok=False, code="E_CANCELLED", message="相机预览请求已取消"
                )
            sessionId, error = self.livePreviewManager.open(
                operatorId,
                self.loadedProjectId,
                workflowId,
                nodeId,
                params,
                globalVariables=variableSnapshot,
                configurationOnly=bool(getattr(request, "configuration_only", False)),
            )
            if sessionId is not None and not isActive():
                error = self.livePreviewManager.close(sessionId)
                return runtime_pb2.OpenOperatorPreviewSessionReply(
                    ok=False, code="E_PREVIEW_RELEASE_FAILED" if error else "E_CANCELLED",
                    message=error or "相机预览请求已取消，设备已释放",
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
        if self.loadedDocument is not None and self.loadedDocument.schemaVersion in {"2.6", "2.7"}:
            return runtime_pb2.OpenOperatorPreviewSessionReply(ok=False, code="E_DEVICE_DEBUG_REQUIRED",
                message="private camera previews cannot bypass the managed device authority")
        try:
            draft = json.loads(request.project_json or "{}")
            if isinstance(draft, dict) and draft.get("schemaVersion") in {"2.6", "2.7"}:
                return runtime_pb2.OpenOperatorPreviewSessionReply(ok=False, code="E_DEVICE_DEBUG_REQUIRED",
                    message="managed camera drafts must use the device diagnostic entry point")
        except (ValueError, TypeError):
            pass
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
        try:
            params, variableSnapshot = self._previewVariableParams(request, params)
        except (ValueError, KeyError, StopIteration) as failure:
            return runtime_pb2.OpenOperatorPreviewSessionReply(ok=False, code=getattr(failure, "code", "E_VARIABLE_BINDING"), message=str(failure))
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
                globalVariables=variableSnapshot,
                configurationOnly=bool(getattr(request, "configuration_only", False)),
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

    def _previewVariableParams(self, request, params):
        if getattr(request, "configuration_only", False):
            # The explicitly selected device is opened read-only. Saved node
            # bindings must not silently select another target or apply values.
            return params, None
        from emo_master.apps.runtime.preview.global_variables import previewParameters
        from emo_master.core.project.global_variables import VariableError
        document = self.loadedDocument
        if getattr(request, "project_json", ""):
            projectId, error = draftPreviewProjectId(request)
            if not projectId:
                raise ValueError(error)
            document = ProjectDocument.model_validate(migrateProjectPayload(json.loads(request.project_json)))
        if document is None or document.project.projectId != request.project_id:
            raise ValueError("preview project mismatch")
        jobId = getattr(request, "job_id", "")
        if jobId:
            job = self.jobRepository.get(jobId)
            if job is None or job.projectId != document.project.projectId:
                raise VariableError("E_VARIABLE_JOB_UNKNOWN", "preview job does not belong to this project")
        descriptor = self.pluginScanResult.activeOperators.get(request.operator_id)
        schema = descriptor.manifest.paramSchema if descriptor else {}
        return previewParameters(self.sqliteStore, document, request.workflow_id, request.node_id, params, schema, jobId, withSnapshot=True)

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
                    state=frame.state,
                    generation=frame.generation,
                    code=frame.code,
                    message=frame.message,
                )
        except RuntimeError as err:
            abort = getattr(context, "abort", None)
            if callable(abort):
                abort(grpc.StatusCode.FAILED_PRECONDITION, str(err))
            raise

    def ConfigureOperatorSession(self, request, context):  # type: ignore[override]
        self.jobSupervisor.assertMutationAllowed()
        if self._closing:
            return runtime_pb2.ConfigureOperatorSessionReply(ok=False, code="E_RUNTIME_CLOSING", message="Runtime is closing")
        payload, error = parsePreviewParams(str(getattr(request, "payload_json", "") or "{}"))
        if payload is None:
            return runtime_pb2.ConfigureOperatorSessionReply(ok=False, code="E_PARAM_INVALID", message=error or "invalid payload")
        result = self.livePreviewManager.configurationRequest(
            str(request.session_id), str(request.action), payload,
            str(request.device_identity), str(request.request_id))
        return runtime_pb2.ConfigureOperatorSessionReply(
            ok=bool(result.get("ok")), code=str(result.get("code", "")), message=str(result.get("message", "")),
            state_json=json.dumps(result.get("snapshot", {}), ensure_ascii=False),
            warnings_json=json.dumps(result.get("warnings", []), ensure_ascii=False))

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
    def GetDispatchQueueState(self, request, context):
        error = self._globalCounterProjectError(request.project_id)
        if error:
            return runtime_pb2.DispatchQueueReply(ok=False, code=error[0], message=error[1])
        state = self.jobSupervisor.dispatchQueueState(request.project_id, request.run_group_id,
                                                     request.target_slot_id if request.include_items else "")
        state["runtimeInstanceId"] = self.runtimeInstanceId
        return runtime_pb2.DispatchQueueReply(ok=True, state_json=json.dumps(state, ensure_ascii=False))

    @_withProjectStateLock
    def ListGlobalVariables(self, request, context):
        return self._globalVariableRequest(request, "list")

    @_withProjectStateLock
    def GetGlobalVariable(self, request, context):
        return self._globalVariableRequest(request, "get")

    @_withProjectStateLock
    def SetGlobalVariable(self, request, context):
        return self._globalVariableRequest(request, "set")

    @_withProjectStateLock
    def ResetGlobalVariable(self, request, context):
        return self._globalVariableRequest(request, "reset")

    def _globalVariableRequest(self, request, operation):
        from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables, legacyDefinitions
        from emo_master.core.project.global_variables import VariableError
        error = self._globalCounterProjectError(request.project_id)
        if error:
            return runtime_pb2.GlobalVariablesReply(ok=False, code=error[0], message=error[1])
        try:
            variableDefinitions = {**legacyDefinitions(self.sqliteStore, self.loadedProjectId),
                                   **self.loadedDocument.globalVariables}
            selected = variableDefinitions.get(request.variable_id)
            selected = selected.model_dump() if hasattr(selected, "model_dump") else selected or {}
            needsJob = selected.get("lifetime") == "job" and selected.get("kind") != "constant"
            if request.job_id and (operation == "list" or needsJob):
                job = self.jobRepository.get(request.job_id)
                if job is None or job.projectId != self.loadedProjectId:
                    raise VariableError("E_VARIABLE_JOB_UNKNOWN", "job does not belong to the loaded project")
                if operation in {"set", "reset"} and job.isTerminal and selected.get("lifetime") == "job" and selected.get("kind") != "constant":
                    raise VariableError("E_VARIABLE_JOB_ENDED", "job has ended")
            accessor = ProjectGlobalVariables(self.sqliteStore, self.loadedProjectId, variableDefinitions, request.job_id)
            if operation in {"set", "reset"}:
                if not request.HasField("expected_revision"):
                    raise VariableError("E_VARIABLE_REVISION_REQUIRED", "refresh and supply the expected value revision")
                if operation == "set":
                    accessor.set(request.variable_id, json.loads(request.value_json), request.expected_revision)
                else:
                    accessor.reset(request.variable_id, request.expected_revision)
            keys = list(accessor.definitions) if operation == "list" else [request.variable_id]
            rows = []
            for key in keys:
                definition = accessor._definition(key)
                row = {"variableId": key, **definition.model_dump(), "state": "current"}
                if definition.kind == "variable" and definition.lifetime == "job" and not request.job_id:
                    if operation != "list":
                        raise VariableError("E_VARIABLE_JOB_REQUIRED", "select a job for this variable")
                    row.update(value=None, revision=None, updatedAtMs=None, state="initial")
                else:
                    try:
                        row.update(accessor.records([key])[key].toDict())
                    except VariableError as missing:
                        if operation != "list" or missing.code != "E_VARIABLE_NOT_INITIALIZED":
                            raise
                        row.update(value=None, revision=None, updatedAtMs=None, state="not_initialized")
                rows.append(row)
            jobs = [{"jobId": record.jobId, "ended": record.isTerminal, "workflowId": record.workflowId}
                    for record in self.jobRepository.all() if record.projectId == self.loadedProjectId]
            return runtime_pb2.GlobalVariablesReply(ok=True, variables_json=json.dumps(rows, ensure_ascii=False, allow_nan=False),
                                                   jobs_json=json.dumps(jobs))
        except Exception as failure:
            return runtime_pb2.GlobalVariablesReply(ok=False, code=getattr(failure, "code", "E_VARIABLE_STATE"), message=str(failure))

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
        network = getattr(self, 'deviceNetwork', None)
        if network is not None:
            self._closeStep('device-network', network.close, retryable=True)
        transfers = getattr(self, "projectResourceTransfers", None)
        if transfers is not None:
            self._closeStep("resource-transfers", transfers.close, retryable=True)
        configuration = getattr(self, "projectConfiguration", None)
        if configuration is not None:
            self._closeStep("project-configuration", configuration.close, retryable=True)
        self.jobSupervisor.beginClosing()
        self.communicationManager.beginClosing()
        presentation = getattr(self, "_presentationOwner", None)
        if presentation is not None:
            presentation.beginClosing()
        self._maintenanceStop.set()
        self.operatorDebugManager.close()
        errors = list(self.livePreviewManager.closeAll(terminate=True))
        errors.extend(self.plcDebugManager.closeAll())
        self._closeStep("camera diagnostics", self.cameraDiagnostics.close, retryable=True)
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
        self._closeStep("communication", self.communicationManager.close, retryable=True)
        self._closeStep("devices", self.deviceManager.close, retryable=True)
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
        self._closeStep("pure-draft-sessions", self.pureDraftPreviewSessions.closeAll)
        try:
            # Only a proven pending-file unlink may retry. Unknown partial
            # disposal keeps the original fail-closed, once-only contract.
            self._closeStep("preview-assets", self.previewAssetStore.close,
                retryable=self._closeStages.get("preview-assets") == "TRANSIENT_CLEANUP_PENDING")
        except PreviewTransientCleanupPending:
            self._closeStages["preview-assets"] = "TRANSIENT_CLEANUP_PENDING"
            raise
        self._closeStep("device-logs", self.deviceManager.diagnostics.flushOperational)
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
        journal = getattr(self, "configurationResourceJournal", None)
        if journal is not None:
            self._closeStep("configuration-resources-retired", journal.retire, retryable=True)
        self._closeStep("sqlite", self.sqliteStore.releaseIdleConnection, retryable=True)
        self._closeStep("data-lock", self._runtimeDataLock.release)
        self._closed = True

    def _operatorDebugResourcesBusy(self):
        if any(not record.isTerminal or self.jobSupervisor.ownsJobResources(record.jobId)
               for record in self.jobRepository.all()):
            return True
        if self.livePreviewManager.ownsResources() or self.plcDebugManager.ownsResources():
            return True
        with self.previewExecutor._stateLock:
            return bool(self.previewExecutor._cancellations)

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

    def _onInspectionSnapshot(self, jobId, event):
        try:
            self.runInspectionStore.enqueue(jobId, event)
        except Exception as error:
            self._inspectionFailed(jobId, error)

    def _inspectionFailed(self, jobId, error):
        self.eventStore.append(jobId, 'preview.snapshot.failed', str(error), level='WARN',
                               code='E_INSPECTION_TAKEOVER')

    def _onJobTerminal(self, jobId: str, status: str) -> None:
        if self.loadedDocument is not None and self.loadedDocument.schemaVersion != '2.7':
            self.communicationManager.revokeJob(jobId)
        self.deviceManager.revoke(jobId)
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
        group = self.deviceManager.dispatchGroups.get(jobId)
        if group is not None and group.source == jobId and group.retired < group.members:
            return
        from emo_master.apps.runtime.preview.store import _ioPath
        if self._workspacePaths.get(jobId) == workspace:
            self._workspacePaths.pop(jobId, None)
        try:
            if workspace.exists():
                # The private preview writer uses extended Win32 paths. Its
                # successfully written assets must be removable by retirement.
                shutil.rmtree(_ioPath(workspace))
        except OSError:
            # Preserve existing deferred stale-workspace cleanup semantics.
            return

    def _onJobRetired(self, jobId: str) -> None:
        if self.loadedDocument is not None and self.loadedDocument.schemaVersion != '2.7':
            self.communicationManager.revokeJob(jobId)
        self.deviceManager.retire(jobId)
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
        from emo_master.apps.runtime.preview.store import _ioPath
        if not self.workspaceRoot.exists():
            return
        for workspace in self.workspaceRoot.iterdir():
            if not workspace.is_dir():
                continue
            record = self.jobRepository.get(workspace.name)
            if record is None or record.isTerminal:
                try:
                    shutil.rmtree(_ioPath(workspace))
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

    @staticmethod
    def _builtinsRoot() -> Path:
        return Path(__file__).resolve().parents[3] / "plugins"

    def _resolveProjectFile(self, projectPath: Path) -> Path | None:
        from emo_master.core.project.files import resolveProjectFile
        try:
            return resolveProjectFile(projectPath)
        except (OSError, ValueError):
            return None

    def _projectMatches(self, requested: str) -> bool:
        if requested == "":
            return True
        if requested in {
            self.loadedProjectId,
            self.loadedProjectPath or "",
            self.loadedProjectFile or "",
        }:
            return True
        # Initial publication resolves its source path, while Designer retains
        # the selected path. Use the same copy identity as inspection renewals,
        # without treating another file with the same project UUID as an alias.
        try:
            reference = self._inspectionReference(requested)
            return any(reference == self._inspectionReference(path)
                       for path in (self.loadedProjectPath, self.loadedProjectFile) if path)
        except (OSError, RuntimeError, ValueError):
            return False

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
        from emo_master.core.project.communication import CommunicationSettings
        self.communicationManager.configure("", "", CommunicationSettings())
        if self.loadedProjectId:
            self.livePreviewManager.closeProject(self.loadedProjectId)
            self.plcDebugManager.closeProject(self.loadedProjectId)
        self.loadedProjectPath = None
        self.loadedProjectFile = None
        self.loadedProjectId = ""
        self._loadedProjectPreviewKey = ""
        self.loadedDocument = None
        self.loadedPayload = None
        self.jobMessages["__load__"] = message

    def _failedProjectCandidate(self, message):
        # Do not tear down a live project's listener for an invalid candidate.
        if self.loadedDocument is not None:
            self.jobMessages["__load__"] = message
        else:
            self._clearLoadedProject(message)


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
