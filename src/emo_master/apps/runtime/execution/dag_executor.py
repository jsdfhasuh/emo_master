from collections import defaultdict, deque
from typing import Protocol, cast

from emo_master.apps.runtime.execution.models import RuntimeEdge, parseRuntimeGraph


class ExecutableOperator(Protocol):
    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, object]: ...


def topologicalSort(nodes: list[str], edges: list[dict[str, str]]) -> list[str]:
    indegree: dict[str, int] = {nodeId: 0 for nodeId in nodes}
    adjacency: dict[str, list[str]] = defaultdict(list)

    for edge in edges:
        fromNode = edge.get("fromNode")
        toNode = edge.get("toNode")
        if not isinstance(fromNode, str) or not isinstance(toNode, str):
            continue
        if fromNode not in indegree or toNode not in indegree:
            continue
        adjacency[fromNode].append(toNode)
        indegree[toNode] += 1

    queue = deque([nodeId for nodeId in nodes if indegree.get(nodeId, 0) == 0])
    ordered: list[str] = []
    while queue:
        current = queue.popleft()
        ordered.append(current)
        for nextNode in adjacency.get(current, []):
            indegree[nextNode] -= 1
            if indegree[nextNode] == 0:
                queue.append(nextNode)

    if len(ordered) != len(nodes):
        raise ValueError("cycle detected")
    return ordered


def executeGraph(
    graph: dict[str, object],
    operatorRegistry: dict[str, object],
    runtimeContext: dict[str, object] | None = None,
) -> dict[str, object]:
    context = {} if runtimeContext is None else dict(runtimeContext)
    runtimeGraph = parseRuntimeGraph(graph)
    nodeIds = [node.nodeId for node in runtimeGraph.nodes]
    edgeMaps = [_edgeToMap(edge) for edge in runtimeGraph.edges]
    orderedNodeIds = topologicalSort(nodeIds, edgeMaps)

    nodeById = {node.nodeId: node for node in runtimeGraph.nodes}
    nodeInputs: dict[str, dict[str, object]] = {nodeId: {} for nodeId in nodeIds}
    nodeStatus: dict[str, str] = {}
    artifacts: dict[str, object] = {}
    branchHits: dict[str, str] = {}

    for nodeId in orderedNodeIds:
        node = nodeById[nodeId]
        inputs = dict(nodeInputs.get(nodeId, {}))
        if _shouldSkipNode(node.operatorId, inputs):
            nodeStatus[nodeId] = "SKIPPED"
            continue

        if node.operatorId == "vision.demo.source":
            sourceImage = context.get("sourceImage")
            if sourceImage is None:
                nodeStatus[nodeId] = "FAILED"
                return {
                    "ok": False,
                    "artifacts": artifacts,
                    "nodeStatus": nodeStatus,
                    "branchHits": branchHits,
                    "error": "missing source image",
                }
            outputs = {"image": sourceImage}
            nodeStatus[nodeId] = "COMPLETED"
        else:
            operator = _buildOperator(node.operatorId, operatorRegistry)
            if operator is None or not hasattr(operator, "executeNode"):
                nodeStatus[nodeId] = "FAILED"
                return {
                    "ok": False,
                    "artifacts": artifacts,
                    "nodeStatus": nodeStatus,
                    "branchHits": branchHits,
                    "error": f"operator not found: {node.operatorId}",
                }
            executable = cast(ExecutableOperator, operator)
            executeResult = executable.executeNode(
                inputs=inputs, params=node.params, runtimeContext=context
            )
            if (
                not isinstance(executeResult, dict)
                or executeResult.get("status") != "ok"
            ):
                nodeStatus[nodeId] = "FAILED"
                return {
                    "ok": False,
                    "artifacts": artifacts,
                    "nodeStatus": nodeStatus,
                    "branchHits": branchHits,
                    "error": f"node execute failed: {nodeId}",
                }
            rawOutputs = executeResult.get("outputs", {})
            outputs = rawOutputs if isinstance(rawOutputs, dict) else {}
            nodeStatus[nodeId] = "COMPLETED"
            if node.operatorId == "vision.flow.if":
                if "true" in outputs:
                    branchHits[nodeId] = "true"
                elif "false" in outputs:
                    branchHits[nodeId] = "false"
            elif node.operatorId == "vision.flow.switch":
                for portName in ("case0", "case1", "case2", "case3", "default"):
                    if portName in outputs:
                        branchHits[nodeId] = portName
                        break

        for portName, value in outputs.items():
            if isinstance(portName, str):
                artifacts[f"{nodeId}.{portName}"] = value
        _routeNodeOutputs(
            nodeId=nodeId,
            outputs=outputs,
            edges=runtimeGraph.edges,
            nodeInputs=nodeInputs,
        )

    return {
        "ok": True,
        "artifacts": artifacts,
        "nodeStatus": nodeStatus,
        "branchHits": branchHits,
    }


def _shouldSkipNode(operatorId: str, inputs: dict[str, object]) -> bool:
    if operatorId in {"vision.demo.source", "vision.io.image_loader"}:
        return False
    return len(inputs) == 0


def _edgeToMap(edge: RuntimeEdge) -> dict[str, str]:
    return {
        "fromNode": edge.fromNode,
        "fromPort": edge.fromPort,
        "toNode": edge.toNode,
        "toPort": edge.toPort,
    }


def _buildOperator(
    operatorId: str, operatorRegistry: dict[str, object]
) -> object | None:
    operatorFactory = operatorRegistry.get(operatorId)
    if operatorFactory is None:
        return None
    if isinstance(operatorFactory, type):
        return operatorFactory()
    return operatorFactory


def _routeNodeOutputs(
    nodeId: str,
    outputs: dict[str, object],
    edges: list[RuntimeEdge],
    nodeInputs: dict[str, dict[str, object]],
) -> None:
    for edge in edges:
        if edge.fromNode != nodeId:
            continue
        if edge.fromPort not in outputs:
            continue
        targetInputs = nodeInputs.get(edge.toNode)
        if targetInputs is None:
            targetInputs = {}
            nodeInputs[edge.toNode] = targetInputs
        targetInputs[edge.toPort] = outputs[edge.fromPort]
