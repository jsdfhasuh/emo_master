from emo_master.apps.runtime.execution.models import parseRuntimeGraph


def testParseRuntimeGraphBuildsTypedGraph() -> None:
  payload = {
    "nodes": [
      {
        "nodeId": "n1",
        "operatorId": "vision.edge.canny",
        "params": {"thresholdLow": 50, "thresholdHigh": 150}
      }
    ],
    "edges": []
  }
  graph = parseRuntimeGraph(payload)
  assert graph.nodes[0].nodeId == "n1"
