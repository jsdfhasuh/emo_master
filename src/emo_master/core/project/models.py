from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, PrivateAttr, model_validator, model_serializer

from emo_master.core.presentation.models import Presentation
from emo_master.core.project.resources import ResourcePlan
from emo_master.core.project.communication import CommunicationSettings
from emo_master.core.project.global_variables import VariableDefinition, VariableBinding
from emo_master.core.project.devices import (
    DeviceBinding, DeviceDefinition, DeviceId, DeviceValueBinding, validateSources,
)
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
    # None explicitly disables the admission count, not resource/ownership checks.
    maxConcurrentJobs: int | None = Field(default=2, ge=1)
    gracefulStopTimeoutMs: int = Field(default=5000, ge=0)
    heartbeatTimeoutMs: int = Field(default=5000, ge=100)
    eventRetentionPerJob: int = Field(default=10000, ge=1)
    # Missing means follow entryWorkflowId, preserving legacy single-entry files.
    startupWorkflowIds: list[str] | None = Field(default=None, min_length=1)

    @model_serializer(mode="wrap")
    def serializeStartup(self, handler):
        payload = handler(self)
        if self.startupWorkflowIds is None:
            payload.pop("startupWorkflowIds", None)
        return payload


class ProductionSettings(StrictModel):
    autoStart: bool = False
    mode: Literal["single", "continuous"] = "single"
    cycleIntervalMs: int = Field(default=100, ge=1)
    inputs: dict[str, object] = Field(default_factory=dict)


class WorkflowNode(StrictModel):
    nodeId: str
    kind: Literal[
        "operator", "workflow_input", "workflow_output", "subflow", "loop", "dispatch", "join"
    ] = "operator"
    operatorId: str | None = None
    displayName: str | None = None
    inputPorts: dict[str, str] = Field(default_factory=dict)
    outputPorts: dict[str, str] = Field(default_factory=dict)
    paramSchema: dict[str, object] = Field(default_factory=dict)
    params: dict[str, object] = Field(default_factory=dict)
    globalVariableBindings: list[VariableBinding] = Field(default_factory=list)
    deviceBindings: dict[DeviceId, DeviceBinding] = Field(default_factory=dict)
    deviceOverrides: dict[DeviceId, dict[str, JsonValue]] = Field(default_factory=dict)
    deviceValueBindings: dict[DeviceId, dict[str, DeviceValueBinding]] = Field(default_factory=dict)
    targetWorkflowId: str | None = None
    loop: dict[str, object] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def serializeBindings(self, handler):
        payload = handler(self)
        if not self.globalVariableBindings:
            payload.pop("globalVariableBindings", None)
        for key in ("deviceBindings", "deviceOverrides", "deviceValueBindings"):
            if not getattr(self, key):
                payload.pop(key, None)
        return payload

    @model_validator(mode="after")
    def validateDeviceSources(self):
        validateSources(self.deviceBindings, self.deviceOverrides, self.deviceValueBindings)
        if self.deviceBindings and self.kind != "operator":
            raise ValueError("device bindings belong to operator nodes")
        return self


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
    schemaVersion: Literal["1.0"] | None = None
    definitions: dict[DeviceId, DeviceDefinition] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def serializeCatalog(self, handler):
        payload = handler(self)
        if self.schemaVersion is None:
            payload.pop("schemaVersion", None)
            if not self.definitions:
                payload.pop("definitions", None)
        return payload


class ProjectDocument(StrictModel):
    # Execution-only asset locations never enter portable snapshots or digests.
    _pageResourcePaths: dict[str, str] = PrivateAttr(default_factory=dict)
    schemaVersion: Literal["2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7"]
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
    communication: CommunicationSettings | None = None

    @model_validator(mode="after")
    def validateVersion(self) -> "ProjectDocument":
        if self.schemaVersion not in {"2.2", "2.3", "2.4", "2.5", "2.6", "2.7"}:
            if {"presentation", "resources"} & self.model_fields_set:
                raise ValueError("presentation/resources require explicit project 2.2 migration")
        elif self.presentation is None or self.resources is None:
            raise ValueError("project 2.2/2.3 requires presentation and resources")
        if self.schemaVersion in {"2.3", "2.4", "2.5", "2.6", "2.7"}:
            if self.production is None:
                raise ValueError("project 2.3 requires production settings")
        elif "production" in self.model_fields_set:
            raise ValueError("production requires explicit project 2.3 migration")
        if self.schemaVersion not in {"2.4", "2.5", "2.6", "2.7"} and (self.globalVariables or
            (self.presentation and any(source.kind == "global_variable" for source in self.presentation.dataSources.values())) or any(
            node.globalVariableBindings or node.loop.get("conditionMode") == "globalVariable"
            or node.operatorId in {"vision.state.variable_read", "vision.state.variable_write"}
            for workflow in self.workflows.values() for node in workflow.nodes
        )):
            raise ValueError("global variables require explicit project 2.4 migration")
        if self.schemaVersion in {"2.5", "2.6"}:
            if self.communication is None:
                raise ValueError("project 2.5 requires communication settings")
        elif self.communication is not None:
            raise ValueError("communication requires explicit project 2.5 migration")
        if self.schemaVersion in {"2.6", "2.7"}:
            if self.devices.schemaVersion != "1.0":
                raise ValueError("project 2.6 requires device catalog schemaVersion 1.0")
        elif (self.devices.schemaVersion is not None or "definitions" in self.devices.model_fields_set
              or any({"deviceBindings", "deviceOverrides", "deviceValueBindings"} & node.model_fields_set
                     for workflow in self.workflows.values() for node in workflow.nodes)):
            raise ValueError("device catalog and node sources require explicit project 2.6 migration")
        for workflow in self.workflows.values():
            for node in workflow.nodes:
                for binding in node.deviceBindings.values():
                    if binding.deviceId not in self.devices.definitions:
                        raise ValueError(f"unknown device reference: {binding.deviceId}")
        if self.resources is not None:
            from emo_master.core.project.resources import DeviceParameterTarget, ResourceBinding, SiteBinding
            resourceBindings: list[ResourceBinding | SiteBinding] = [
                *self.resources.parameterBindings, *self.resources.siteBindings]
            for resourceBinding in resourceBindings:
                target = resourceBinding.target
                if isinstance(target, DeviceParameterTarget):
                    if self.schemaVersion not in {"2.6", "2.7"}:
                        raise ValueError("device resource targets require explicit project 2.6 migration")
                    if target.deviceId not in self.devices.definitions:
                        raise ValueError(f"unknown device resource target: {target.deviceId}")
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
        if self.schemaVersion not in {"2.2", "2.3", "2.4", "2.5", "2.6", "2.7"}:
            payload.pop("presentation", None)
            payload.pop("resources", None)
        if self.schemaVersion not in {"2.3", "2.4", "2.5", "2.6", "2.7"}:
            payload.pop("production", None)
        if self.schemaVersion not in {"2.4", "2.5", "2.6", "2.7"}:
            payload.pop("globalVariables", None)
            for workflow in payload["workflows"].values():
                for node in workflow["nodes"]:
                    node.pop("globalVariableBindings", None)
        if self.schemaVersion not in {"2.5", "2.6"}:
            payload.pop("communication", None)
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
        startup = self.runtime.startupWorkflowIds
        if startup is not None:
            if len(startup) != len(set(startup)):
                raise ValueError("startupWorkflowIds must not contain duplicate workflow ids")
            if any(key not in workflowIds for key in startup):
                raise ValueError("startupWorkflowIds must reference existing workflows")
            limit = self.runtime.maxConcurrentJobs
            if limit is not None and len(startup) > limit:
                raise ValueError("startupWorkflowIds exceeds maxConcurrentJobs")

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

    def startupWorkflows(self) -> list[str]:
        return list(self.runtime.startupWorkflowIds or [self.entryWorkflowId])


def parseProjectDocument(payload: dict[str, object]) -> ProjectDocument:
    return ProjectDocument.model_validate(payload)
