from __future__ import annotations

from pathlib import Path

from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel
from emo_master.apps.designer.ui.main_window import MainWindow


class _RuntimeClientStub:
    def listOperators(self):
        return []

    def loadProject(self, projectPath: str):
        _ = projectPath
        return type("Reply", (), {"ok": True, "message": "ok"})()


def _boundaryNode(window: MainWindow, kind: str):
    return next(node for node in window.flowModel.nodes.values() if node.kind == kind)


def _addInternalNode(window: MainWindow) -> str:
    nodeId = window.flowModel.addNode(
        "vision.demo.echo",
        "Echo",
        {"value": "string"},
        {"result": "string"},
    )
    node = window.flowModel.nodes[nodeId]
    window.flowScene.addFlowNode(
        FlowNodeViewModel(
            nodeId,
            node.displayName,
            260.0,
            120.0,
            node.inputPorts,
            node.outputPorts,
            node.operatorId,
            node.kind,
        )
    )
    return nodeId


def _connect(window: MainWindow, fromNode: str, fromPort: str, toNode: str, toPort: str) -> None:
    edge = window.connectPorts(fromNode, fromPort, toNode, toPort)
    assert edge is not None
    window.flowScene.renderEdge(edge)


def testWorkflowBoundaryNodesAreVisibleAndFollowInterface() -> None:
    window = MainWindow(_RuntimeClientStub())
    window.editWorkflowInterface({"value": "string"}, {"result": "string"})

    inputNode = _boundaryNode(window, "workflow_input")
    outputNode = _boundaryNode(window, "workflow_output")
    assert window.flowScene.hasNode(inputNode.nodeId) is True
    assert window.flowScene.hasNode(outputNode.nodeId) is True
    assert inputNode.outputPorts == {"value": "string"}
    assert outputNode.inputPorts == {"result": "string"}


def testWorkflowBoundaryNodesCannotBeDeleted() -> None:
    window = MainWindow(_RuntimeClientStub())
    inputNode = _boundaryNode(window, "workflow_input")
    window.flowModel.selectNode(inputNode.nodeId)
    window.flowScene.setNodeSelected(inputNode.nodeId)

    window.deleteSelectedElements()

    assert inputNode.nodeId in window.flowModel.nodes
    assert window.flowScene.hasNode(inputNode.nodeId) is True


def testBoundaryEdgesSurviveSwitchSaveAndReload(tmp_path: Path) -> None:
    window = MainWindow(_RuntimeClientStub())
    window.editWorkflowInterface({"value": "string"}, {"result": "string"})
    inputNode = _boundaryNode(window, "workflow_input")
    outputNode = _boundaryNode(window, "workflow_output")
    internalNodeId = _addInternalNode(window)
    _connect(window, inputNode.nodeId, "value", internalNodeId, "value")
    _connect(window, internalNodeId, "result", outputNode.nodeId, "result")

    inputItem = window.flowScene._nodeItems[inputNode.nodeId]
    inputItem.setPos(40.0, 70.0)
    projectDir = tmp_path / "boundary-project"
    assert window.saveProjectToDirectory(str(projectDir)) is True

    reloaded = MainWindow(_RuntimeClientStub())
    assert reloaded.loadProjectDirectory(str(projectDir)) is True
    reloadedInput = _boundaryNode(reloaded, "workflow_input")
    reloadedOutput = _boundaryNode(reloaded, "workflow_output")
    assert len(reloaded.flowModel.edges) == 2
    assert reloaded.flowScene.hasNode(reloadedInput.nodeId) is True
    assert reloaded.flowScene.hasNode(reloadedOutput.nodeId) is True
    position = reloaded.flowScene.getNodePositions()[reloadedInput.nodeId]
    assert position == (40.0, 70.0)


def testInterfaceChangePrunesInvalidBoundaryEdgesAndRefreshesSubflowPorts() -> None:
    window = MainWindow(_RuntimeClientStub())
    window.editWorkflowInterface({"value": "string"}, {"result": "string"})
    inputNode = _boundaryNode(window, "workflow_input")
    internalNodeId = _addInternalNode(window)
    _connect(window, inputNode.nodeId, "value", internalNodeId, "value")

    window.editWorkflowInterface({"renamed": "string"}, {"result": "string"})
    assert window.flowModel.edges == []

    bodyWorkflowId = window.createWorkflow("Body")
    window.editWorkflowInterface({"bodyValue": "string"}, {"bodyResult": "string"})
    window.workflowController.switchWorkflow("main")
    window.activeWorkflowId = "main"
    window._refreshWorkflowTabs()
    subflowNodeId = window.addSubflowNode(bodyWorkflowId)
    assert subflowNodeId is not None
    assert window.flowModel.nodes[subflowNodeId].inputPorts == {"bodyValue": "string"}
    assert window.flowModel.nodes[subflowNodeId].outputPorts == {"bodyResult": "string"}

    window.workflowController.switchWorkflow(bodyWorkflowId)
    window.activeWorkflowId = bodyWorkflowId
    window.workflowController.setWorkflowInterface(
        bodyWorkflowId, {"nextValue": "string"}, {"nextResult": "string"}
    )
    window.workflowController.switchWorkflow("main")
    window.activeWorkflowId = "main"
    assert window.flowModel.nodes[subflowNodeId].inputPorts == {"nextValue": "string"}
    assert window.flowModel.nodes[subflowNodeId].outputPorts == {"nextResult": "string"}
