from collections import defaultdict, deque
from typing import Iterable


def validateFlowGraph(flowData: dict[str, object]) -> dict[str, object]:
  errors: list[str] = []

  nodes = flowData.get("nodes", [])
  edges = flowData.get("edges", [])
  if not isinstance(nodes, list) or not isinstance(edges, list):
    return {"ok": False, "errors": ["nodes and edges must be lists"]}

  nodeById: dict[str, dict[str, object]] = {}
  for node in nodes:
    if not isinstance(node, dict):
      errors.append("node must be object")
      continue
    nodeId = node.get("nodeId")
    if not isinstance(nodeId, str):
      errors.append("nodeId must be string")
      continue
    nodeById[nodeId] = node

  for edge in edges:
    if not isinstance(edge, dict):
      errors.append("edge must be object")
      continue
    fromNode = edge.get("fromNode")
    toNode = edge.get("toNode")
    fromPort = edge.get("fromPort")
    toPort = edge.get("toPort")

    if not isinstance(fromNode, str) or not isinstance(toNode, str):
      errors.append("edge node ids must be string")
      continue
    if fromNode not in nodeById or toNode not in nodeById:
      errors.append("edge references unknown node")
      continue

    sourceNode = nodeById[fromNode]
    targetNode = nodeById[toNode]
    sourceOutputPorts = sourceNode.get("outputPorts", {})
    targetInputPorts = targetNode.get("inputPorts", {})
    if not isinstance(sourceOutputPorts, dict) or not isinstance(targetInputPorts, dict):
      errors.append("node port definitions must be object")
      continue

    sourceType = sourceOutputPorts.get(fromPort)
    targetType = targetInputPorts.get(toPort)
    if sourceType is None or targetType is None:
      errors.append("edge references missing port")
      continue
    if sourceType != targetType:
      errors.append(f"type mismatch: {fromNode}.{fromPort} -> {toNode}.{toPort}")

  if _containsCycle(nodeById.keys(), edges):
    errors.append("cycle detected")

  return {"ok": len(errors) == 0, "errors": errors}


def _containsCycle(nodeIds: Iterable[str], edges: list[object]) -> bool:

  indegree: dict[str, int] = defaultdict(int)
  adjacency: dict[str, list[str]] = defaultdict(list)
  nodeSet = set(nodeIds)

  for nodeId in nodeSet:
    indegree[str(nodeId)] = 0

  for edge in edges:
    if not isinstance(edge, dict):
      continue
    fromNode = edge.get("fromNode")
    toNode = edge.get("toNode")
    if not isinstance(fromNode, str) or not isinstance(toNode, str):
      continue
    if fromNode in nodeSet and toNode in nodeSet:
      adjacency[fromNode].append(toNode)
      indegree[toNode] += 1

  queue = deque([nodeId for nodeId, value in indegree.items() if value == 0])
  visited = 0
  while queue:
    current = queue.popleft()
    visited += 1
    for nextNode in adjacency[current]:
      indegree[nextNode] -= 1
      if indegree[nextNode] == 0:
        queue.append(nextNode)

  return visited != len(nodeSet)
