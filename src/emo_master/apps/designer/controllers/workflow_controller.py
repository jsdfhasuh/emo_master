from __future__ import annotations

from copy import deepcopy
from typing import Callable

from emo_master.apps.designer.state.flow_graph_model import FlowGraphModel
from emo_master.apps.designer.state.workflow_store import WorkflowStore, portTypes
from emo_master.apps.designer.ui.flow_scene import FlowEdgeViewModel, FlowNodeViewModel


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

    @property
    def activeWorkflowId(self) -> str:
        return self.workflowStore.activeWorkflowId

    def loadPayload(self, payload: dict[str, object]) -> None:
        self.workflowStore.loadPayload(payload)
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

    def commitSavedPayload(self, payload: dict[str, object]) -> None:
        self.workflowStore.commitSavedPayload(payload)

    def switchWorkflow(self, workflowId: str) -> None:
        self.captureActiveWorkflow()
        self.workflowStore.setActiveWorkflow(workflowId)
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
        target = self.workflowStore.get(targetWorkflowId)
        node = self.flowModel.nodes[nodeId]
        node.kind = "subflow"
        node.operatorId = ""
        node.targetWorkflowId = targetWorkflowId
        node.inputPorts = portTypes(target.inputs)
        node.outputPorts = portTypes(target.outputs)

    def configureLoopNode(self, nodeId: str, config: dict[str, object]) -> None:
        mode = config.get("mode")
        if mode not in {"repeat", "foreach", "while"}:
            raise ValueError("loop mode must be repeat, foreach, or while")
        for key in ("maxIterations", "timeoutMs"):
            value = config.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{key} must be a non-negative integer")
        node = self.flowModel.nodes[nodeId]
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
        node.kind = "loop"
        node.operatorId = ""
        normalizedConfig = deepcopy(config)
        normalizedConfig["bodyWorkflowId"] = bodyWorkflowId
        node.loop = normalizedConfig
        if mode == "foreach":
            node.inputPorts = {"items": "list"}
            node.outputPorts = {"results": "list"}
        elif mode == "while":
            node.inputPorts = {"state": "object"}
            node.outputPorts = {"state": "object"}
        else:
            target = self.workflowStore.get(bodyWorkflowId)
            node.inputPorts = portTypes(target.inputs)
            node.outputPorts = portTypes(target.outputs)

    def setWorkflowInterface(
        self,
        workflowId: str,
        inputs: dict[str, object],
        outputs: dict[str, object],
    ) -> None:
        workflow = self.workflowStore.get(workflowId)
        workflow.inputs = deepcopy(inputs)
        workflow.outputs = deepcopy(outputs)
        self._refreshSubflowPorts(workflowId)
        if workflowId == self.activeWorkflowId:
            self._renderActive()

    def _refreshSubflowPorts(self, targetWorkflowId: str) -> None:
        target = self.workflowStore.get(targetWorkflowId)
        inputPorts = portTypes(target.inputs)
        outputPorts = portTypes(target.outputs)
        for workflow in self.workflowStore.workflows.values():
            for node in workflow.nodes:
                if node.get("kind") != "subflow" or node.get("targetWorkflowId") != targetWorkflowId:
                    continue
                node["inputPorts"] = deepcopy(inputPorts)
                node["outputPorts"] = deepcopy(outputPorts)

    def referencesTo(self, workflowId: str) -> list[str]:
        self.captureActiveWorkflow()
        return self.workflowStore.referencesTo(workflowId)

    def _renderActive(self) -> None:
        graph = self.workflowStore.graphFor()
        rawNodes = graph.get("nodes", [])
        rawEdges = graph.get("edges", [])
        nodes = rawNodes if isinstance(rawNodes, list) else []
        edges = rawEdges if isinstance(rawEdges, list) else []
        visibleNodeIds = {
            node.get("nodeId")
            for node in nodes
            if isinstance(node, dict)
            and node.get("kind") not in {"workflow_input", "workflow_output"}
            and isinstance(node.get("nodeId"), str)
        }
        graph = {
            "nodes": [
                node
                for node in nodes
                if isinstance(node, dict) and node.get("nodeId") in visibleNodeIds
            ],
            "edges": [
                edge
                for edge in edges
                if isinstance(edge, dict)
                and edge.get("fromNode") in visibleNodeIds
                and edge.get("toNode") in visibleNodeIds
            ],
        }
        self.flowModel.loadProjectGraph(graph)
        self.flowScene.clearGraph()
        layout = self.workflowStore.layoutFor().get("nodePositions", {})
        positions = layout if isinstance(layout, dict) else {}
        for node in self.flowModel.nodes.values():
            rawPosition = positions.get(node.nodeId, {})
            position = rawPosition if isinstance(rawPosition, dict) else {}
            x = float(position.get("x", 20.0)) if isinstance(position.get("x", 20.0), (int, float)) else 20.0
            y = float(position.get("y", 20.0)) if isinstance(position.get("y", 20.0), (int, float)) else 20.0
            self.flowScene.addFlowNode(
                FlowNodeViewModel(
                    nodeId=node.nodeId,
                    title=node.displayName,
                    x=x,
                    y=y,
                    inputPorts=node.inputPorts,
                    outputPorts=node.outputPorts,
                    operatorId=node.operatorId,
                )
            )
        for edge in self.flowModel.edges:
            self.flowScene.renderEdge(
                FlowEdgeViewModel(edge.fromNode, edge.fromPort, edge.toNode, edge.toPort)
            )
        self.refreshSidebarNodeList()
        self.focusGraphContent()
        self.updateToolbarState()
