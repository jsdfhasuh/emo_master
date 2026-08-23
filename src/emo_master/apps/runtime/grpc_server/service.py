from pathlib import Path
import json
from uuid import uuid4

from emo_master.apps.runtime.execution.dag_executor import executeGraph
from emo_master.apps.runtime.execution.models import RuntimeGraph, parseRuntimeGraph
from emo_master.apps.runtime.events.event_bus import RuntimeEventBus
from emo_master.apps.runtime.scheduler.job_service import JobService
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2, runtime_pb2_grpc


class RuntimeService(runtime_pb2_grpc.RuntimeServiceServicer):
    def __init__(self) -> None:
        self.jobService = JobService()
        self.currentStatus = "IDLE"
        self.loadedProjectPath: str | None = None
        self.jobMessages: dict[str, str] = {}
        self.eventBus = RuntimeEventBus()
        self.loadedGraph: RuntimeGraph | None = None
        self.pluginScanResult = self._scanBuiltins()

    def LoadProject(self, request, context):  # type: ignore[override]
        _ = context
        projectPathRaw = str(request.project_path)
        self.loadedProjectPath = projectPathRaw

        projectFile = self._resolveProjectFile(Path(projectPathRaw))
        if projectFile is None:
            self.loadedGraph = None
            self.currentStatus = "FAILED"
            return runtime_pb2.LoadProjectReply(
                ok=False,
                status="FAILED",
                message="project.json not found; please load project folder",
            )

        try:
            payloadRaw = json.loads(projectFile.read_text(encoding="utf-8"))
        except Exception as err:
            return runtime_pb2.LoadProjectReply(
                ok=False, status="FAILED", message=f"invalid project file: {err}"
            )

        if not isinstance(payloadRaw, dict):
            return runtime_pb2.LoadProjectReply(
                ok=False, status="FAILED", message="invalid project format"
            )

        designerRaw = payloadRaw.get("designer", {})
        designer = designerRaw if isinstance(designerRaw, dict) else {}
        self.loadedGraph = parseRuntimeGraph(
            {
                "nodes": designer.get("nodes", []),
                "edges": designer.get("edges", []),
            }
        )

        self.currentStatus = "READY"
        return runtime_pb2.LoadProjectReply(
            ok=True, status="READY", message="project loaded"
        )

    def ValidateProject(self, request, context):  # type: ignore[override]
        _ = request
        _ = context
        if self.loadedProjectPath is None:
            return runtime_pb2.ValidateProjectReply(
                ok=False, errors=["project not loaded"]
            )
        return runtime_pb2.ValidateProjectReply(ok=True, errors=[])

    def StartJob(self, request, context):  # type: ignore[override]
        _ = request
        _ = context
        if self.currentStatus != "READY":
            return runtime_pb2.StartJobReply(
                ok=False, job_id="", message="runtime not ready"
            )
        jobId = str(uuid4())
        self.jobService.createJob(jobId)
        self.jobService.applyEvent(jobId, "load")
        self._appendJobEvent(jobId=jobId, eventType="job.loaded", message="job loaded")
        self.jobService.applyEvent(jobId, "start")
        self._appendJobEvent(
            jobId=jobId, eventType="job.started", message="job started"
        )
        self.currentStatus = "RUNNING"

        if self.loadedProjectPath is None:
            self.jobService.applyEvent(jobId, "fail")
            self.currentStatus = "FAILED"
            self.jobMessages[jobId] = "project not loaded"
            self._appendJobEvent(
                jobId=jobId,
                eventType="job.failed",
                message="project not loaded",
                level="ERROR",
            )
            return runtime_pb2.StartJobReply(
                ok=False, job_id=jobId, message="project not loaded"
            )

        if self.loadedGraph is None or len(self.loadedGraph.nodes) == 0:
            self.jobService.applyEvent(jobId, "fail")
            self.currentStatus = "FAILED"
            self.jobMessages[jobId] = "project graph is empty"
            self._appendJobEvent(
                jobId=jobId,
                eventType="job.failed",
                message="project graph is empty",
                level="ERROR",
            )
            return runtime_pb2.StartJobReply(
                ok=False, job_id=jobId, message="project graph is empty"
            )

        ok, message = self._runLoadedGraph(jobId)
        return runtime_pb2.StartJobReply(ok=ok, job_id=jobId, message=message)

    def StopJob(self, request, context):  # type: ignore[override]
        _ = context
        jobState = self.jobService.getJob(request.job_id)
        if jobState is None:
            return runtime_pb2.StopJobReply(
                ok=False, status="FAILED", message="job not found"
            )

        if request.mode == "force":
            jobState.state = jobState.state.ABORTED
            self.currentStatus = "IDLE"
            self._appendJobEvent(
                jobId=request.job_id,
                eventType="job.aborted",
                message="job aborted",
                level="WARN",
            )
            return runtime_pb2.StopJobReply(
                ok=True, status="ABORTED", message="job aborted"
            )

        if jobState.state.value in ("RUNNING", "PAUSED"):
            self.jobService.applyEvent(request.job_id, "stop")
            self.jobService.applyEvent(request.job_id, "abort")
            self.currentStatus = "IDLE"
            self._appendJobEvent(
                jobId=request.job_id,
                eventType="job.aborted",
                message="job stopped",
                level="WARN",
            )
            return runtime_pb2.StopJobReply(
                ok=True, status="ABORTED", message="job stopped"
            )

        return runtime_pb2.StopJobReply(
            ok=False, status=jobState.state.value, message="invalid job state"
        )

    def GetJobStatus(self, request, context):  # type: ignore[override]
        _ = context
        jobState = self.jobService.getJob(request.job_id)
        if jobState is None:
            return runtime_pb2.GetJobStatusReply(
                ok=False,
                status="UNKNOWN",
                error_code="E_JOB_NOT_FOUND",
                message="job not found",
            )
        return runtime_pb2.GetJobStatusReply(
            ok=True,
            status=jobState.state.value,
            error_code="",
            message=self.jobMessages.get(request.job_id, "ok"),
        )

    def StreamJobEvents(self, request, context):  # type: ignore[override]
        events = self.eventBus.read(request.job_id)
        _ = context
        eventCtor = getattr(runtime_pb2, "JobEvent")
        for event in events:
            yield eventCtor(
                job_id=event.jobId,
                node_id=event.nodeId,
                event_type=event.eventType,
                level=event.level,
                code="",
                message=event.message,
                payload_json=event.payloadJson,
                timestamp_ms=0,
            )

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
                    param_schema_json=json.dumps(
                        descriptor.manifest.paramSchema, ensure_ascii=True
                    ),
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

    def _scanBuiltins(self):
        pluginsRoot = Path(__file__).resolve().parents[3] / "plugins"
        registry = PluginRegistry(coreVersion="0.1.0")
        return registry.scan(pluginsRoot)

    def _runLoadedGraph(self, jobId: str) -> tuple[bool, str]:
        if self.loadedGraph is None:
            self.jobService.applyEvent(jobId, "fail")
            self.currentStatus = "FAILED"
            self.jobMessages[jobId] = "project graph not loaded"
            self._appendJobEvent(
                jobId=jobId,
                eventType="job.failed",
                message="project graph not loaded",
                level="ERROR",
            )
            return False, "project graph not loaded"

        runtimeContext: dict[str, object] = {}

        graphPayload = {
            "nodes": [
                {
                    "nodeId": node.nodeId,
                    "operatorId": node.operatorId,
                    "params": node.params,
                }
                for node in self.loadedGraph.nodes
            ],
            "edges": [
                {
                    "fromNode": edge.fromNode,
                    "fromPort": edge.fromPort,
                    "toNode": edge.toNode,
                    "toPort": edge.toPort,
                }
                for edge in self.loadedGraph.edges
            ],
        }
        operatorRegistry: dict[str, object] = {
            operatorId: descriptor.operatorClass
            for operatorId, descriptor in self.pluginScanResult.activeOperators.items()
        }
        executeResult = executeGraph(
            graph=graphPayload,
            operatorRegistry=operatorRegistry,
            runtimeContext=runtimeContext,
        )
        if executeResult.get("ok") is not True:
            message = str(executeResult.get("error", "graph execution failed"))
            self.jobService.applyEvent(jobId, "fail")
            self.currentStatus = "FAILED"
            self.jobMessages[jobId] = message
            self._appendJobEvent(
                jobId=jobId, eventType="job.failed", message=message, level="ERROR"
            )
            return False, message

        nodeStatusRaw = executeResult.get("nodeStatus", {})
        nodeStatus = nodeStatusRaw if isinstance(nodeStatusRaw, dict) else {}
        branchHitsRaw = executeResult.get("branchHits", {})
        branchHits = branchHitsRaw if isinstance(branchHitsRaw, dict) else {}
        for nodeId, status in nodeStatus.items():
            if not isinstance(nodeId, str) or not isinstance(status, str):
                continue
            branch = (
                branchHits.get(nodeId, "")
                if isinstance(branchHits.get(nodeId, ""), str)
                else ""
            )
            eventType = "node.completed" if status == "COMPLETED" else "node.skipped"
            payloadJson = json.dumps(
                {"status": status, "branch": branch}, ensure_ascii=True
            )
            self._appendJobEvent(
                jobId=jobId,
                eventType=eventType,
                message=f"{nodeId} {status}",
                nodeId=nodeId,
                payloadJson=payloadJson,
            )

        completionMessage = "job completed"
        artifacts = executeResult.get("artifacts", {})
        if isinstance(artifacts, dict):
            for artifactValue in artifacts.values():
                if not isinstance(artifactValue, dict):
                    continue
                savedPath = artifactValue.get("path")
                if isinstance(savedPath, str) and savedPath != "":
                    completionMessage = f"output image saved: {savedPath}"
                    break

        self.jobService.applyEvent(jobId, "complete")
        self.currentStatus = "COMPLETED"
        self.jobMessages[jobId] = completionMessage
        self._appendJobEvent(
            jobId=jobId, eventType="job.completed", message=completionMessage
        )
        return True, "job completed"

    def _resolveProjectFile(self, projectPath: Path) -> Path | None:
        if projectPath.is_file() and projectPath.name.lower() == "project.json":
            return projectPath
        if projectPath.is_dir():
            candidate = projectPath / "project.json"
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _appendJobEvent(
        self,
        jobId: str,
        eventType: str,
        message: str,
        level: str = "INFO",
        nodeId: str = "",
        payloadJson: str = "{}",
    ) -> None:
        self.eventBus.publish(
            jobId=jobId,
            eventType=eventType,
            message=message,
            level=level,
            nodeId=nodeId,
            payloadJson=payloadJson,
        )
