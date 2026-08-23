from emo_master.apps.designer.state.flow_graph_model import FlowGraphModel


def testAddNodeAndSelectNode() -> None:
  model = FlowGraphModel()
  nodeId = model.addNode(
    operatorId="vision.demo.empty",
    displayName="Empty",
    inputPorts={"image": "json"},
    outputPorts={"result": "json"}
  )

  assert nodeId in model.nodes
  model.selectNode(nodeId)
  assert model.selectedNodeId == nodeId


def testConnectNodesAndValidateGraph() -> None:
  model = FlowGraphModel()
  sourceNodeId = model.addNode(
    operatorId="vision.source",
    displayName="Source",
    inputPorts={},
    outputPorts={"result": "json"}
  )
  targetNodeId = model.addNode(
    operatorId="vision.target",
    displayName="Target",
    inputPorts={"image": "json"},
    outputPorts={}
  )

  model.connectNodesByDefaultPorts(sourceNodeId, targetNodeId)
  validation = model.validateGraph()

  assert validation["ok"] is True
  assert len(model.edges) == 1


def testParameterBindingForSelectedNode() -> None:
  model = FlowGraphModel()
  nodeId = model.addNode(
    operatorId="vision.demo.empty",
    displayName="Empty",
    inputPorts={},
    outputPorts={}
  )
  model.selectNode(nodeId)

  model.setNodeParams(nodeId, {"enabled": True, "threshold": 5})
  params = model.getSelectedNodeParams()
  assert params["enabled"] is True
  assert params["threshold"] == 5

  model.updateSelectedNodeParam("threshold", 10)
  updated = model.getSelectedNodeParams()
  assert updated["threshold"] == 10


def testConnectNodesByDefaultPortsRejectsTypeMismatch() -> None:
  model = FlowGraphModel()
  sourceNodeId = model.addNode(
    operatorId="vision.source",
    displayName="Source",
    inputPorts={},
    outputPorts={"result": "image"}
  )
  targetNodeId = model.addNode(
    operatorId="vision.target",
    displayName="Target",
    inputPorts={"image": "json"},
    outputPorts={}
  )

  try:
    model.connectNodesByDefaultPorts(sourceNodeId, targetNodeId)
  except ValueError as err:
    assert "type mismatch" in str(err)
    return

  raise AssertionError("expected ValueError")


def testConnectNodesReplacesExistingInputEdge() -> None:
  model = FlowGraphModel()
  sourceA = model.addNode(
    operatorId="vision.source.a",
    displayName="SourceA",
    inputPorts={},
    outputPorts={"result": "json"}
  )
  sourceB = model.addNode(
    operatorId="vision.source.b",
    displayName="SourceB",
    inputPorts={},
    outputPorts={"result": "json"}
  )
  target = model.addNode(
    operatorId="vision.target",
    displayName="Target",
    inputPorts={"image": "json"},
    outputPorts={}
  )

  model.connectNodes(sourceA, "result", target, "image", replaceInputPort=True)
  model.connectNodes(sourceB, "result", target, "image", replaceInputPort=True)

  assert len(model.edges) == 1
  assert model.edges[0].fromNode == sourceB


def testValidateGraphIncludesSchemaErrors() -> None:
  model = FlowGraphModel()
  nodeId = model.addNode(
    operatorId="vision.edge.canny",
    displayName="Canny",
    inputPorts={"image": "image"},
    outputPorts={"edges": "image"},
    paramSchema={
      "type": "object",
      "properties": {
        "thresholdLow": {"type": "integer", "minimum": 0, "maximum": 255}
      },
      "required": ["thresholdLow"]
    }
  )
  model.setNodeParams(nodeId, {})

  result = model.validateGraph()
  assert result["ok"] is False
  errors = result["errors"]
  assert any("thresholdLow is required" in error for error in errors)


def testRemoveNodeRemovesConnectedEdges() -> None:
  model = FlowGraphModel()
  sourceNodeId = model.addNode(
    operatorId="vision.source",
    displayName="Source",
    inputPorts={},
    outputPorts={"result": "json"}
  )
  targetNodeId = model.addNode(
    operatorId="vision.target",
    displayName="Target",
    inputPorts={"image": "json"},
    outputPorts={}
  )
  model.connectNodesByDefaultPorts(sourceNodeId, targetNodeId)

  model.removeNode(targetNodeId)

  assert targetNodeId not in model.nodes
  assert len(model.edges) == 0


def testRemoveEdgeByIdentity() -> None:
  model = FlowGraphModel()
  sourceNodeId = model.addNode(
    operatorId="vision.source",
    displayName="Source",
    inputPorts={},
    outputPorts={"result": "json"}
  )
  targetNodeId = model.addNode(
    operatorId="vision.target",
    displayName="Target",
    inputPorts={"image": "json"},
    outputPorts={}
  )
  edge = model.connectNodesByDefaultPorts(sourceNodeId, targetNodeId)

  model.removeEdge(edge.fromNode, edge.fromPort, edge.toNode, edge.toPort)

  assert len(model.edges) == 0
