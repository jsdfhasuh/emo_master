from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeNode:
  nodeId: str
  operatorId: str
  params: dict[str, object]


@dataclass(frozen=True)
class RuntimeEdge:
  fromNode: str
  fromPort: str
  toNode: str
  toPort: str


@dataclass(frozen=True)
class RuntimeGraph:
  nodes: list[RuntimeNode]
  edges: list[RuntimeEdge]


def parseRuntimeGraph(payload: dict[str, object]) -> RuntimeGraph:
  rawNodes = payload.get("nodes", [])
  rawEdges = payload.get("edges", [])

  nodes: list[RuntimeNode] = []
  if isinstance(rawNodes, list):
    for item in rawNodes:
      if not isinstance(item, dict):
        continue
      nodeId = item.get("nodeId")
      operatorId = item.get("operatorId")
      params = item.get("params", {})
      if isinstance(nodeId, str) and isinstance(operatorId, str) and isinstance(params, dict):
        nodes.append(RuntimeNode(nodeId=nodeId, operatorId=operatorId, params=dict(params)))

  edges: list[RuntimeEdge] = []
  if isinstance(rawEdges, list):
    for edge in rawEdges:
      if not isinstance(edge, dict):
        continue
      fromNode = edge.get("fromNode")
      fromPort = edge.get("fromPort")
      toNode = edge.get("toNode")
      toPort = edge.get("toPort")
      if all(isinstance(value, str) for value in [fromNode, fromPort, toNode, toPort]):
        edges.append(
          RuntimeEdge(
            fromNode=str(fromNode),
            fromPort=str(fromPort),
            toNode=str(toNode),
            toPort=str(toPort)
          )
        )

  return RuntimeGraph(nodes=nodes, edges=edges)
