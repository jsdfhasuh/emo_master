from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4

from emo_master.core.project.migration import migrateProjectPayload, utc_now_iso
from emo_master.core.project.models import ProjectDocument
from emo_master.core.contracts.port_compatibility import arePortTypesCompatible
from emo_master.core.contracts.port_types import normalizePortType


_WORKFLOW_RELATION_LABELS = {
    "subflow": "Subflow",
    "repeat-body": "Repeat · Body",
    "foreach-body": "ForEach · Body",
    "while-body": "While · Body",
    "while-condition": "While · Condition",
    "loop-body": "Loop · Body",
    "loop-condition": "Loop · Condition",
}

_BOUNDARY_NODE_DEFAULT_POSITIONS = {
    "workflow_input": (20.0, 20.0),
    "workflow_output": (360.0, 20.0),
}


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
        self.ensureBoundaryNodes()

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
        self.ensureBoundaryNodes()

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
        self.ensureBoundaryNodes(workflowId)
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
            node.get("nodeId") for node in nodes if isinstance(node.get("nodeId"), str)
        }
        nodes.extend(
            node
            for node in boundaryNodes
            if isinstance(node.get("nodeId"), str) and node.get("nodeId") not in nodeIds
        )
        nodeIds = {
            node.get("nodeId") for node in nodes if isinstance(node.get("nodeId"), str)
        }
        workflow.nodes = nodes
        capturedEdges = (
            [dict(edge) for edge in rawEdges if isinstance(edge, dict)]
            if isinstance(rawEdges, list)
            else []
        )
        workflow.edges = capturedEdges
        previousPositions = workflow.layout.get("nodePositions", {})
        preservedPositions = (
            deepcopy(previousPositions) if isinstance(previousPositions, dict) else {}
        )
        positions = nodePositions or {}
        for nodeId, position in positions.items():
            if not isinstance(nodeId, str) or not isinstance(position, tuple):
                continue
            if len(position) != 2:
                continue
            preservedPositions[nodeId] = {
                "x": float(position[0]),
                "y": float(position[1]),
            }
        workflow.layout = {
            "nodePositions": {
                nodeId: value
                for nodeId, value in preservedPositions.items()
                if nodeId in nodeIds and isinstance(value, dict)
            }
        }
        self._pruneBoundaryEdges(workflow)

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
        self.ensureBoundaryNodes(candidate)
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

    def getWorkflowDependencyReferences(self) -> list[dict[str, object]]:
        """Return ordered, lossless references between editable workflows."""
        references: list[dict[str, object]] = []

        def appendReference(
            sourceWorkflowId: str,
            sourceNodeId: str,
            relation: str,
            targetWorkflowId: object,
        ) -> None:
            target = (
                targetWorkflowId
                if isinstance(targetWorkflowId, str) and targetWorkflowId != ""
                else None
            )
            references.append(
                {
                    "sourceWorkflowId": sourceWorkflowId,
                    "sourceNodeId": sourceNodeId,
                    "relation": relation,
                    "relationLabel": _WORKFLOW_RELATION_LABELS[relation],
                    "targetWorkflowId": target,
                }
            )

        for sourceWorkflowId in self._orderedWorkflowIds():
            workflow = self.workflows[sourceWorkflowId]
            for node in workflow.nodes:
                if not isinstance(node, dict):
                    continue
                rawNodeId = node.get("nodeId")
                sourceNodeId = rawNodeId if isinstance(rawNodeId, str) else ""
                kind = node.get("kind")
                if kind == "subflow":
                    appendReference(
                        sourceWorkflowId,
                        sourceNodeId,
                        "subflow",
                        node.get("targetWorkflowId"),
                    )
                    continue
                if kind != "loop":
                    continue
                loop = node.get("loop")
                if not isinstance(loop, dict):
                    appendReference(
                        sourceWorkflowId,
                        sourceNodeId,
                        "loop-body",
                        None,
                    )
                    continue
                rawMode = loop.get("mode")
                mode = rawMode if isinstance(rawMode, str) else ""
                bodyRelation = {
                    "repeat": "repeat-body",
                    "foreach": "foreach-body",
                    "while": "while-body",
                }.get(mode, "loop-body")
                appendReference(
                    sourceWorkflowId,
                    sourceNodeId,
                    bodyRelation,
                    loop.get("bodyWorkflowId"),
                )
                if mode == "while" or "conditionWorkflowId" in loop:
                    appendReference(
                        sourceWorkflowId,
                        sourceNodeId,
                        "while-condition" if mode == "while" else "loop-condition",
                        loop.get("conditionWorkflowId"),
                    )
        return references

    def getWorkflowDependencyTree(self) -> list[dict[str, object]]:
        """Build the entry-rooted dependency tree plus unreachable components."""
        workflowIds = self._orderedWorkflowIds()
        references = self.getWorkflowDependencyReferences()
        referencesBySource: dict[str, list[dict[str, object]]] = {
            workflowId: [] for workflowId in workflowIds
        }
        incomingCount = {workflowId: 0 for workflowId in workflowIds}
        for reference in references:
            sourceWorkflowId = reference["sourceWorkflowId"]
            if isinstance(sourceWorkflowId, str):
                referencesBySource.setdefault(sourceWorkflowId, []).append(reference)
            targetWorkflowId = reference["targetWorkflowId"]
            if isinstance(targetWorkflowId, str) and targetWorkflowId in incomingCount:
                incomingCount[targetWorkflowId] += 1

        reachable: set[str] = set()
        pending = [self.entryWorkflowId]
        while pending:
            workflowId = pending.pop()
            if workflowId in reachable or workflowId not in self.workflows:
                continue
            reachable.add(workflowId)
            for reference in reversed(referencesBySource.get(workflowId, [])):
                targetWorkflowId = reference["targetWorkflowId"]
                if (
                    isinstance(targetWorkflowId, str)
                    and targetWorkflowId in self.workflows
                ):
                    pending.append(targetWorkflowId)

        def workflowEntry(
            workflowId: str,
            relation: str,
            relationLabel: str,
            sourceWorkflowId: str | None,
            sourceNodeId: str | None,
            path: tuple[str, ...],
        ) -> dict[str, object]:
            workflow = self.workflows[workflowId]
            isCycle = workflowId in path
            entry: dict[str, object] = {
                "itemType": "workflow",
                "workflowId": workflowId,
                "name": workflow.name,
                "relation": relation,
                "relationLabel": relationLabel,
                "sourceWorkflowId": sourceWorkflowId,
                "sourceNodeId": sourceNodeId,
                "status": "cycle" if isCycle else "normal",
                "exists": True,
                "isEntry": workflowId == self.entryWorkflowId,
                "isReachable": workflowId in reachable,
                "isUnreferenced": incomingCount.get(workflowId, 0) == 0,
                "children": [],
            }
            if isCycle:
                return entry
            nextPath = (*path, workflowId)
            children = [
                referenceEntry(reference, nextPath)
                for reference in referencesBySource.get(workflowId, [])
            ]
            entry["children"] = children
            return entry

        def referenceEntry(
            reference: dict[str, object], path: tuple[str, ...]
        ) -> dict[str, object]:
            relation = str(reference["relation"])
            relationLabel = str(reference["relationLabel"])
            sourceWorkflowId = str(reference["sourceWorkflowId"])
            sourceNodeId = str(reference["sourceNodeId"])
            targetWorkflowId = reference["targetWorkflowId"]
            if (
                not isinstance(targetWorkflowId, str)
                or targetWorkflowId not in self.workflows
            ):
                return {
                    "itemType": "workflow",
                    "workflowId": targetWorkflowId,
                    "name": targetWorkflowId or "未设置",
                    "relation": relation,
                    "relationLabel": relationLabel,
                    "sourceWorkflowId": sourceWorkflowId,
                    "sourceNodeId": sourceNodeId,
                    "status": "missing",
                    "exists": False,
                    "isEntry": False,
                    "isReachable": False,
                    "isUnreferenced": False,
                    "children": [],
                }
            return workflowEntry(
                targetWorkflowId,
                relation,
                relationLabel,
                sourceWorkflowId,
                sourceNodeId,
                path,
            )

        tree: list[dict[str, object]] = []
        if self.entryWorkflowId in self.workflows:
            tree.append(
                workflowEntry(
                    self.entryWorkflowId,
                    "entry",
                    "入口",
                    None,
                    None,
                    (),
                )
            )

        unreachableIds = [
            workflowId for workflowId in workflowIds if workflowId not in reachable
        ]
        if not unreachableIds:
            return tree

        unreachableSet = set(unreachableIds)
        incomingWithin = {workflowId: 0 for workflowId in unreachableIds}
        for sourceWorkflowId in unreachableIds:
            for reference in referencesBySource.get(sourceWorkflowId, []):
                targetWorkflowId = reference["targetWorkflowId"]
                if (
                    isinstance(targetWorkflowId, str)
                    and targetWorkflowId in unreachableSet
                ):
                    incomingWithin[targetWorkflowId] += 1

        rootCandidates = [
            workflowId
            for workflowId in unreachableIds
            if incomingWithin[workflowId] == 0
        ]
        covered: set[str] = set()

        def coverComponent(workflowId: str) -> None:
            if workflowId in covered or workflowId not in unreachableSet:
                return
            covered.add(workflowId)
            for reference in referencesBySource.get(workflowId, []):
                targetWorkflowId = reference["targetWorkflowId"]
                if isinstance(targetWorkflowId, str):
                    coverComponent(targetWorkflowId)

        unusedRoots: list[dict[str, object]] = []
        for workflowId in [*rootCandidates, *unreachableIds]:
            if workflowId in covered:
                continue
            unusedRoots.append(
                workflowEntry(
                    workflowId,
                    "unreachable-root",
                    "入口不可达",
                    None,
                    None,
                    (),
                )
            )
            coverComponent(workflowId)
        tree.append(
            {
                "itemType": "group",
                "label": "未使用工作流（入口不可达）",
                "count": len(unreachableIds),
                "workflowIds": list(unreachableIds),
                "children": unusedRoots,
            }
        )
        return tree

    def _orderedWorkflowIds(self) -> list[str]:
        ordered = [
            workflowId
            for workflowId in self.workflowOrder
            if workflowId in self.workflows
        ]
        orderedSet = set(ordered)
        ordered.extend(
            workflowId for workflowId in self.workflows if workflowId not in orderedSet
        )
        return ordered

    def deleteWorkflow(self, workflowId: str) -> None:
        if workflowId not in self.workflows:
            raise KeyError(workflowId)
        if len(self.workflows) <= 1:
            raise ValueError("a project must keep at least one workflow")
        references = self.referencesTo(workflowId)
        if references:
            raise ValueError(f"workflow is referenced by: {', '.join(references)}")
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
        self.ensureBoundaryNodes()
        project = deepcopy(self.project)
        if projectName:
            project["name"] = projectName
        project["updatedAt"] = utc_now_iso()
        revision = project.get("revision", 1)
        project["revision"] = (revision if isinstance(revision, int) else 1) + 1
        return {
            "schemaVersion": "2.1",
            "project": project,
            "entryWorkflowId": self.entryWorkflowId,
            "workflowOrder": list(self.workflowOrder),
            "workflows": {
                workflowId: {
                    "name": workflow.name,
                    "inputs": deepcopy(workflow.inputs),
                    "outputs": deepcopy(workflow.outputs),
                    "nodes": _serializedNodes(
                        workflowId, workflow.nodes, workflow.inputs, workflow.outputs
                    ),
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

    def ensureBoundaryNodes(self, workflowId: str | None = None) -> None:
        selectedIds = (
            [workflowId] if workflowId is not None else list(self.workflowOrder)
        )
        for selectedId in selectedIds:
            workflow = self.workflows.get(selectedId)
            if workflow is None:
                continue
            self._ensureBoundaryNodesForWorkflow(workflow)
            self._ensureBoundaryNodePositions(workflow)
            self._pruneBoundaryEdges(workflow)

    def _ensureBoundaryNodesForWorkflow(self, workflow: WorkflowState) -> None:
        normalizedNodes: list[dict[str, object]] = []
        seenKinds: set[str] = set()
        for rawNode in workflow.nodes:
            if not isinstance(rawNode, dict):
                continue
            node = deepcopy(rawNode)
            kind = node.get("kind")
            if isinstance(kind, str) and kind in {"workflow_input", "workflow_output"}:
                if kind in seenKinds:
                    continue
                seenKinds.add(kind)
                self._normalizeBoundaryNode(workflow, node, kind)
            normalizedNodes.append(node)

        if "workflow_input" not in seenKinds:
            node = {"nodeId": self._boundaryNodeId(workflow, "workflow_input")}
            self._normalizeBoundaryNode(workflow, node, "workflow_input")
            normalizedNodes.append(node)
        if "workflow_output" not in seenKinds:
            node = {"nodeId": self._boundaryNodeId(workflow, "workflow_output")}
            self._normalizeBoundaryNode(workflow, node, "workflow_output")
            normalizedNodes.append(node)
        workflow.nodes = normalizedNodes

    def _ensureBoundaryNodePositions(self, workflow: WorkflowState) -> None:
        layout = deepcopy(workflow.layout)
        rawPositions = layout.get("nodePositions", {})
        positions = deepcopy(rawPositions) if isinstance(rawPositions, dict) else {}
        for node in workflow.nodes:
            kind = node.get("kind")
            nodeId = node.get("nodeId")
            if (
                not isinstance(kind, str)
                or kind not in _BOUNDARY_NODE_DEFAULT_POSITIONS
                or not isinstance(nodeId, str)
            ):
                continue
            defaultX, defaultY = defaultNodePosition(kind)
            rawPosition = positions.get(nodeId)
            position = dict(rawPosition) if isinstance(rawPosition, dict) else {}
            if not _isCoordinate(position.get("x")):
                position["x"] = defaultX
            if not _isCoordinate(position.get("y")):
                position["y"] = defaultY
            positions[nodeId] = position
        layout["nodePositions"] = positions
        workflow.layout = layout

    def _normalizeBoundaryNode(
        self, workflow: WorkflowState, node: dict[str, object], kind: str
    ) -> None:
        rawNodeId = node.get("nodeId")
        nodeId = (
            rawNodeId
            if isinstance(rawNodeId, str) and rawNodeId
            else self._boundaryNodeId(workflow, kind)
        )
        node.update(
            {
                "nodeId": nodeId,
                "operatorId": "",
                "displayName": "Workflow Input"
                if kind == "workflow_input"
                else "Workflow Output",
                "inputPorts": {}
                if kind == "workflow_input"
                else portTypes(workflow.outputs),
                "outputPorts": portTypes(workflow.inputs)
                if kind == "workflow_input"
                else {},
                "paramSchema": {},
                "params": {},
                "kind": kind,
                "targetWorkflowId": None,
                "loop": {},
            }
        )

    def _boundaryNodeId(self, workflow: WorkflowState, kind: str) -> str:
        prefix = (
            "__workflow_input__" if kind == "workflow_input" else "__workflow_output__"
        )
        return (
            prefix
            if workflow.workflowId == "main"
            else f"{prefix}:{workflow.workflowId}"
        )

    def _pruneBoundaryEdges(self, workflow: WorkflowState) -> None:
        nodesById = {
            node.get("nodeId"): node
            for node in workflow.nodes
            if isinstance(node, dict) and isinstance(node.get("nodeId"), str)
        }
        boundaryIds = {
            nodeId
            for nodeId, node in nodesById.items()
            if isinstance(node, dict)
            and node.get("kind") in {"workflow_input", "workflow_output"}
        }
        validEdges: list[dict[str, object]] = []
        seen: set[tuple[object, object, object, object]] = set()
        for rawEdge in workflow.edges:
            if not isinstance(rawEdge, dict):
                continue
            fromNode = rawEdge.get("fromNode")
            fromPort = rawEdge.get("fromPort")
            toNode = rawEdge.get("toNode")
            toPort = rawEdge.get("toPort")
            edgeKey = (fromNode, fromPort, toNode, toPort)
            if not all(isinstance(value, str) for value in edgeKey):
                continue
            if fromNode not in nodesById or toNode not in nodesById:
                continue
            if edgeKey in seen:
                continue
            if fromNode in boundaryIds or toNode in boundaryIds:
                sourceNode = nodesById[fromNode]
                targetNode = nodesById[toNode]
                sourcePorts = sourceNode.get("outputPorts", {})
                targetPorts = targetNode.get("inputPorts", {})
                sourceType = (
                    sourcePorts.get(fromPort) if isinstance(sourcePorts, dict) else None
                )
                targetType = (
                    targetPorts.get(toPort) if isinstance(targetPorts, dict) else None
                )
                if not isinstance(sourceType, str) or not isinstance(targetType, str):
                    continue
                if not arePortTypesCompatible(sourceType, targetType):
                    continue
            seen.add(edgeKey)
            validEdges.append(dict(rawEdge))
        workflow.edges = validEdges


def _slug(value: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in value)
    return "-".join(part for part in normalized.split("-") if part)[:48]


def defaultNodePosition(kind: str) -> tuple[float, float]:
    return _BOUNDARY_NODE_DEFAULT_POSITIONS.get(kind, (20.0, 20.0))


def _isCoordinate(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _serializedNodes(
    workflowId: str,
    nodes: list[dict[str, object]],
    inputs: dict[str, object] | None = None,
    outputs: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    serialized = deepcopy(nodes)
    kinds = {node.get("kind") for node in serialized if isinstance(node, dict)}
    if "workflow_input" not in kinds:
        inputId = (
            "__workflow_input__"
            if workflowId == "main"
            else f"__workflow_input__:{workflowId}"
        )
        serialized.append(
            {
                "nodeId": inputId,
                "operatorId": "",
                "displayName": "Workflow Input",
                "inputPorts": {},
                "outputPorts": portTypes(inputs or {}),
                "paramSchema": {},
                "params": {},
                "kind": "workflow_input",
                "targetWorkflowId": None,
                "loop": {},
            }
        )
    if "workflow_output" not in kinds:
        outputId = (
            "__workflow_output__"
            if workflowId == "main"
            else f"__workflow_output__:{workflowId}"
        )
        serialized.append(
            {
                "nodeId": outputId,
                "operatorId": "",
                "displayName": "Workflow Output",
                "inputPorts": portTypes(outputs or {}),
                "outputPorts": {},
                "paramSchema": {},
                "params": {},
                "kind": "workflow_output",
                "targetWorkflowId": None,
                "loop": {},
            }
        )
    return serialized


def portTypes(values: dict[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in values.items():
        if isinstance(value, str):
            result[name] = normalizePortType(value)
        elif isinstance(value, dict) and isinstance(value.get("type"), str):
            result[name] = normalizePortType(value)
    return result
