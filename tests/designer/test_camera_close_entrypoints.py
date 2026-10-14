import pytest

from emo_master.apps.designer.ui.main_window import MainWindow
from tests.designer.test_main_window_workflow_tabs import _RuntimeClientStub


@pytest.mark.parametrize("action", ["project", "node", "workflow", "exit", "undo"])
def testCancelledEditorClosePreventsDestructiveAction(action, tmp_path, monkeypatch, designerApplication):
    window = MainWindow(_RuntimeClientStub())
    window.addNodeFromOperatorPayload({"operatorId": "vision.value.number", "displayName": "保留节点",
                                      "inputPorts": {}, "outputPorts": {"value": "number"}}, 50, 50)
    nodeId = next(node.nodeId for node in window.flowModel.nodes.values() if node.operatorId == "vision.value.number")
    manager = window.operatorEditorManager
    originalNodes = set(window.flowModel.nodes)
    originalWorkflow = window.activeWorkflowId
    with monkeypatch.context() as patch:
        patch.setattr(manager, "closeAll", lambda: False)
        patch.setattr(manager, "closeNode", lambda *_: False)
        patch.setattr(manager, "closeWorkflow", lambda *_: False)
        patch.setattr(manager, "closeCameraConfigurations", lambda: False)
        if action == "project":
            assert not window.loadProjectDirectory(str(tmp_path / "not-loaded"))
        elif action == "node":
            window.flowScene._nodeItems[nodeId].setSelected(True)
            window.deleteSelectedElements()
        elif action == "workflow":
            window.deleteWorkflow(originalWorkflow)
        elif action == "undo":
            window.pageCoordinator.history()
        else:
            assert not window.close()
        assert set(window.flowModel.nodes) == originalNodes
        assert window.activeWorkflowId == originalWorkflow
    if action == "undo":
        window.pageCoordinator.history()
        assert nodeId not in window.flowModel.nodes
    window.close()


def testCancelledExitKeepsUnappliedOrdinaryEditor(monkeypatch, designerApplication):
    window = MainWindow(_RuntimeClientStub())
    manager = window.operatorEditorManager
    editor = manager.open(projectId=window._currentProjectId(), workflowId=window.activeWorkflowId,
        nodeId="ordinary", operatorId="vision.value.number", displayName="Number",
        schema={"properties": {"value": {"type": "number", "default": 1}}}, values={"value": 1})
    editor._schemaForm._controls["value"].setValue(2)
    with monkeypatch.context() as patch:
        patch.setattr(window.pageCoordinator, "prepareClose", lambda: False)
        assert not window.close()
        assert manager.count() == 1 and not editor._disposed
        assert editor.collectParams()["value"] == 2
    editor.forceClose()
    window.close()
