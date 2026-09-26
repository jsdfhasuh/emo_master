from __future__ import annotations

from pathlib import Path

from emo_master.apps.designer.ui.main_window import MainWindow


class _RuntimeClientStub:
    def listOperators(self):
        return []

    def loadProject(self, projectPath: str):
        _ = projectPath
        return type("Reply", (), {"ok": True, "message": "ok"})()


def _switchToMain(window: MainWindow) -> None:
    window.workflowController.switchWorkflow("main")
    window.activeWorkflowId = "main"
    window._refreshWorkflowTabs()


def testControlFlowLibraryContainsSystemNodes() -> None:
    window = MainWindow(_RuntimeClientStub())
    window.activeOperatorCategory = "控制流"

    payloads = window._getOperatorsByCategory()

    assert {payload.get("systemNodeKind") for payload in payloads} == {
        "subflow",
        "loop:repeat",
        "loop:foreach",
        "loop:while",
    }
    assert all(payload.get("category") == "控制流" for payload in payloads)
    assert {payload.get("operatorId") for payload in payloads} == {
        "system.subflow",
        "system.repeat",
        "system.foreach",
        "system.while",
    }


def testSystemNodeLibraryPayloadCreatesAtDropPositionAndFocusesCanvas() -> None:
    window = MainWindow(_RuntimeClientStub())
    bodyWorkflowId = window.createWorkflow("Body")
    _switchToMain(window)
    focusCalls: list[bool] = []
    window.focusGraphContent = lambda: focusCalls.append(True)  # type: ignore[method-assign]
    repeatPayload = next(
        payload
        for payload in window._getSystemNodeLibraryPayloads()
        if payload.get("systemNodeKind") == "loop:repeat"
    )

    window.addNodeFromOperatorPayload(repeatPayload, sceneX=640.0, sceneY=360.0)

    repeatNodes = [
        node
        for node in window.flowModel.nodes.values()
        if node.kind == "loop" and node.loop.get("mode") == "repeat"
    ]
    assert len(repeatNodes) == 1
    repeatNode = repeatNodes[0]
    assert repeatNode.loop["bodyWorkflowId"] == bodyWorkflowId
    assert window.flowScene.getNodePositions()[repeatNode.nodeId] == (640.0, 360.0)
    assert focusCalls == []
    assert window.flowScene.getSelectedNodeId() == repeatNode.nodeId


def testNewForEachNodeDerivesPortsFromBodyWorkflow() -> None:
    window = MainWindow(_RuntimeClientStub())
    bodyWorkflowId = window.createWorkflow("Body")
    window.editWorkflowInterface(
        {"image": "image", "threshold": "number"},
        {"edges": "image", "score": "number"},
    )
    _switchToMain(window)

    nodeId = window.addForEachNode()

    assert nodeId is not None
    node = window.flowModel.nodes[nodeId]
    assert node.loop["contractVersion"] == 2
    assert node.loop["bodyWorkflowId"] == bodyWorkflowId
    assert node.loop["itemInputPort"] == "image"
    assert node.inputPorts == {
        "items": "list<image>",
        "threshold": "number",
    }
    assert node.outputPorts == {
        "edges": "list<image>",
        "score": "list<number>",
    }


def testNewWhileNodeSelectsCompatibleConditionAndBody() -> None:
    window = MainWindow(_RuntimeClientStub())
    bodyWorkflowId = window.createWorkflow("Body")
    window.editWorkflowInterface({"count": "integer"}, {"count": "integer"})
    conditionWorkflowId = window.createWorkflow("Condition")
    window.editWorkflowInterface(
        {"count": "integer"}, {"continue": "boolean"}
    )
    _switchToMain(window)

    nodeId = window.addWhileNode()

    assert nodeId is not None
    node = window.flowModel.nodes[nodeId]
    assert node.loop["contractVersion"] == 2
    assert node.loop["bodyWorkflowId"] == bodyWorkflowId
    assert node.loop["conditionWorkflowId"] == conditionWorkflowId
    assert node.inputPorts == {"count": "integer"}
    assert node.outputPorts == {"count": "integer"}


def testApplyingLegacyForEachConfigurationUpgradesItsContract() -> None:
    window = MainWindow(_RuntimeClientStub())
    bodyWorkflowId = window.createWorkflow("Body")
    window.editWorkflowInterface({"image": "image"}, {"edges": "image"})
    _switchToMain(window)
    nodeId = window.addForEachNode(
        {
            "mode": "foreach",
            "bodyWorkflowId": bodyWorkflowId,
            "maxIterations": 10,
            "timeoutMs": 0,
        }
    )
    assert nodeId is not None
    assert window.flowModel.nodes[nodeId].loop["contractVersion"] == 1

    window.applyNodeParams(
        nodeId,
        {
            "mode": "foreach",
            "bodyWorkflowId": bodyWorkflowId,
            "itemInputPort": "image",
            "indexInputPort": "",
            "maxIterations": 10,
            "timeoutMs": 0,
        },
    )

    node = window.flowModel.nodes[nodeId]
    assert node.loop["contractVersion"] == 2
    assert node.inputPorts == {"items": "list<image>"}
    assert node.outputPorts == {"edges": "list<image>"}


def testSystemNodeCatalogAndInteractiveConfiguration() -> None:
    window = MainWindow(_RuntimeClientStub())
    catalog = window.getSystemNodeCatalog()
    assert {item["kind"] for item in catalog} == {
        "workflow_input",
        "workflow_output",
        "subflow",
        "loop:repeat",
        "loop:foreach",
        "loop:while",
    }

    bodyWorkflowId = window.createWorkflow("Body")
    conditionWorkflowId = window.createWorkflow("Condition")
    _switchToMain(window)

    subflowNodeId = window.addSubflowNode(bodyWorkflowId)
    repeatNodeId = window.addRepeatNode(
        {
            "mode": "repeat",
            "bodyWorkflowId": bodyWorkflowId,
            "repeatCount": 1,
            "maxIterations": 3,
            "timeoutMs": 1000,
        }
    )
    foreachNodeId = window.addForEachNode(
        {
            "mode": "foreach",
            "bodyWorkflowId": bodyWorkflowId,
            "maxIterations": 3,
            "timeoutMs": 1000,
        }
    )
    whileNodeId = window.addWhileNode(
        {
            "mode": "while",
            "conditionWorkflowId": conditionWorkflowId,
            "bodyWorkflowId": bodyWorkflowId,
            "maxIterations": 3,
            "timeoutMs": 1000,
        }
    )
    assert all(isinstance(nodeId, str) for nodeId in (subflowNodeId, repeatNodeId, foreachNodeId, whileNodeId))

    assert subflowNodeId is not None
    window.openNodeParamDialog(subflowNodeId)
    window.applyNodeParams(subflowNodeId, {"targetWorkflowId": bodyWorkflowId})
    assert window.flowModel.nodes[subflowNodeId].targetWorkflowId == bodyWorkflowId

    assert repeatNodeId is not None
    window.openNodeParamDialog(repeatNodeId)
    window.applyNodeParams(
        repeatNodeId,
        {
            "mode": "repeat",
            "bodyWorkflowId": bodyWorkflowId,
            "repeatCount": 2,
            "maxIterations": 3,
            "timeoutMs": 1000,
        },
    )
    assert window.flowModel.nodes[repeatNodeId].loop["repeatCount"] == 2


def testSystemNodeConfigurationsSurviveSaveReload(tmp_path: Path) -> None:
    window = MainWindow(_RuntimeClientStub())
    bodyWorkflowId = window.createWorkflow("Body")
    conditionWorkflowId = window.createWorkflow("Condition")
    _switchToMain(window)
    window.addSubflowNode(bodyWorkflowId)
    window.addRepeatNode(
        {
            "mode": "repeat",
            "bodyWorkflowId": bodyWorkflowId,
            "repeatCount": 2,
            "maxIterations": 3,
            "timeoutMs": 1000,
        }
    )
    window.addForEachNode(
        {
            "mode": "foreach",
            "bodyWorkflowId": bodyWorkflowId,
            "maxIterations": 4,
            "timeoutMs": 1000,
        }
    )
    window.addWhileNode(
        {
            "mode": "while",
            "conditionWorkflowId": conditionWorkflowId,
            "bodyWorkflowId": bodyWorkflowId,
            "maxIterations": 5,
            "timeoutMs": 1000,
        }
    )

    projectDir = tmp_path / "system-node-project"
    assert window.saveProjectToDirectory(str(projectDir)) is True

    reloaded = MainWindow(_RuntimeClientStub())
    assert reloaded.loadProjectDirectory(str(projectDir)) is True
    nodes = list(reloaded.workflowStore.get("main").nodes)
    kinds = {node.get("kind") for node in nodes}
    assert {"subflow", "loop"}.issubset(kinds)
    loops = [node for node in nodes if node.get("kind") == "loop"]
    assert {node["loop"]["mode"] for node in loops} == {"repeat", "foreach", "while"}
    repeat = next(node for node in loops if node["loop"]["mode"] == "repeat")
    assert repeat["loop"]["repeatCount"] == 2
