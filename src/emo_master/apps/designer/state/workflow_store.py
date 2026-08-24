from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4

from emo_master.core.project.migration import migrateProjectPayload, utc_now_iso
from emo_master.core.project.models import ProjectDocument


@dataclass
class WorkflowState:
    workflowId: str
    name: str
    inputs: dict[str, object] = field(default_factory=dict)
    outputs: dict[str, object] = field(default_factory=dict)
    nodes: list[dict[str, object]] = field(default_factory=list)
    edges: list[dict[str, object]] = field(default_factory=list)
    layout: dict[str, object] = field(default_factory=lambda: {"nodePositions": {}})

    def graph(self) -> dict[str, object]:
        return {"nodes": deepcopy(self.nodes), "edges": deepcopy(self.edges)}


class WorkflowStore:
    """Owns the editable workflow collection independently from the Qt canvas."""

    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self.project: dict[str, object] = {}
        self.runtime: dict[str, object] = {}
        self.dependencies: dict[str, object] = {"operators": []}
        self.devices: dict[str, object] = {"bindings": {}}
        self.workflowOrder: list[str] = []
        self.entryWorkflowId = ""
        self.activeWorkflowId = ""
        self.workflows: dict[str, WorkflowState] = {}
        self.reset()
        if payload is not None:
            self.loadPayload(payload)

    def reset(self) -> None:
        self.project = {
            "projectId": str(uuid4()),
            "name": "project",
            "revision": 1,
            "createdAt": utc_now_iso(),
            "updatedAt": utc_now_iso(),
        }
        self.runtime = {
            "maxConcurrentJobs": 2,
            "gracefulStopTimeoutMs": 5000,
            "heartbeatTimeoutMs": 5000,
            "eventRetentionPerJob": 10000,
        }
        self.dependencies = {"operators": []}
        self.devices = {"bindings": {}}
        self.workflowOrder = ["main"]
        self.entryWorkflowId = "main"
        self.activeWorkflowId = "main"
        self.workflows = {"main": WorkflowState("main", "Main")}

    def loadPayload(self, payload: dict[str, object]) -> None:
        canonical = migrateProjectPayload(payload)
        document = ProjectDocument.model_validate(canonical)
        self.project = document.project.model_dump(mode="python")
        self.runtime = document.runtime.model_dump(mode="python")
        self.dependencies = document.dependencies.model_dump(mode="python")
        self.devices = document.devices.model_dump(mode="python")
        self.workflowOrder = list(document.workflowOrder)
        self.entryWorkflowId = document.entryWorkflowId
        self.activeWorkflowId = self.entryWorkflowId
        self.workflows = {}
        for workflowId in self.workflowOrder:
            workflow = document.workflows[workflowId]
            self.workflows[workflowId] = WorkflowState(
                workflowId=workflowId,
                name=workflow.name,
                inputs=deepcopy(workflow.inputs),
                outputs=deepcopy(workflow.outputs),
                nodes=[node.model_dump(mode="python") for node in workflow.nodes],
                edges=[edge.model_dump(mode="python") for edge in workflow.edges],
                layout=workflow.layout.model_dump(mode="python"),
            )

    def loadProjectPayload(self, payload: dict[str, object]) -> None:
        self.loadPayload(payload)

    def get(self, workflowId: str | None = None) -> WorkflowState:
        selected = workflowId or self.activeWorkflowId
        workflow = self.workflows.get(selected)
        if workflow is None:
            raise KeyError(selected)
        return workflow

    def getWorkflow(self, workflowId: str | None = None) -> WorkflowState:
        return self.get(workflowId)

    def listWorkflows(self) -> list[WorkflowState]:
        return [self.workflows[workflowId] for workflowId in self.workflowOrder]

    def captureGraph(
        self,
        workflowId: str,
        graph: dict[str, object],
        nodePositions: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        workflow = self.get(workflowId)
        rawNodes = graph.get("nodes", [])
        rawEdges = graph.get("edges", [])
        nodes: list[dict[str, object]] = []
        for rawNode in rawNodes if isinstance(rawNodes, list) else []:
            if not isinstance(rawNode, dict):
                continue
            node = dict(rawNode)
            node.pop("x", None)
            node.pop("y", None)
            nodes.append(node)
        boundaryNodes = [
            deepcopy(node)
            for node in workflow.nodes
            if node.get("kind") in {"workflow_input", "workflow_output"}
        ]
        nodeIds = {
            node.get("nodeId")
            for node in nodes
            if isinstance(node.get("nodeId"), str)
        }
        nodes.extend(
            node
            for node in boundaryNodes
            if isinstance(node.get("nodeId"), str)
            and node.get("nodeId") not in nodeIds
        )
        workflow.nodes = nodes
        capturedEdges = (
            [dict(edge) for edge in rawEdges if isinstance(edge, dict)]
            if isinstance(rawEdges, list)
            else []
        )
        boundaryIds = {
            node.get("nodeId")
            for node in boundaryNodes
            if isinstance(node.get("nodeId"), str)
        }
        capturedEdgeKeys = {
            tuple(edge.get(key) for key in ("fromNode", "fromPort", "toNode", "toPort"))
            for edge in capturedEdges
        }
        capturedEdges.extend(
            deepcopy(edge)
            for edge in workflow.edges
            if (
                edge.get("fromNode") in boundaryIds
                or edge.get("toNode") in boundaryIds
            )
            and tuple(
                edge.get(key) for key in ("fromNode", "fromPort", "toNode", "toPort")
            )
            not in capturedEdgeKeys
        )
        workflow.edges = capturedEdges
        positions = nodePositions or {}
        workflow.layout = {
            "nodePositions": {
                nodeId: {"x": float(position[0]), "y": float(position[1])}
                for nodeId, position in positions.items()
            }
        }

    def captureActiveGraph(
        self,
        graph: dict[str, object],
        nodePositions: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self.captureGraph(self.activeWorkflowId, graph, nodePositions)

    def graphFor(self, workflowId: str | None = None) -> dict[str, object]:
        return self.get(workflowId).graph()

    def layoutFor(self, workflowId: str | None = None) -> dict[str, object]:
        return deepcopy(self.get(workflowId).layout)

    def addWorkflow(
        self,
        name: str,
        workflowId: str | None = None,
        inputs: dict[str, object] | None = None,
        outputs: dict[str, object] | None = None,
    ) -> str:
        base = workflowId or _slug(name) or "workflow"
        candidate = base
        index = 2
        while candidate in self.workflows:
            candidate = f"{base}-{index}"
            index += 1
        self.workflows[candidate] = WorkflowState(
            candidate,
            name.strip() or candidate,
            inputs=deepcopy(inputs or {}),
            outputs=deepcopy(outputs or {}),
        )
        self.workflowOrder.append(candidate)
        if self.activeWorkflowId == "":
            self.activeWorkflowId = candidate
        return candidate

    def createWorkflow(self, name: str, workflowId: str | None = None) -> str:
        return self.addWorkflow(name, workflowId)

    def renameWorkflow(self, workflowId: str, name: str) -> None:
        value = name.strip()
        if value == "":
            raise ValueError("workflow name is required")
        self.get(workflowId).name = value

    def rename(self, workflowId: str, name: str) -> None:
        self.renameWorkflow(workflowId, name)

    def referencesTo(self, workflowId: str) -> list[str]:
        references: list[str] = []
        for sourceId, workflow in self.workflows.items():
            for node in workflow.nodes:
                if node.get("targetWorkflowId") == workflowId:
                    references.append(sourceId)
                loop = node.get("loop")
                if isinstance(loop, dict) and workflowId in {
                    loop.get("bodyWorkflowId"),
                    loop.get("conditionWorkflowId"),
                }:
                    references.append(sourceId)
        return sorted(set(references))

    def deleteWorkflow(self, workflowId: str) -> None:
        if workflowId not in self.workflows:
            raise KeyError(workflowId)
        if len(self.workflows) <= 1:
            raise ValueError("a project must keep at least one workflow")
        references = self.referencesTo(workflowId)
        if references:
            raise ValueError(
                f"workflow is referenced by: {', '.join(references)}"
            )
        del self.workflows[workflowId]
        self.workflowOrder = [item for item in self.workflowOrder if item != workflowId]
        if self.entryWorkflowId == workflowId:
            self.entryWorkflowId = self.workflowOrder[0]
        if self.activeWorkflowId == workflowId:
            self.activeWorkflowId = self.entryWorkflowId

    def delete(self, workflowId: str) -> None:
        self.deleteWorkflow(workflowId)

    def setEntryWorkflow(self, workflowId: str) -> None:
        self.get(workflowId)
        self.entryWorkflowId = workflowId

    def setActiveWorkflow(self, workflowId: str) -> None:
        self.get(workflowId)
        self.activeWorkflowId = workflowId

    def toPayload(self, projectName: str | None = None) -> dict[str, object]:
        project = deepcopy(self.project)
        if projectName:
            project["name"] = projectName
        project["updatedAt"] = utc_now_iso()
        revision = project.get("revision", 1)
        project["revision"] = (revision if isinstance(revision, int) else 1) + 1
        return {
            "schemaVersion": "2.0",
            "project": project,
            "entryWorkflowId": self.entryWorkflowId,
            "workflowOrder": list(self.workflowOrder),
            "workflows": {
                workflowId: {
                    "name": workflow.name,
                    "inputs": deepcopy(workflow.inputs),
                    "outputs": deepcopy(workflow.outputs),
                    "nodes": _serializedNodes(workflowId, workflow.nodes),
                    "edges": deepcopy(workflow.edges),
                    "layout": deepcopy(workflow.layout),
                }
                for workflowId, workflow in self.workflows.items()
            },
            "runtime": deepcopy(self.runtime),
            "dependencies": deepcopy(self.dependencies),
            "devices": deepcopy(self.devices),
        }

    def toProjectPayload(self, projectName: str | None = None) -> dict[str, object]:
        return self.toPayload(projectName)

    def commitSavedPayload(self, payload: dict[str, object]) -> None:
        """Advance project metadata only after an atomic save succeeds."""
        project = payload.get("project")
        if isinstance(project, dict):
            self.project = deepcopy(project)


def _slug(value: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in value)
    return "-".join(part for part in normalized.split("-") if part)[:48]


def _serializedNodes(workflowId: str, nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    serialized = deepcopy(nodes)
    kinds = {
        node.get("kind") for node in serialized if isinstance(node, dict)
    }
    if "workflow_input" not in kinds:
        inputId = "__workflow_input__" if workflowId == "main" else f"__workflow_input__:{workflowId}"
        serialized.append({"nodeId": inputId, "kind": "workflow_input"})
    if "workflow_output" not in kinds:
        outputId = "__workflow_output__" if workflowId == "main" else f"__workflow_output__:{workflowId}"
        serialized.append({"nodeId": outputId, "kind": "workflow_output"})
    return serialized


def portTypes(values: dict[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in values.items():
        if isinstance(value, str):
            result[name] = value
        elif isinstance(value, dict) and isinstance(value.get("type"), str):
            result[name] = str(value["type"])
    return result
