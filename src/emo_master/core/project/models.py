from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProjectMetadata(StrictModel):
    projectId: str
    name: str
    revision: int = Field(default=1, ge=1)
    createdAt: str
    updatedAt: str


class RuntimeSettings(StrictModel):
    maxConcurrentJobs: int = Field(default=2, ge=1)
    gracefulStopTimeoutMs: int = Field(default=5000, ge=0)
    heartbeatTimeoutMs: int = Field(default=5000, ge=100)
    eventRetentionPerJob: int = Field(default=10000, ge=1)


class WorkflowNode(StrictModel):
    nodeId: str
    kind: Literal["operator", "workflow_input", "workflow_output", "subflow", "loop"] = "operator"
    operatorId: str | None = None
    displayName: str | None = None
    inputPorts: dict[str, str] = Field(default_factory=dict)
    outputPorts: dict[str, str] = Field(default_factory=dict)
    paramSchema: dict[str, object] = Field(default_factory=dict)
    params: dict[str, object] = Field(default_factory=dict)
    targetWorkflowId: str | None = None
    loop: dict[str, object] = Field(default_factory=dict)


class WorkflowEdge(StrictModel):
    fromNode: str
    fromPort: str
    toNode: str
    toPort: str


class WorkflowLayout(StrictModel):
    nodePositions: dict[str, dict[str, float]] = Field(default_factory=dict)


class WorkflowDefinition(StrictModel):
    name: str
    inputs: dict[str, object] = Field(default_factory=dict)
    outputs: dict[str, object] = Field(default_factory=dict)
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    layout: WorkflowLayout = Field(default_factory=WorkflowLayout)


class ProjectDependencies(StrictModel):
    operators: list[object] = Field(default_factory=list)


class ProjectDevices(StrictModel):
    bindings: dict[str, object] = Field(default_factory=dict)


class ProjectDocument(StrictModel):
    schemaVersion: Literal["2.0"]
    project: ProjectMetadata
    entryWorkflowId: str
    workflowOrder: list[str]
    workflows: dict[str, WorkflowDefinition]
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    dependencies: ProjectDependencies = Field(default_factory=ProjectDependencies)
    devices: ProjectDevices = Field(default_factory=ProjectDevices)

    def toPayload(self) -> dict[str, object]:
        return self.model_dump(mode="python")


def parseProjectDocument(payload: dict[str, object]) -> ProjectDocument:
    return ProjectDocument.model_validate(payload)
