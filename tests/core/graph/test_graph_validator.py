from emo_master.core.graph.validator import validateFlowGraph


def testGraphValidatorPassesForSimpleChain() -> None:
  flowData = {
    "nodes": [
      {
        "nodeId": "n1",
        "operatorId": "vision.demo.empty",
        "outputPorts": {"result": "json"},
        "inputPorts": {}
      },
      {
        "nodeId": "n2",
        "operatorId": "vision.demo.empty",
        "inputPorts": {"image": "json"},
        "outputPorts": {}
      }
    ],
    "edges": [
      {"fromNode": "n1", "fromPort": "result", "toNode": "n2", "toPort": "image"}
    ]
  }

  result = validateFlowGraph(flowData)
  assert result["ok"] is True
  assert result["errors"] == []


def testGraphValidatorRejectsCycle() -> None:
  flowData = {
    "nodes": [
      {
        "nodeId": "a",
        "operatorId": "vision.demo.empty",
        "outputPorts": {"result": "json"},
        "inputPorts": {"image": "json"}
      },
      {
        "nodeId": "b",
        "operatorId": "vision.demo.empty",
        "outputPorts": {"result": "json"},
        "inputPorts": {"image": "json"}
      }
    ],
    "edges": [
      {"fromNode": "a", "fromPort": "result", "toNode": "b", "toPort": "image"},
      {"fromNode": "b", "fromPort": "result", "toNode": "a", "toPort": "image"}
    ]
  }

  result = validateFlowGraph(flowData)
  assert result["ok"] is False
  assert any("cycle" in err.lower() for err in result["errors"])


def testGraphValidatorRejectsPortTypeMismatch() -> None:
  flowData = {
    "nodes": [
      {
        "nodeId": "n1",
        "operatorId": "vision.demo.empty",
        "outputPorts": {"result": "image"},
        "inputPorts": {}
      },
      {
        "nodeId": "n2",
        "operatorId": "vision.demo.empty",
        "outputPorts": {},
        "inputPorts": {"image": "json"}
      }
    ],
    "edges": [
      {"fromNode": "n1", "fromPort": "result", "toNode": "n2", "toPort": "image"}
    ]
  }

  result = validateFlowGraph(flowData)
  assert result["ok"] is False
  assert any("type mismatch" in err.lower() for err in result["errors"])
