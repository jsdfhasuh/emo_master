from emo_master.apps.runtime.execution.dag_executor import topologicalSort
import numpy as np

from emo_master.apps.runtime.execution.dag_executor import executeGraph
from emo_master.plugins.builtins.flow_switch.operator import FlowSwitchOperator


def testTopologicalSortReturnsDependencyOrder() -> None:
  nodes = ["n1", "n2", "n3"]
  edges = [
    {"fromNode": "n1", "toNode": "n2"},
    {"fromNode": "n2", "toNode": "n3"}
  ]
  ordered = topologicalSort(nodes, edges)
  assert ordered == ["n1", "n2", "n3"]


def testExecuteGraphRoutesOutputToDownstreamInput() -> None:
  class CannyStub:
    def executeNode(self, inputs, params, runtimeContext):
      _ = params
      _ = runtimeContext
      image = inputs.get("image")
      if isinstance(image, np.ndarray):
        return {"status": "ok", "outputs": {"edges": image.copy()}}
      return {"status": "error", "error": {"message": "missing image"}}

  graph = {
    "nodes": [
      {"nodeId": "src", "operatorId": "vision.demo.source", "params": {}},
      {
        "nodeId": "canny",
        "operatorId": "vision.edge.canny",
        "params": {"thresholdLow": 50, "thresholdHigh": 150}
      }
    ],
    "edges": [
      {"fromNode": "src", "fromPort": "image", "toNode": "canny", "toPort": "image"}
    ]
  }
  sourceImage = np.zeros((10, 10), dtype=np.uint8)
  result = executeGraph(
    graph,
    operatorRegistry={"vision.edge.canny": CannyStub},
    runtimeContext={"sourceImage": sourceImage}
  )
  assert result["ok"] is True
  assert result["nodeStatus"]["canny"] == "COMPLETED"
  assert "canny.edges" in result["artifacts"]


def testExecuteGraphRecordsSwitchBranchHit() -> None:
  class SinkStub:
    def executeNode(self, inputs, params, runtimeContext):
      _ = params
      _ = runtimeContext
      return {"status": "ok", "outputs": {"value": inputs.get("value")}}

  graph = {
    "nodes": [
      {"nodeId": "src", "operatorId": "vision.demo.source", "params": {}},
      {
        "nodeId": "switch1",
        "operatorId": "vision.flow.switch",
        "params": {
          "case0Value": "A",
          "case1Value": "B",
          "case2Value": "C",
          "case3Value": "D"
        }
      },
      {"nodeId": "sink", "operatorId": "demo.sink", "params": {}}
    ],
    "edges": [
      {"fromNode": "src", "fromPort": "image", "toNode": "switch1", "toPort": "value"},
      {"fromNode": "switch1", "fromPort": "case1", "toNode": "sink", "toPort": "value"}
    ]
  }
  result = executeGraph(
    graph,
    operatorRegistry={
      "vision.flow.switch": FlowSwitchOperator,
      "demo.sink": SinkStub,
    }
    ,
    runtimeContext={"sourceImage": "B"}
  )
  assert result["ok"] is True
  assert result["nodeStatus"]["switch1"] == "COMPLETED"
  assert result["branchHits"]["switch1"] == "case1"
  assert result["nodeStatus"]["sink"] == "COMPLETED"
