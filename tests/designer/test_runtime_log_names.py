"""Human-readable log context with workflow-scoped node filters."""
from dataclasses import replace
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.ui.log_dialog import LogDialog, StructuredLogEntry
from tests.designer.test_runtime_log_usability import Client, Settings, addNode, acceptedError


def entry(workflow="capture", node="output", **changes):
    return replace(StructuredLogEntry(
        1234, "INFO", f"node completed: {node}", source="runtime",
        eventType="node.completed", projectId="project", workflowId=workflow,
        nodeId=node, workflowName={"capture": "图像采集", "detect": "缺陷检测"}.get(workflow, workflow),
        nodeName="工作流输出",
    ), **changes)


def populatedDialog(entries):
    dialog = LogDialog()
    dialog.setEntries(entries)
    return dialog


def testNamesAreProminentWhileDetailsAndTooltipsKeepRawIds():
    record = entry()
    dialog = populatedDialog([record])
    view = dialog.view
    assert view.table.item(0, 4).text() == "图像采集"
    assert view.table.item(0, 5).text() == "工作流输出"
    assert view.table.item(0, 9).text() == "节点执行完成"
    assert "capture" in view.table.item(0, 4).toolTip()
    assert "output" in view.table.item(0, 5).toolTip()
    assert view.nodeFilter.itemText(1) == "图像采集 / 工作流输出"
    header = view.table.horizontalHeader()
    assert header.visualIndex(4) < header.visualIndex(5) < header.visualIndex(9)
    assert header.visualIndex(9) < header.visualIndex(3)
    view.table.setCurrentCell(0, 0)
    details = json.loads(view.detailViewer.toPlainText())
    assert details["workflowName"] == "图像采集"
    assert details["nodeName"] == "工作流输出"
    assert details["workflowId"] == "capture"
    assert details["nodeId"] == "output"
    assert details["message"] == "node completed: output"


def testWorkflowAndNodeSelectionUseIdsInsteadOfDisplayText():
    capture, detect = entry(), entry("detect")
    dialog = populatedDialog([capture, detect])
    view = dialog.view
    index = view.nodeFilter.findData(list(capture.nodeKey()))
    assert index > 0
    view.nodeFilter.setCurrentIndex(index)
    assert view._visibleEntries == [capture]

    index = view.workflowFilter.findData(list(detect.workflowKey()))
    assert index > 0
    view.workflowFilter.setCurrentIndex(index)
    assert view.nodeFilter.currentData() is None
    assert view._visibleEntries == [detect]
    assert view.nodeFilter.count() == 2
    assert view.nodeFilter.itemText(1) == "缺陷检测 / 工作流输出"
    view.nodeFilter.setCurrentIndex(1)
    view.workflowFilter.setCurrentIndex(0)
    assert view._visibleEntries == [detect]
    assert view.nodeFilter.currentData() == list(detect.nodeKey())


def testDuplicateNamesHaveDistinctChoicesWithoutChangingIdentity():
    records = [
        entry("capture", "camera-a", workflowName="采集", nodeName="相机"),
        entry("capture", "camera-b", workflowName="采集", nodeName="相机"),
        entry("detect", "camera-a", workflowName="采集", nodeName="相机"),
        entry("capture", "camera-a", workflowName="采集", nodeName="相机", projectId="other-project"),
    ]
    dialog = populatedDialog(records)
    view = dialog.view
    workflowLabels = [view.workflowFilter.itemText(i) for i in range(1, view.workflowFilter.count())]
    nodeLabels = [view.nodeFilter.itemText(i) for i in range(1, view.nodeFilter.count())]
    assert len(set(workflowLabels)) == 3
    assert len(set(nodeLabels)) == 4
    for record in records:
        view.nodeFilter.setCurrentIndex(view.nodeFilter.findData(list(record.nodeKey())))
        assert view._visibleEntries == [record]


@pytest.mark.parametrize("query", ["图像采集", "工作流输出", "capture", "output", "节点执行完成"])
def testSearchFindsFriendlyNamesAndOriginalIdentifiers(query):
    record = entry()
    dialog = populatedDialog([record, StructuredLogEntry(2, "INFO", "unrelated")])
    dialog.view.searchInput.setText(query)
    assert dialog.view._visibleEntries == [record]


def testScopedFiltersRestoreBeforeEventsAndKeepFriendlyLabels():
    record = entry()
    original = populatedDialog([record, entry("detect")])
    original.view.workflowFilter.setCurrentIndex(original.view.workflowFilter.findData(list(record.workflowKey())))
    original.view.nodeFilter.setCurrentIndex(original.view.nodeFilter.findData(list(record.nodeKey())))
    preferences = original.view.settings()
    restored = LogDialog()
    restored.view.restoreSettings(preferences)
    assert restored.view.workflowFilter.currentData() == list(record.workflowKey())
    assert restored.view.nodeFilter.currentData() == list(record.nodeKey())
    assert restored.view.nodeFilter.currentText() == "图像采集 / 工作流输出"
    restored.appendEntry(entry("detect"))
    restored.appendEntry(record)
    restored.view._applyPendingEntries()
    assert restored.view._visibleEntries == [record]
    assert restored.view.settings()["node"] == ["project", "capture", "output"]


def testScopedPreferencesRoundTripThroughNativeQSettings(tmp_path):
    from PySide2.QtCore import QSettings

    record = entry()
    original = populatedDialog([record])
    original.view.workflowFilter.setCurrentIndex(1)
    original.view.nodeFilter.setCurrentIndex(1)
    path = str(tmp_path / "log.ini")
    settings = QSettings(path, QSettings.IniFormat)
    settings.setValue("ui/runtime_log_view", original.view.settings())
    settings.sync()
    restored = populatedDialog([record, entry("detect")])
    restored.view.restoreSettings(QSettings(path, QSettings.IniFormat).value("ui/runtime_log_view"))
    assert restored.view._visibleEntries == [record]
    assert restored.view.nodeFilter.currentData() == list(record.nodeKey())


def testLegacyUnscopedFilterCanBeReplacedWithNamedWorkflowNode():
    records = [entry(), entry("detect")]
    dialog = populatedDialog(records)
    view = dialog.view
    view.restoreSettings({"node": "output"})
    assert view._visibleEntries == records
    assert view.settings()["node"] == "output"
    assert view.nodeFilter.currentText() == "全部工作流 / output"
    view.nodeFilter.setCurrentIndex(view.nodeFilter.findData(list(records[1].nodeKey())))
    assert view._visibleEntries == [records[1]]


def testClearFiltersRecoversEmptyViewWithoutClearingLogs():
    records = [entry(), entry("detect")]
    dialog = populatedDialog(records)
    view = dialog.view
    view.workflowFilter.setCurrentIndex(view.workflowFilter.findData(list(records[0].workflowKey())))
    view.nodeFilter.setCurrentIndex(1)
    view.searchInput.setText("not present")
    assert view.table.rowCount() == 0
    assert not view.showErrorsButton.isEnabled()
    assert view.resetFiltersButton.isEnabled()
    view.resetFiltersButton.click()
    assert view._entries == records
    assert view._visibleEntries == records
    assert view.workflowFilter.currentData() is None
    assert view.nodeFilter.currentData() is None
    assert not view.resetFiltersButton.isEnabled()


def testShowErrorsClearsWorkflowAndNodeFilters():
    records = [entry(), entry("detect", level="ERROR", message="missing image")]
    dialog = populatedDialog(records)
    view = dialog.view
    view.workflowFilter.setCurrentIndex(view.workflowFilter.findData(list(records[0].workflowKey())))
    view.nodeFilter.setCurrentIndex(1)
    assert view._visibleEntries == [records[0]]
    assert view.showErrorsButton.isEnabled()
    view.showErrorsButton.click()
    assert view._visibleEntries == records
    assert view.table.currentRow() == 1
    assert "missing image" in view.detailViewer.toPlainText()


def testWorkflowNamedAllIsStillAnActiveFilter():
    dialog = populatedDialog([entry(workflowName="全部"), entry("detect")])
    dialog.view.workflowFilter.setCurrentIndex(1)
    assert dialog.view.resetFiltersButton.isEnabled()


def testSearchRowKeepsClearActionCompact(designerApplication):
    dialog = populatedDialog([entry()])
    dialog.resize(1000, 640)
    dialog.show()
    designerApplication.processEvents()
    assert dialog.view.searchInput.width() > dialog.view.resetFiltersButton.width() * 3


def testCustomMessagesAreNotRewritten():
    record = entry(message="custom output: important diagnostic", eventType="node.completed")
    assert record.displayMessage() == record.message


def testAcceptedEventsCaptureWorkflowAndNodeNamesAcrossWorkflows(ownedDesignerWindow):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    window.workflowStore.workflows["main"].name = "主流程"
    addNode(window, 1)
    window.flowModel.nodes["shared-node"].displayName = "读取图像"
    window.workflowController.captureActiveWorkflow()
    child = window.createWorkflow("缺陷检测")
    addNode(window, 2)
    window.flowModel.nodes["shared-node"].displayName = "检测划痕"
    window.workflowController.captureActiveWorkflow()
    window.activateWorkflow("main")

    mainEntry = acceptedError(window)
    childEntry = acceptedError(window, child)
    assert (mainEntry.workflowName, mainEntry.nodeName) == ("主流程", "读取图像")
    assert (childEntry.workflowName, childEntry.nodeName) == ("缺陷检测", "检测划痕")

    window.flowModel.nodes["shared-node"].displayName = "重命名节点"
    window.workflowStore.workflows["main"].name = "重命名流程"
    assert (mainEntry.workflowName, mainEntry.nodeName) == ("主流程", "读取图像")
    window._applyLoadedProjectState(None, None)
    assert (mainEntry.workflowName, mainEntry.nodeName) == ("主流程", "读取图像")


def testUnacceptedOrWrongProjectEventDoesNotBorrowCurrentNames(ownedDesignerWindow):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    window.runtimeController.callbacks["onJobAccepted"](SimpleNamespace(job_id="known"))
    for job, project in (("unknown", window._currentProjectId()), ("known", "another-project")):
        result = window._runtimeLogEntry({
            "jobId": job, "projectId": project, "workflowId": "main", "nodeId": "shared-node",
        })
        assert result.workflowName == result.nodeName == ""


def testBoundaryNodesUseReadableDefaultNames(ownedDesignerWindow):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    window.runtimeController.callbacks["onJobAccepted"](SimpleNamespace(job_id="known"))
    for node, name in (("__workflow_input__", "工作流输入"), ("__workflow_output__", "工作流输出")):
        result = window._runtimeLogEntry({
            "jobId": "known", "workflowId": "main", "nodeId": node,
        })
        assert result.nodeName == name


def testLogNavigationRevealsCanvasWithoutApplyingPageInputs(
    ownedDesignerWindow, designerApplication, monkeypatch,
):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    window.show()
    addNode(window, 1)
    record = acceptedError(window)
    window.pageCoordinator.showPages()
    assert window.pageCoordinator.pageActive()
    before = deepcopy(window.pageCoordinator.session.payload())
    history = deepcopy(window.pageCoordinator.session._undo)
    window.shutdownOperatorDisplay()
    designerApplication.processEvents()
    dialog = window.logDialog
    dialog.setEntries([record])
    window.openLogDialog()
    dialog.showMaximized()
    designerApplication.processEvents()
    with monkeypatch.context() as patch:
        patch.setattr(window.pageCoordinator.editor.tools, "commitPending",
                      lambda: pytest.fail("read-only navigation applied page inputs"))
        dialog.view.table.setCurrentCell(0, 0)
        dialog.view.locateButton.click()
        designerApplication.processEvents()
    assert not dialog.isVisible()
    assert window.pageCoordinator.flowActive()
    assert window.flowModel.selectedNodeId == record.nodeId
    assert window.pageCoordinator.session.payload() == before
    assert window.pageCoordinator.session._undo == history
    retained = list(dialog.view._entries)
    assert retained[0] is record
    window.openLogDialog()
    assert dialog.isMaximized()
    assert dialog.view._entries == retained
