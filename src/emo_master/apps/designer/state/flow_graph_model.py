from dataclasses import dataclass, field
from uuid import uuid4

from emo_master.apps.designer.state.schema_utils import collectSchemaErrors
from emo_master.core.contracts.port_compatibility import arePortTypesCompatible
from emo_master.core.graph.validator import validateFlowGraph


@dataclass
class FlowNode:
    nodeId: str
    operatorId: str
    displayName: str
    inputPorts: dict[str, str]
    outputPorts: dict[str, str]
    paramSchema: dict[str, object] = field(default_factory=dict)
    params: dict[str, object] = field(default_factory=dict)
    kind: str = "operator"
    targetWorkflowId: str | None = None
    loop: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class FlowEdge:
    fromNode: str
    fromPort: str
    toNode: str
    toPort: str


class FlowGraphModel:
    def __init__(self) -> None:
        self.nodes: dict[str, FlowNode] = {}
        self.edges: list[FlowEdge] = []
        self.selectedNodeId: str | None = None

    def addNode(
        self,
        operatorId: str,
        displayName: str,
        inputPorts: dict[str, str],
        outputPorts: dict[str, str],
        paramSchema: dict[str, object] | None = None,
        kind: str = "operator",
        targetWorkflowId: str | None = None,
        loop: dict[str, object] | None = None,
    ) -> str:
        nodeId = f"node-{uuid4().hex[:8]}"
        node = FlowNode(
            nodeId=nodeId,
            operatorId=operatorId,
            displayName=displayName,
            inputPorts=dict(inputPorts),
            outputPorts=dict(outputPorts),
            paramSchema={} if paramSchema is None else dict(paramSchema),
            kind=kind,
            targetWorkflowId=targetWorkflowId,
            loop={} if loop is None else dict(loop),
        )
        if kind in {"workflow_input", "workflow_output"}:
            self.nodes[nodeId] = node
            return nodeId

        # Keep user-created nodes first so existing selection and navigation
        # callers do not accidentally select an invisible system boundary.
        regularNodes = {
            nodeId: value
            for nodeId, value in self.nodes.items()
            if value.kind not in {"workflow_input", "workflow_output"}
        }
        boundaryNodes = {
            nodeId: value
            for nodeId, value in self.nodes.items()
            if value.kind in {"workflow_input", "workflow_output"}
        }
        regularNodes[nodeId] = node
        self.nodes.clear()
        self.nodes.update(regularNodes)
        self.nodes.update(boundaryNodes)
        return nodeId

    def selectNode(self, nodeId: str | None) -> None:
        if nodeId is not None and nodeId not in self.nodes:
            raise ValueError(f"unknown node: {nodeId}")
        self.selectedNodeId = nodeId

    def connectNodes(
        self,
        fromNode: str,
        fromPort: str,
        toNode: str,
        toPort: str,
        replaceInputPort: bool = True,
    ) -> FlowEdge:
        if fromNode not in self.nodes or toNode not in self.nodes:
            raise ValueError("connectNodes references unknown node")

        sourceNode = self.nodes[fromNode]
        targetNode = self.nodes[toNode]
        if fromPort not in sourceNode.outputPorts:
            raise ValueError(f"unknown source port: {fromNode}.{fromPort}")
        if toPort not in targetNode.inputPorts:
            raise ValueError(f"unknown target port: {toNode}.{toPort}")
        sourceType = sourceNode.outputPorts[fromPort]
        targetType = targetNode.inputPorts[toPort]
        if not arePortTypesCompatible(sourceType, targetType):
            raise ValueError(
                f"port type mismatch: {fromNode}.{fromPort}({sourceType}) -> {toNode}.{toPort}({targetType})"
            )

        if replaceInputPort:
            self._removeInputEdge(toNode=toNode, toPort=toPort)

        edge = FlowEdge(
            fromNode=fromNode, fromPort=fromPort, toNode=toNode, toPort=toPort
        )
        if edge not in self.edges:
            self.edges.append(edge)
        return edge

    def connectNodesByDefaultPorts(self, fromNode: str, toNode: str) -> FlowEdge:
        sourceNode = self._getNodeOrRaise(fromNode)
        targetNode = self._getNodeOrRaise(toNode)
        fromPort = self._getFirstPortName(sourceNode.outputPorts, "source output")
        toPort = self._getFirstPortName(targetNode.inputPorts, "target input")
        return self.connectNodes(fromNode, fromPort, toNode, toPort)

    def setNodeParams(self, nodeId: str, params: dict[str, object]) -> None:
        if nodeId not in self.nodes:
            raise ValueError(f"unknown node: {nodeId}")
        self.nodes[nodeId].params = dict(params)

    def removeNode(self, nodeId: str) -> None:
        if nodeId not in self.nodes:
            return
        if self.isBoundaryNode(nodeId):
            return
        del self.nodes[nodeId]
        self.edges = [
            edge
            for edge in self.edges
            if edge.fromNode != nodeId and edge.toNode != nodeId
        ]
        if self.selectedNodeId == nodeId:
            self.selectedNodeId = None

    def isBoundaryNode(self, nodeId: str) -> bool:
        node = self.nodes.get(nodeId)
        return node is not None and node.kind in {"workflow_input", "workflow_output"}

    def removeEdge(
        self, fromNode: str, fromPort: str, toNode: str, toPort: str
    ) -> None:
        self.edges = [
            edge
            for edge in self.edges
            if not (
                edge.fromNode == fromNode
                and edge.fromPort == fromPort
                and edge.toNode == toNode
                and edge.toPort == toPort
            )
        ]

    def getNodeParams(self, nodeId: str) -> dict[str, object]:
        if nodeId not in self.nodes:
            raise ValueError(f"unknown node: {nodeId}")
        return dict(self.nodes[nodeId].params)

    def getSelectedNodeParams(self) -> dict[str, object]:
        if self.selectedNodeId is None:
            return {}
        return self.getNodeParams(self.selectedNodeId)

    def getSelectedNodeSchema(self) -> dict[str, object]:
        if self.selectedNodeId is None:
            return {}
        return dict(self.nodes[self.selectedNodeId].paramSchema)

    def updateSelectedNodeParam(self, paramName: str, value: object) -> None:
        if self.selectedNodeId is None:
            raise ValueError("no node selected")
        current = self.nodes[self.selectedNodeId].params
        updated = dict(current)
        updated[paramName] = value
        self.nodes[self.selectedNodeId].params = updated

    def validateGraph(self) -> dict[str, object]:
        flowData = {
            "nodes": [
                {
                    "nodeId": node.nodeId,
                    "operatorId": node.operatorId,
                    "inputPorts": node.inputPorts,
                    "outputPorts": node.outputPorts,
                }
                for node in self.nodes.values()
            ],
            "edges": [
                {
                    "fromNode": edge.fromNode,
                    "fromPort": edge.fromPort,
                    "toNode": edge.toNode,
                    "toPort": edge.toPort,
                }
                for edge in self.edges
            ],
        }
        graphValidation = validateFlowGraph(flowData)
        schemaErrors = self._validateNodeParamsBySchema()
        combinedErrorsRaw = graphValidation.get("errors", [])
        combinedErrors = (
            combinedErrorsRaw if isinstance(combinedErrorsRaw, list) else []
        )
        allErrors = [*combinedErrors, *schemaErrors]
        return {"ok": len(allErrors) == 0, "errors": allErrors}

    def toProjectGraph(self) -> dict[str, object]:
        return {
            "nodes": [
                {
                    "nodeId": node.nodeId,
                    "operatorId": node.operatorId,
                    "displayName": node.displayName,
                    "inputPorts": dict(node.inputPorts),
                    "outputPorts": dict(node.outputPorts),
                    "paramSchema": dict(node.paramSchema),
                    "params": dict(node.params),
                    "kind": node.kind,
                    "targetWorkflowId": node.targetWorkflowId,
                    "loop": dict(node.loop),
                }
                for node in self.nodes.values()
            ],
            "edges": [
                {
                    "fromNode": edge.fromNode,
                    "fromPort": edge.fromPort,
                    "toNode": edge.toNode,
                    "toPort": edge.toPort,
                }
                for edge in self.edges
            ],
        }

    def loadProjectGraph(self, payload: dict[str, object]) -> None:
        rawNodes = payload.get("nodes", [])
        rawEdges = payload.get("edges", [])
        self.nodes = {}
        self.edges = []
        self.selectedNodeId = None

        if isinstance(rawNodes, list):
            for item in rawNodes:
                if not isinstance(item, dict):
                    continue
                nodeId = item.get("nodeId")
                operatorId = item.get("operatorId")
                displayName = item.get("displayName")
                inputPorts = item.get("inputPorts", {})
                outputPorts = item.get("outputPorts", {})
                paramSchema = item.get("paramSchema", {})
                params = item.get("params", {})
                kind = item.get("kind", "operator")
                targetWorkflowId = item.get("targetWorkflowId")
                loop = item.get("loop", {})
                if not isinstance(nodeId, str):
                    continue
                if not isinstance(operatorId, str):
                    operatorId = ""
                if not isinstance(displayName, str):
                    displayName = operatorId or str(kind) if isinstance(kind, str) else nodeId
                if not isinstance(inputPorts, dict) or not isinstance(
                    outputPorts, dict
                ):
                    continue
                if not isinstance(paramSchema, dict) or not isinstance(params, dict):
                    continue
                if not isinstance(kind, str) or not isinstance(loop, dict):
                    continue
                if targetWorkflowId is not None and not isinstance(targetWorkflowId, str):
                    continue
                self.nodes[nodeId] = FlowNode(
                    nodeId=nodeId,
                    operatorId=operatorId,
                    displayName=displayName,
                    inputPorts={
                        str(key): str(value)
                        for key, value in inputPorts.items()
                        if isinstance(key, str) and isinstance(value, str)
                    },
                    outputPorts={
                        str(key): str(value)
                        for key, value in outputPorts.items()
                        if isinstance(key, str) and isinstance(value, str)
                    },
                    paramSchema=dict(paramSchema),
                    params=dict(params),
                    kind=kind,
                    targetWorkflowId=targetWorkflowId,
                    loop=dict(loop),
                )

        regularNodes = {
            nodeId: node
            for nodeId, node in self.nodes.items()
            if node.kind not in {"workflow_input", "workflow_output"}
        }
        boundaryNodes = {
            nodeId: node
            for nodeId, node in self.nodes.items()
            if node.kind in {"workflow_input", "workflow_output"}
        }
        self.nodes.clear()
        self.nodes.update(regularNodes)
        self.nodes.update(boundaryNodes)

        if isinstance(rawEdges, list):
            for edgeItem in rawEdges:
                if not isinstance(edgeItem, dict):
                    continue
                fromNode = edgeItem.get("fromNode")
                fromPort = edgeItem.get("fromPort")
                toNode = edgeItem.get("toNode")
                toPort = edgeItem.get("toPort")
                if not all(
                    isinstance(value, str)
                    for value in [fromNode, fromPort, toNode, toPort]
                ):
                    continue
                if fromNode not in self.nodes or toNode not in self.nodes:
                    continue
                self.edges.append(
                    FlowEdge(
                        fromNode=str(fromNode),
                        fromPort=str(fromPort),
                        toNode=str(toNode),
                        toPort=str(toPort),
                    )
                )

    def _validateNodeParamsBySchema(self) -> list[str]:
        errors: list[str] = []
        for node in self.nodes.values():
            schema = node.paramSchema
            if not isinstance(schema, dict) or len(schema) == 0:
                continue
            nodeErrors = collectSchemaErrors(
                schema, node.params, f"{node.nodeId} params"
            )
            errors.extend(nodeErrors)
        return errors

    def _removeInputEdge(self, toNode: str, toPort: str) -> None:
        filteredEdges: list[FlowEdge] = []
        for edge in self.edges:
            if edge.toNode == toNode and edge.toPort == toPort:
                continue
            filteredEdges.append(edge)
        self.edges = filteredEdges

    def _getNodeOrRaise(self, nodeId: str) -> FlowNode:
        if nodeId not in self.nodes:
            raise ValueError(f"unknown node: {nodeId}")
        return self.nodes[nodeId]

    def _getFirstPortName(self, ports: dict[str, str], label: str) -> str:
        portNames = list(ports.keys())
        if len(portNames) == 0:
            raise ValueError(f"{label} port is missing")
        return portNames[0]
