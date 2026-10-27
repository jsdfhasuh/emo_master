from copy import deepcopy

from PySide2.QtCore import Qt, QEvent, QPointF
from PySide2.QtGui import QFocusEvent
from PySide2.QtTest import QTest

from emo_master.apps.designer.ui.main_window import MainWindow
from tests.designer.test_main_window_workflow_tabs import _RuntimeClientStub


def nodeWindow():
    window = MainWindow(_RuntimeClientStub())
    window.show()
    window.addNodeFromOperatorPayload({"operatorId": "vision.value.number", "displayName": "Original",
                                      "inputPorts": {}, "outputPorts": {"value": "number"}}, 50, 50)
    nodeId = next(node.nodeId for node in window.flowModel.nodes.values() if node.operatorId == "vision.value.number")
    return window, nodeId


def testChineseTitleEditCancelValidationAndSaveReopen(tmp_path, designerApplication):
    window, nodeId = nodeWindow()
    item = window.flowScene._nodeItems[nodeId]
    geometry = item.rect()
    positions = deepcopy(window.flowScene.getNodePositions())
    item.beginRename()
    edit = item._titleProxy.widget()
    QTest.keyClick(edit, Qt.Key_Delete)
    window.deleteSelectedElements()
    assert nodeId in window.flowModel.nodes
    edit.setText("  中文相机节点  ")
    QTest.keyClick(edit, Qt.Key_Return)
    assert window.flowModel.nodes[nodeId].displayName == "中文相机节点"
    assert item.model.title == "中文相机节点"
    assert item.rect() == geometry
    assert window.flowScene.getNodePositions() == positions
    assert window.pageCoordinator.session.dirty
    for text, key in (("取消此名称", Qt.Key_Escape), ("   ", Qt.Key_Return), ("长" * 65, Qt.Key_Return)):
        item.beginRename()
        edit = item._titleProxy.widget()
        edit.setText(text)
        QTest.keyClick(edit, key)
        assert window.flowModel.nodes[nodeId].displayName == "中文相机节点"
    target = tmp_path / "renamed.emoproj"
    assert window.saveProjectToDirectory(str(target))
    other = MainWindow(_RuntimeClientStub())
    assert other.loadProjectDirectory(str(target))
    assert other.flowModel.nodes[nodeId].displayName == "中文相机节点"
    assert other.flowModel.edges == window.flowModel.edges
    other.close()
    window.close()


def testRenameUsesSharedUndoAndUpdatesOpenEditor(designerApplication):
    window, nodeId = nodeWindow()
    editor = window.operatorEditorManager.open(projectId=window._currentProjectId(),
        workflowId=window.activeWorkflowId, nodeId=nodeId, operatorId="vision.value.number",
        displayName="Original", schema={}, values={}, operatorDefinition={"displayName": "数值常量"})
    window.renameNode(nodeId, "Renamed")
    assert editor.windowTitle().startswith("Renamed")
    assert "数值常量" in editor.windowTitle()
    window.pageCoordinator.history()
    assert window.flowModel.nodes[nodeId].displayName == "Original"
    assert editor.windowTitle().startswith("Original")
    assert "数值常量" in editor.windowTitle()
    window.pageCoordinator.history(redo=True)
    assert window.flowScene._nodeItems[nodeId].model.title == "Renamed"
    assert editor.windowTitle().startswith("Renamed")
    assert editor._operatorIdentityLabel.text() == "原始算子：数值常量\n算子 ID：vision.value.number"
    window.close()


def testFocusOutCommitsButRunningWorkflowCannotRename(designerApplication):
    window, nodeId = nodeWindow()
    item = window.flowScene._nodeItems[nodeId]
    item.beginRename()
    edit = item._titleProxy.widget()
    edit.setText("Focus committed")
    designerApplication.sendEvent(edit, QFocusEvent(QEvent.FocusOut))
    designerApplication.processEvents()
    assert window.flowModel.nodes[nodeId].displayName == "Focus committed"
    window.isJobRunning = True
    item.beginRename()
    assert item._titleProxy is None
    assert window.renameNode(nodeId, "Blocked") == "Focus committed"
    window.isJobRunning = False
    window.close()


def testTitleDoubleClickRenamesAndBodyDoubleClickOpensConfiguration(designerApplication):
    window, nodeId = nodeWindow()
    item = window.flowScene._nodeItems[nodeId]
    opened = []
    window.flowScene.setNodeDoubleClickHandler(opened.append)
    window.flowView.centerOn(item)
    designerApplication.processEvents()

    def doubleClick(y):
        point = window.flowView.mapFromScene(item.mapToScene(QPointF(item.rect().width() / 2, y)))
        QTest.mouseClick(window.flowView.viewport(), Qt.LeftButton, pos=point)
        QTest.mouseDClick(window.flowView.viewport(), Qt.LeftButton, pos=point)
        QTest.mouseRelease(window.flowView.viewport(), Qt.LeftButton, pos=point)
        designerApplication.processEvents()

    doubleClick(20)
    assert item._titleProxy is not None and opened == []
    QTest.keyClick(item._titleProxy.widget(), Qt.Key_Escape)
    doubleClick(item.geometry.header + 10)
    assert opened == [nodeId]
    window.close()
