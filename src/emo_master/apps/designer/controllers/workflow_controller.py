from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Callable, Collection

from emo_master.apps.designer.state.flow_graph_model import FlowGraphModel
from emo_master.apps.designer.state.workflow_package import (
    WorkflowPackageExportResult,
    WorkflowPackageImportPreview,
    WorkflowPackageImportResult,
    buildWorkflowPackage,
    importWorkflowPackage,
    loadWorkflowPackage,
    previewWorkflowPackageImport as buildWorkflowPackageImportPreview,
    replaceWorkflowStoreState,
    writeWorkflowPackage,
)
from emo_master.apps.designer.state.workflow_store import (
    WorkflowStore,
    defaultNodePosition,
    portTypes,
)
from emo_master.apps.designer.ui.flow_scene import FlowEdgeViewModel, FlowNodeViewModel
from emo_master.core.contracts.port_compatibility import arePortTypesCompatible
from emo_master.core.workflow.loop_contracts import LoopContract, deriveLoopContract


class WorkflowController:
    def __init__(
        self,
        workflowStore: WorkflowStore,
        flowModel: FlowGraphModel,
        flowScene,
        refreshSidebarNodeList: Callable[[], None] | None = None,
        focusGraphContent: Callable[[], None] | None = None,
        updateToolbarState: Callable[[], None] | None = None,
    ) -> None:
        self.workflowStore = workflowStore
        self.flowModel = flowModel
        self.flowScene = flowScene
        self.refreshSidebarNodeList = refreshSidebarNodeList or (lambda: None)
        self.focusGraphContent = focusGraphContent or (lambda: None)
        self.updateToolbarState = updateToolbarState or (lambda: None)
        self.lastInterfaceRefreshReport: list[str] = []

    @property
    def activeWorkflowId(self) -> str:
        return self.workflowStore.activeWorkflowId

    def loadPayload(self, payload: dict[str, object]) -> None:
        self.workflowStore.loadPayload(payload)
        self._refreshWorkflowReferences()
        self._renderActive()

    def loadProjectPayload(self, payload: dict[str, object]) -> None:
        self.loadPayload(payload)

    def captureActiveWorkflow(self) -> None:
        self.workflowStore.captureActiveGraph(
            self.flowModel.toProjectGraph(), self.flowScene.getNodePositions()
        )

    def buildPayload(self, projectName: str | None = None) -> dict[str, object]:
        self.captureActiveWorkflow()
        return self.workflowStore.toPayload(projectName)

    def saveProjectPayload(self, projectName: str | None = None) -> dict[str, object]:
        return self.buildPayload(projectName)

    def exportWorkflowPackage(
        self, workflowId: str, filePath: str | Path
    ) -> WorkflowPackageExportResult:
        self.captureActiveWorkflow()
        document = buildWorkflowPackage(self.workflowStore, workflowId)
        packagePath = writeWorkflowPackage(filePath, document)
        return WorkflowPackageExportResult(
            packagePath=packagePath,
            rootWorkflowId=workflowId,
            workflowIds=tuple(document.workflowOrder),
        )

    def previewWorkflowPackageImport(
        self,
        filePath: str | Path,
        availableOperatorIds: Collection[str] | None = None,
    ) -> WorkflowPackageImportPreview:
        document = loadWorkflowPackage(filePath)
        workingStore = self._capturedWorkflowStoreCopy()
        return buildWorkflowPackageImportPreview(
            workingStore,
            document,
            availableOperatorIds=availableOperatorIds,
        )

    def importWorkflowPackage(
        self,
        filePath: str | Path,
        insertIntoWorkflowId: str | None = None,
    ) -> WorkflowPackageImportResult:
        document = loadWorkflowPackage(filePath)
        workingStore = self._capturedWorkflowStoreCopy()
        result = importWorkflowPackage(
            workingStore,
            document,
            insertIntoWorkflowId=insertIntoWorkflowId,
        )
        replaceWorkflowStoreState(self.workflowStore, workingStore)
        self.lastInterfaceRefreshReport = self._refreshWorkflowReferences()
        self._renderActive()
        return result

    def _capturedWorkflowStoreCopy(self) -> WorkflowStore:
        workingStore = deepcopy(self.workflowStore)
        workingStore.captureActiveGraph(
            self.flowModel.toProjectGraph(),
            self.flowScene.getNodePositions(),
        )
        return workingStore

    def commitSavedPayload(self, payload: dict[str, object]) -> None:
        self.workflowStore.commitSavedPayload(payload)

    def switchWorkflow(self, workflowId: str) -> None:
        self.captureActiveWorkflow()
        self.workflowStore.setActiveWorkflow(workflowId)
        self._renderActive()

    def refreshActiveWorkflow(self) -> None:
        self.captureActiveWorkflow()
        self._renderActive()

    def createWorkflow(self, name: str = "New Workflow") -> str:
        self.captureActiveWorkflow()
        workflowId = self.workflowStore.addWorkflow(name)
        self.workflowStore.setActiveWorkflow(workflowId)
        self._renderActive()
        return workflowId

    def renameWorkflow(self, workflowId: str, name: str) -> None:
        self.workflowStore.renameWorkflow(workflowId, name)

    def deleteWorkflow(self, workflowId: str) -> None:
        self.captureActiveWorkflow()
        self.workflowStore.deleteWorkflow(workflowId)
        self._renderActive()

    def setEntryWorkflow(self, workflowId: str) -> None:
        self.workflowStore.setEntryWorkflow(workflowId)

    def getWorkflowTabLabels(self) -> list[dict[str, object]]:
        return [
            {
                "workflowId": workflow.workflowId,
                "name": workflow.name,
                "isEntry": workflow.workflowId == self.workflowStore.entryWorkflowId,
                "isActive": workflow.workflowId == self.activeWorkflowId,
            }
            for workflow in self.workflowStore.listWorkflows()
        ]

    def configureSubflowNode(self, nodeId: str, targetWorkflowId: str) -> None:
        if targetWorkflowId == self.activeWorkflowId:
            raise ValueError("subflow cannot target its current workflow")
        target = self.workflowStore.get(targetWorkflowId)
        node = self.flowModel.nodes[nodeId]
        if node.kind in {"workflow_input", "workflow_output"}:
            raise ValueError("workflow boundary nodes cannot be reconfigured")
        node.kind = "subflow"
        node.operatorId = ""
        node.targetWorkflowId = targetWorkflowId
        node.inputPorts = portTypes(target.inputs)
        node.outputPorts = portTypes(target.outputs)
        self._pruneNodeEdges(node)

    def configureLoopNode(self, nodeId: str, config: dict[str, object]) -> None:
        mode = config.get("mode")
        if mode not in {"repeat", "foreach", "while"}:
            raise ValueError("loop mode must be repeat, foreach, or while")
        for key in ("maxIterations", "timeoutMs"):
            value = config.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{key} must be a non-negative integer")
        node = self.flowModel.nodes[nodeId]
        if node.kind in {"workflow_input", "workflow_output"}:
            raise ValueError("workflow boundary nodes cannot be reconfigured")
        bodyWorkflowId = config.get("bodyWorkflowId")
        if bodyWorkflowId is None and mode in {"repeat", "foreach"}:
            bodyWorkflowId = node.targetWorkflowId
            if bodyWorkflowId is None:
                bodyWorkflowId = next(
                    (
                        workflowId
                        for workflowId in self.workflowStore.workflowOrder
                        if workflowId != self.activeWorkflowId
                    ),
                    None,
                )
        if not isinstance(bodyWorkflowId, str) or bodyWorkflowId not in self.workflowStore.workflows:
            raise ValueError("bodyWorkflowId must reference an existing workflow")
        if bodyWorkflowId == self.activeWorkflowId:
            raise ValueError("loop body cannot target its current workflow")
        if mode == "repeat":
            repeatCount = config.get("repeatCount")
            if (
                not isinstance(repeatCount, int)
                or isinstance(repeatCount, bool)
                or repeatCount < 0
            ):
                raise ValueError("repeatCount must be a non-negative integer")
        if mode == "while":
            conditionWorkflowId = config.get("conditionWorkflowId")
            if (
                not isinstance(conditionWorkflowId, str)
                or conditionWorkflowId not in self.workflowStore.workflows
            ):
                raise ValueError("conditionWorkflowId must reference an existing workflow")
            if conditionWorkflowId == self.activeWorkflowId:
                raise ValueError("loop condition cannot target its current workflow")
        normalizedConfig = deepcopy(config)
        normalizedConfig["bodyWorkflowId"] = bodyWorkflowId
        if mode != "repeat":
            normalizedConfig.pop("repeatCount", None)
        if mode != "while":
            normalizedConfig.pop("conditionWorkflowId", None)
        contract = self._loopContract(normalizedConfig)
        if contract.issues:
            raise ValueError("; ".join(issue.message for issue in contract.issues))
        # Commit only after the complete derived contract is valid.  In
        # particular, a rejected configuration must leave an existing node
        # and its edges untouched.
        node.kind = "loop"
        node.operatorId = ""
        node.targetWorkflowId = None
        node.loop = deepcopy(contract.normalizedConfig)
        node.inputPorts = dict(contract.inputPorts)
        node.outputPorts = dict(contract.outputPorts)
        self._pruneNodeEdges(node)

    def setWorkflowInterface(
        self,
        workflowId: str,
        inputs: dict[str, object],
        outputs: dict[str, object],
    ) -> list[str]:
        self.captureActiveWorkflow()
        workflow = self.workflowStore.get(workflowId)
        workflow.inputs = deepcopy(inputs)
        workflow.outputs = deepcopy(outputs)
        self.workflowStore.ensureBoundaryNodes(workflowId)
        self.lastInterfaceRefreshReport = self._refreshWorkflowReferences(workflowId)
        self._renderActive()
        return list(self.lastInterfaceRefreshReport)

    def _refreshSubflowPorts(self, targetWorkflowId: str) -> None:
        self._refreshWorkflowReferences(targetWorkflowId)

    def _refreshWorkflowReferences(
        self, targetWorkflowId: str | None = None
    ) -> list[str]:
        report: list[str] = []
        for workflow in self.workflowStore.workflows.values():
            for node in workflow.nodes:
                kind = node.get("kind")
                if kind == "subflow":
                    referencedId = node.get("targetWorkflowId")
                    if not isinstance(referencedId, str):
                        continue
                    if targetWorkflowId is not None and referencedId != targetWorkflowId:
                        continue
                    target = self.workflowStore.workflows.get(referencedId)
                    if target is None:
                        continue
                    node["inputPorts"] = portTypes(target.inputs)
                    node["outputPorts"] = portTypes(target.outputs)
                    continue
                if kind != "loop":
                    continue
                loop = node.get("loop")
                if not isinstance(loop, dict):
                    continue
                referencedIds = {
                    value
                    for value in (
                        loop.get("bodyWorkflowId"),
                        loop.get("conditionWorkflowId"),
                    )
                    if isinstance(value, str)
                }
                if targetWorkflowId is not None and targetWorkflowId not in referencedIds:
                    continue
                try:
                    contract = self._loopContract(loop)
                except (KeyError, ValueError):
                    continue
                node["loop"] = deepcopy(contract.normalizedConfig)
                node["inputPorts"] = dict(contract.inputPorts)
                node["outputPorts"] = dict(contract.outputPorts)
                nodeId = node.get("nodeId")
                if contract.issues and isinstance(nodeId, str):
                    report.extend(
                        f"{workflow.workflowId}.{nodeId}: {issue.message}"
                        for issue in contract.issues
                    )
            report.extend(self._pruneWorkflowEdges(workflow))
        return report

    def _loopContract(self, config: dict[str, object]) -> LoopContract:
        bodyWorkflowId = config.get("bodyWorkflowId")
        if not isinstance(bodyWorkflowId, str):
            raise ValueError("bodyWorkflowId must reference an existing workflow")
        body = self.workflowStore.get(bodyWorkflowId)
        conditionWorkflowId = config.get("conditionWorkflowId")
        condition = (
            self.workflowStore.get(conditionWorkflowId)
            if isinstance(conditionWorkflowId, str)
            else None
        )
        return deriveLoopContract(
            config,
            body.inputs,
            body.outputs,
            condition.inputs if condition is not None else {},
            condition.outputs if condition is not None else {},
        )

    def previewLoopContract(self, config: dict[str, object]) -> LoopContract:
        return self._loopContract(deepcopy(config))

    def _pruneWorkflowEdges(self, workflow) -> list[str]:
        nodesById = {
            node.get("nodeId"): node
            for node in workflow.nodes
            if isinstance(node, dict) and isinstance(node.get("nodeId"), str)
        }
        kept: list[dict[str, object]] = []
        removed: list[str] = []
        occupiedInputs: set[tuple[str, str]] = set()
        for edge in workflow.edges:
            if not isinstance(edge, dict):
                continue
            fromNode = edge.get("fromNode")
            fromPort = edge.get("fromPort")
            toNode = edge.get("toNode")
            toPort = edge.get("toPort")
            if (
                not isinstance(fromNode, str)
                or not isinstance(fromPort, str)
                or not isinstance(toNode, str)
                or not isinstance(toPort, str)
            ):
                continue
            source = nodesById.get(fromNode)
            target = nodesById.get(toNode)
            sourcePorts = source.get("outputPorts", {}) if source is not None else {}
            targetPorts = target.get("inputPorts", {}) if target is not None else {}
            sourceType = (
                sourcePorts.get(fromPort) if isinstance(sourcePorts, dict) else None
            )
            targetType = (
                targetPorts.get(toPort) if isinstance(targetPorts, dict) else None
            )
            inputKey = (toNode, toPort)
            valid = (
                isinstance(sourceType, str)
                and isinstance(targetType, str)
                and arePortTypesCompatible(sourceType, targetType)
                and inputKey not in occupiedInputs
            )
            if valid:
                kept.append(dict(edge))
                occupiedInputs.add(inputKey)
            else:
                removed.append(
                    f"{workflow.workflowId}: {fromNode}.{fromPort} -> {toNode}.{toPort}"
                )
        workflow.edges = kept
        return removed

    def _pruneNodeEdges(self, node) -> None:
        kept = []
        for edge in self.flowModel.edges:
            if edge.toNode != node.nodeId and edge.fromNode != node.nodeId:
                kept.append(edge)
                continue
            source = self.flowModel.nodes.get(edge.fromNode)
            target = self.flowModel.nodes.get(edge.toNode)
            sourceType = source.outputPorts.get(edge.fromPort) if source is not None else None
            targetType = target.inputPorts.get(edge.toPort) if target is not None else None
            if (
                isinstance(sourceType, str)
                and isinstance(targetType, str)
                and arePortTypesCompatible(sourceType, targetType)
            ):
                kept.append(edge)
        self.flowModel.edges = kept

    def referencesTo(self, workflowId: str) -> list[str]:
        self.captureActiveWorkflow()
        return self.workflowStore.referencesTo(workflowId)

    def _renderActive(self) -> None:
        self.workflowStore.ensureBoundaryNodes(self.activeWorkflowId)
        graph = self.workflowStore.graphFor()
        rawNodes = graph.get("nodes", [])
        rawEdges = graph.get("edges", [])
        nodes = rawNodes if isinstance(rawNodes, list) else []
        edges = rawEdges if isinstance(rawEdges, list) else []
        graph = {
            "nodes": [
                node
                for node in nodes
                if isinstance(node, dict) and isinstance(node.get("nodeId"), str)
            ],
            "edges": [
                edge
                for edge in edges
                if isinstance(edge, dict)
                and isinstance(edge.get("fromNode"), str)
                and isinstance(edge.get("toNode"), str)
            ],
        }
        self.flowModel.loadProjectGraph(graph)
        self.flowScene.clearGraph()
        layout = self.workflowStore.layoutFor().get("nodePositions", {})
        positions = layout if isinstance(layout, dict) else {}
        for node in self.flowModel.nodes.values():
            rawPosition = positions.get(node.nodeId, {})
            position = rawPosition if isinstance(rawPosition, dict) else {}
            defaultX, defaultY = defaultNodePosition(node.kind)
            rawX = position.get("x", defaultX)
            rawY = position.get("y", defaultY)
            x = float(rawX) if isinstance(rawX, (int, float)) else defaultX
            y = float(rawY) if isinstance(rawY, (int, float)) else defaultY
            self.flowScene.addFlowNode(
                FlowNodeViewModel(
                    nodeId=node.nodeId,
                    title=node.displayName,
                    x=x,
                    y=y,
                    inputPorts=node.inputPorts,
                    outputPorts=node.outputPorts,
                    operatorId=node.operatorId,
                    kind=node.kind,
                )
            )
        for edge in self.flowModel.edges:
            self.flowScene.renderEdge(
                FlowEdgeViewModel(edge.fromNode, edge.fromPort, edge.toNode, edge.toPort)
            )
        self.refreshSidebarNodeList()
        self.focusGraphContent()
        self.updateToolbarState()
