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
