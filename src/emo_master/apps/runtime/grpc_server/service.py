from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from typing import Any

from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.events.models import RuntimeEvent
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.jobs.manager import JobManager
from emo_master.apps.runtime.jobs.models import JobProcessSpec, JobStatus, nowMs
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_master.apps.runtime.jobs.supervisor import JobSupervisor
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as _runtime_pb2
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError

runtime_pb2: Any = _runtime_pb2


class RuntimeService(runtime_pb2_grpc.RuntimeServiceServicer):
    def __init__(
        self,
        dbPath: Path | None = None,
        pluginRootPaths: tuple[str, ...] | None = None,
        maxConcurrentJobs: int = 2,
        workspaceRoot: Path | None = None,
    ) -> None:
        if dbPath is None:
            dbPath = _defaultDbPath()
        self.sqliteStore = SqliteStore(dbPath)
        self.sqliteStore.initialize()
        self.sqliteStore.markOrphanedJobsFailed()
        self.jobRepository = JobRepository(self.sqliteStore)
        self.eventStore = EventStore(self.sqliteStore)
        self.workspaceRoot = workspaceRoot or _defaultWorkspaceRoot()
        self.workspaceRoot.mkdir(parents=True, exist_ok=True)
        self._workspacePaths: dict[str, Path] = {}
        self._cleanupStaleWorkspaces()
        self.pluginScanResult = self._scanBuiltins(pluginRootPaths)
        self.pluginRootPaths = pluginRootPaths or (str(self._builtinsRoot()),)
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

    def LoadProject(self, request, context):  # type: ignore[override]
        _ = context
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
        self.eventStore.retentionPerJob = document.runtime.eventRetentionPerJob
        self.jobSupervisor.maxConcurrentJobs = document.runtime.maxConcurrentJobs
        self.jobSupervisor.gracefulStopTimeoutMs = document.runtime.gracefulStopTimeoutMs
        self.jobSupervisor.heartbeatTimeoutMs = document.runtime.heartbeatTimeoutMs
        return runtime_pb2.LoadProjectReply(ok=True, status="READY", message="project loaded")

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

        record = self.jobManager.createJob(
            projectId=document.project.projectId,
            projectRevision=document.project.revision,
            workflowId=workflowId,
        )
        self.jobMessages[record.jobId] = "job accepted"
        snapshotPath, workspacePath = self._createSnapshot(record.jobId, document)
        self._workspacePaths[record.jobId] = workspacePath
        spec = JobProcessSpec(
            jobId=record.jobId,
            projectSnapshotPath=str(snapshotPath),
            workflowId=workflowId,
            projectId=document.project.projectId,
            inputsJson=json.dumps(inputs, ensure_ascii=True),
            pluginRootPaths=tuple(self.pluginRootPaths),
            jobWorkspacePath=str(workspacePath),
            heartbeatTimeoutMs=document.runtime.heartbeatTimeoutMs,
        )
        try:
            self.jobManager.start(record, spec)
        except Exception as err:
            self.eventStore.append(
                record.jobId,
                "job.failed",
                str(err),
                level="ERROR",
                code="E_MAX_CONCURRENT_JOBS" if "MAX_CONCURRENT" in str(err) else "E_JOB_START_FAILED",
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
            self._removeWorkspace(record.jobId)
            return runtime_pb2.StartJobReply(
                ok=False, job_id=record.jobId, status="FAILED", message=str(err)
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
        for event in events:
            if context is not None and hasattr(context, "is_active") and not context.is_active():
                return
            yield self._toProtoEvent(event)

    def ListOperators(self, request, context):  # type: ignore[override]
        _ = request
        _ = context
        operators = []
        for operatorId, descriptor in self.pluginScanResult.activeOperators.items():
            operators.append(
                runtime_pb2.OperatorInfo(
                    operator_id=operatorId,
                    display_name=descriptor.manifest.displayName,
                    version=descriptor.manifest.version,
                    input_ports=descriptor.manifest.inputPorts,
                    output_ports=descriptor.manifest.outputPorts,
                    param_schema_json=json.dumps(descriptor.manifest.paramSchema, ensure_ascii=True),
                    category=descriptor.manifest.category,
                    icon_key=descriptor.manifest.iconKey,
                    summary=descriptor.manifest.summary,
                )
            )
        return runtime_pb2.ListOperatorsReply(operators=operators)

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

    def close(self) -> None:
        self.jobSupervisor.shutdown()
        for jobId in list(self._workspacePaths):
            self._removeWorkspace(jobId)
        self._cleanupStaleWorkspaces()

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

    def _scanBuiltins(self, pluginRootPaths: tuple[str, ...] | None):
        roots = pluginRootPaths or (str(self._builtinsRoot()),)
        active = {}
        rejected = {}
        for root in roots:
            result = PluginRegistry(coreVersion="0.2.0").scan(Path(root))
            active.update(result.activeOperators)
            rejected.update(result.rejectedOperators)
        return type("RegistryScanResult", (), {"activeOperators": active, "rejectedOperators": rejected})()

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

    def _clearLoadedProject(self, message: str) -> None:
        self.loadedProjectPath = None
        self.loadedProjectId = ""
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
