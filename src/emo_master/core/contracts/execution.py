from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArtifactRef:
    artifactId: str
    kind: str
    path: str
    mimeType: str = "application/octet-stream"
    sizeBytes: int = 0
    checksum: str = ""
    nodeId: str = ""
    workflowRunId: str = ""


@dataclass(frozen=True)
class RuntimeEventDTO:
    jobId: str
    eventType: str
    message: str
    level: str = "INFO"
    nodeId: str = ""
    code: str = ""
    payload: dict[str, object] = field(default_factory=dict)
    sequence: int = 0
    timestampMs: int = 0
    projectId: str = ""
    workflowId: str = ""
    workflowRunId: str = ""
    parentWorkflowRunId: str = ""
    nodeRunId: str = ""
    iterationPath: tuple[int, ...] = ()

    @property
    def job_id(self) -> str:
        return self.jobId

    @property
    def event_type(self) -> str:
        return self.eventType

    @property
    def node_id(self) -> str:
        return self.nodeId

    @property
    def payload_json(self) -> str:
        import json

        return json.dumps(self.payload, ensure_ascii=True)

    @property
    def timestamp_ms(self) -> int:
        return self.timestampMs

    @property
    def project_id(self) -> str:
        return self.projectId

    @property
    def workflow_id(self) -> str:
        return self.workflowId

    @property
    def workflow_run_id(self) -> str:
        return self.workflowRunId

    @property
    def parent_workflow_run_id(self) -> str:
        return self.parentWorkflowRunId

    @property
    def node_run_id(self) -> str:
        return self.nodeRunId

    @property
    def iteration_path_json(self) -> str:
        import json

        return json.dumps(list(self.iterationPath), ensure_ascii=True)
