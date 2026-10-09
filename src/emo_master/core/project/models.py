from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer

from emo_master.core.presentation.models import Presentation
from emo_master.core.project.resources import ResourcePlan
from emo_master.core.project.global_variables import VariableDefinition, VariableBinding
from emo_master.core.workflow.loop_contracts import loopWorkflowReferenceFields


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


class ProductionSettings(StrictModel):
    autoStart: bool = False
    mode: Literal["single", "continuous"] = "single"
    cycleIntervalMs: int = Field(default=100, ge=1)
    inputs: dict[str, object] = Field(default_factory=dict)


class WorkflowNode(StrictModel):
    nodeId: str
    kind: Literal[
        "operator", "workflow_input", "workflow_output", "subflow", "loop"
    ] = "operator"
    operatorId: str | None = None
    displayName: str | None = None
    inputPorts: dict[str, str] = Field(default_factory=dict)
    outputPorts: dict[str, str] = Field(default_factory=dict)
    paramSchema: dict[str, object] = Field(default_factory=dict)
    params: dict[str, object] = Field(default_factory=dict)
    globalVariableBindings: list[VariableBinding] = Field(default_factory=list)
    targetWorkflowId: str | None = None
    loop: dict[str, object] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def serializeBindings(self, handler):
        payload = handler(self)
        if not self.globalVariableBindings:
            payload.pop("globalVariableBindings", None)
        return payload


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
    schemaVersion: Literal["2.0", "2.1", "2.2", "2.3", "2.4"]
    project: ProjectMetadata
    entryWorkflowId: str
    workflowOrder: list[str]
    workflows: dict[str, WorkflowDefinition]
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    dependencies: ProjectDependencies = Field(default_factory=ProjectDependencies)
    devices: ProjectDevices = Field(default_factory=ProjectDevices)
    presentation: Presentation | None = None
    resources: ResourcePlan | None = None
    production: ProductionSettings | None = None
    globalVariables: dict[str, VariableDefinition] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validateVersion(self) -> "ProjectDocument":
        if self.schemaVersion not in {"2.2", "2.3", "2.4"}:
            if {"presentation", "resources"} & self.model_fields_set:
                raise ValueError("presentation/resources require explicit project 2.2 migration")
        elif self.presentation is None or self.resources is None:
            raise ValueError("project 2.2/2.3 requires presentation and resources")
        if self.schemaVersion in {"2.3", "2.4"}:
            if self.production is None:
                raise ValueError("project 2.3 requires production settings")
        elif "production" in self.model_fields_set:
            raise ValueError("production requires explicit project 2.3 migration")
        if self.schemaVersion != "2.4" and (self.globalVariables or
            (self.presentation and any(source.kind == "global_variable" for source in self.presentation.dataSources.values())) or any(
            node.globalVariableBindings or node.loop.get("conditionMode") == "globalVariable"
            or node.operatorId in {"vision.state.variable_read", "vision.state.variable_write"}
            for workflow in self.workflows.values() for node in workflow.nodes
        )):
            raise ValueError("global variables require explicit project 2.4 migration")
        names = [value.name for value in self.globalVariables.values()]
        if len(names) != len(set(names)):
            raise ValueError("global variable names must be unique")
        for key, value in self.globalVariables.items():
            if (not key or len(key) > 160 or any(char.isspace() for char in key)
                    or ((key.startswith("counter:") or value.legacyCounterName is not None)
                        and key != f"counter:{value.legacyCounterName}")):
                raise ValueError("invalid or reserved global variable ID")
        return self

    @model_serializer(mode="wrap")
    def serializeVersion(self, handler):
        payload = handler(self)
        if self.schemaVersion not in {"2.2", "2.3", "2.4"}:
            payload.pop("presentation", None)
            payload.pop("resources", None)
        if self.schemaVersion not in {"2.3", "2.4"}:
            payload.pop("production", None)
        if self.schemaVersion != "2.4":
            payload.pop("globalVariables", None)
            for workflow in payload["workflows"].values():
                for node in workflow["nodes"]:
                    node.pop("globalVariableBindings", None)
        return payload

    @model_validator(mode="after")
    def validateWorkflowIndex(self) -> "ProjectDocument":
        workflowIds = set(self.workflows)
        orderedIds = self.workflowOrder
        if not workflowIds:
            raise ValueError("project must contain at least one workflow")
        if len(orderedIds) != len(set(orderedIds)):
            raise ValueError("workflowOrder must not contain duplicate workflow ids")
        if set(orderedIds) != workflowIds:
            raise ValueError("workflowOrder must contain every workflow exactly once")
        if self.entryWorkflowId not in workflowIds:
            raise ValueError("entryWorkflowId must reference an existing workflow")

        for sourceWorkflowId, workflow in self.workflows.items():
            seenNodeIds: set[str] = set()
            duplicateNodeIds: set[str] = set()
            for node in workflow.nodes:
                if node.nodeId in seenNodeIds:
                    duplicateNodeIds.add(node.nodeId)
                seenNodeIds.add(node.nodeId)
            if duplicateNodeIds:
                raise ValueError(
                    f"{sourceWorkflowId}.nodes must not contain duplicate nodeId values: "
                    + ", ".join(sorted(duplicateNodeIds))
                )
            for node in workflow.nodes:
                references: list[tuple[str, object]] = []
                if node.kind == "subflow":
                    references.append(("targetWorkflowId", node.targetWorkflowId))
                if node.kind == "loop":
                    references.extend(
                        (field, node.loop.get(field)) for field in loopWorkflowReferenceFields(node.loop)
                    )
                for fieldName, targetWorkflowId in references:
                    if targetWorkflowId is None:
                        continue
                    if (
                        not isinstance(targetWorkflowId, str)
                        or targetWorkflowId not in workflowIds
                    ):
                        raise ValueError(
                            f"{sourceWorkflowId}.{node.nodeId}.{fieldName} must "
                            "reference an existing workflow"
                        )
        return self

    def toPayload(self) -> dict[str, object]:
        return self.model_dump(mode="python")


def parseProjectDocument(payload: dict[str, object]) -> ProjectDocument:
    return ProjectDocument.model_validate(payload)
