from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from functools import wraps
from pathlib import Path
import shutil
import threading
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
from emo_master.apps.runtime.jobs.models import JobProcessSpec, JobStatus, nowMs
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_master.apps.runtime.jobs.supervisor import JobSupervisor
from emo_master.apps.runtime.preview.executor import PurePreviewExecutor, parsePreviewParams
from emo_master.apps.runtime.preview.live import LivePreviewManager
from emo_master.apps.runtime.preview.store import PreviewAssetStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as _runtime_pb2
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.core.contracts.port_types import canonicalPortTypes
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
        with self._projectStateLock:
            return method(self, *args, **kwargs)

    return locked


class RuntimeService(runtime_pb2_grpc.RuntimeServiceServicer):
    def __init__(
        self,
        dbPath: Path | None = None,
        pluginRootPaths: tuple[str, ...] | None = None,
        maxConcurrentJobs: int = 2,
        workspaceRoot: Path | None = None,
        logDirectory: Path | None = None,
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
        self.workspaceRoot.mkdir(parents=True, exist_ok=True)
        lockName = self.workspaceRoot.name or "runtime"
        self._runtimeDataLock = RuntimeDataLock(
            self.workspaceRoot.parent / f".{lockName}.runtime.lock"
        )
        self._runtimeDataLockIsPrimary = self._runtimeDataLock.acquire()
        self._closed = False
        try:
            self.sqliteStore = SqliteStore(dbPath)
            self.sqliteStore.initialize()
            if self._runtimeDataLockIsPrimary:
                self.sqliteStore.markOrphanedJobsFailed()
            self.jobRepository = JobRepository(self.sqliteStore)
            self.eventStore = EventStore(self.sqliteStore)
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
        )
        self.jobManager = JobManager(self.jobRepository, self.eventStore, self.jobSupervisor)
        self.loadedProjectPath: str | None = None
        self.loadedProjectId: str = ""
        self.loadedDocument: ProjectDocument | None = None
        self.loadedPayload: dict[str, object] | None = None
        self.jobMessages: dict[str, str] = {}

    @_withProjectStateLock
    def LoadProject(self, request, context):  # type: ignore[override]
        _ = context
        if self.loadedProjectId:
            with self._previewJobLock:
                cleanupErrors = self.livePreviewManager.closeProject(
                    self.loadedProjectId, timeoutSeconds=3.0
                )
            if cleanupErrors:
                return runtime_pb2.LoadProjectReply(
                    ok=False,
                    status="FAILED",
                    message=(
                        "E_PREVIEW_RELEASE_FAILED: " + "; ".join(cleanupErrors)
                    ),
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
        self.jobSupervisor.maxConcurrentJobs = document.runtime.maxConcurrentJobs
        self.jobSupervisor.gracefulStopTimeoutMs = document.runtime.gracefulStopTimeoutMs
        self.jobSupervisor.heartbeatTimeoutMs = document.runtime.heartbeatTimeoutMs
        return runtime_pb2.LoadProjectReply(ok=True, status="READY", message="project loaded")

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
        _ = context
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
        inputsJson = str(getattr(request, "inputs_json", "") or "{}")
        try:
            inputs = json.loads(inputsJson)
            if not isinstance(inputs, dict):
                raise ValueError("inputs_json must contain an object")
        except Exception as err:
            return runtime_pb2.StartJobReply(
                ok=False, job_id="", status="FAILED", message=f"invalid inputs_json: {err}"
            )

        with self._previewJobLock:
            previewCleanupErrors = self.livePreviewManager.closeProject(
                document.project.projectId,
                timeoutSeconds=3.0,
            )
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
            )
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
            )
            try:
                self.jobManager.start(record, spec)
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
                self._jobPreviewKeys.pop(record.jobId, None)
                self._removeWorkspace(record.jobId)
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
        _ = context
        jobId = str(getattr(request, "job_id", ""))
        mode = str(getattr(request, "mode", "graceful")) or "graceful"
        if mode not in {"graceful", "force"}:
            return runtime_pb2.StopJobReply(ok=False, status="FAILED", message="invalid stop mode")
        record = self.jobRepository.get(jobId)
        if record is None:
            return runtime_pb2.StopJobReply(ok=False, status="FAILED", message="job not found")
        if record.isTerminal:
            return runtime_pb2.StopJobReply(ok=True, status=record.status, message="job already terminal")
        try:
            status = self.jobManager.stop(jobId, mode)
        except KeyError:
            return runtime_pb2.StopJobReply(ok=False, status="FAILED", message="job not found")
        return runtime_pb2.StopJobReply(ok=True, status=status, message=f"job stopping ({mode})")

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
    def ListNodePreviewSources(self, request, context):  # type: ignore[override]
        _ = context
        projectId = str(getattr(request, "project_id", ""))
        workflowId = str(getattr(request, "workflow_id", ""))
        nodeId = str(getattr(request, "node_id", ""))
        if (
            self.loadedDocument is None
            or not self._projectMatches(projectId)
            or workflowId not in self.loadedDocument.workflows
        ):
            return runtime_pb2.ListNodePreviewSourcesReply(sources=[])
        workflow = self.loadedDocument.workflows[workflowId]
        if not any(node.nodeId == nodeId for node in workflow.nodes):
            return runtime_pb2.ListNodePreviewSourcesReply(sources=[])
        incoming = [
            (edge.fromNode, edge.fromPort)
            for edge in workflow.edges
            if edge.toNode == nodeId and edge.toPort == "image"
        ]
        assets = self.previewAssetStore.listSources(
            self._loadedProjectPreviewKey,
            workflowId,
            nodeId,
            incoming,
        )
        return runtime_pb2.ListNodePreviewSourcesReply(
            sources=[
                runtime_pb2.PreviewSourceInfo(
                    source_id=asset.assetId,
                    label=(
                        f"当前节点 {asset.port}"
                        if asset.nodeId == nodeId
                        else f"上游 {asset.nodeId}.{asset.port}"
                    ),
                    source_kind="current" if asset.nodeId == nodeId else "upstream",
                    workflow_id=asset.workflowId,
                    node_id=asset.nodeId,
                    port=asset.port,
                    width=asset.width,
                    height=asset.height,
                    mime_type=asset.mimeType,
                    iteration_path_json=json.dumps(list(asset.iterationPath)),
                )
                for asset in assets
            ]
        )

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
        self._closed = True
        self._maintenanceStop.set()
        try:
            self.livePreviewManager.closeAll()
            self.previewExecutor.close()
            self.previewAssetStore.close()
            self.jobSupervisor.shutdown()
            self.eventStore.removeSink(self._operationalLogSink)
            writerError = self.operationalLogWriter.close(timeoutSeconds=3.0)
            if writerError:
                self._recordLogFileFailure(None, writerError)
            if self._maintenanceThread.is_alive():
                self._maintenanceThread.join(timeout=1.0)
            for jobId in list(self._workspacePaths):
                self._removeWorkspace(jobId)
            self._cleanupStaleWorkspaces()
        finally:
            self._runtimeDataLock.release()

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
        workspace = self._workspacePaths.pop(jobId, self.workspaceRoot / jobId)
        try:
            if workspace.exists():
                shutil.rmtree(workspace)
        except OSError:
            # A running third-party operator may still hold a file briefly;
            # the next Runtime start will retry stale workspace cleanup.
            return

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
