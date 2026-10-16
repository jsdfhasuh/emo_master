"""Error visibility and safe, read-only navigation from the runtime log."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QCoreApplication, QEvent

from emo_master.apps.designer.state.flow_graph_model import FlowNode
from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel
from emo_master.apps.designer.ui.log_dialog import RuntimeLogDock, StructuredLogEntry


class Client:
    def listOperators(self):
        return []


class Settings:
    def __init__(self):
        self.values = {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


@pytest.fixture
def logDock(retainedQtApplication):
    dock = RuntimeLogDock(maximumEntries=3)
    yield dock
    dock.close()
    dock.deleteLater()
    QCoreApplication.sendPostedEvents(dock, QEvent.DeferredDelete)


def errorEntry(**kwargs):
    return StructuredLogEntry(
        1234, "ERROR", "图像文件不存在", source="runtime",
        eventType="node.failed", jobId="new-job", nodeId="new-node", **kwargs,
    )


def testOldFiltersExposeHiddenErrorsWithoutChangingUserConditions(logDock):
    view = logDock.view
    filters = {
        "job": "old-job", "node": "old-node", "search": "failed",
        "level": "WARN", "source": "editor", "event": "node.log",
        "autoScroll": False,
    }
    view.restoreSettings(filters)
    view.appendEntry(errorEntry())
    view.appendEntry(StructuredLogEntry(2345, "ERROR", "项目同步失败"))
    view._applyPendingEntries()

    assert all(view.settings()[key] == value for key, value in filters.items())
    assert view.table.rowCount() == 0
    assert "显示 0 / 2" in view.filterStatusLabel.text()
    assert "隐藏了 2 条错误" in view.filterStatusLabel.text()
    assert view.showErrorsButton.isEnabled()

    view.showErrorsButton.click()

    assert view.table.rowCount() == 2
    assert view.table.currentRow() == 1
    assert "项目同步失败" in view.detailViewer.toPlainText()
    assert view.searchInput.text() == ""
    for key in ("job", "node", "level", "source", "event"):
        assert view.settings()[key] == "全部"
    assert view.settings()["autoScroll"] is False
    assert not view.showErrorsButton.isEnabled()
    assert "未设置筛选" in view.filterStatusLabel.text()


@pytest.mark.parametrize("key,value", [
    ("job", "old-job"), ("node", "old-node"), ("search", "old query"),
    ("level", "INFO"), ("source", "designer"), ("event", "node.log"),
])
def testEachFilterReportsHiddenNewErrors(logDock, key, value):
    view = logDock.view
    view.restoreSettings({key: value})
    view.appendEntry(errorEntry())
    view._applyPendingEntries()
    assert view.settings()[key] == value
    assert "隐藏了 1 条错误" in view.filterStatusLabel.text()


def testHiddenErrorCountTracksEvictionFailureEventsAndClear(logDock):
    view = logDock.view
    view.restoreSettings({"search": "no match"})
    view.appendEntry(errorEntry())
    view.appendEntry(StructuredLogEntry(2, "WARN", "运行失败", eventType="workflow.failed"))
    view._applyPendingEntries()
    assert "隐藏了 2 条错误" in view.filterStatusLabel.text()
    for index in range(3):
        view.appendEntry(StructuredLogEntry(index + 3, "INFO", "普通日志"))
    view._applyPendingEntries()
    assert "隐藏了 0 条错误" in view.filterStatusLabel.text()
    assert not view.showErrorsButton.isEnabled()
    view.clearView()
    assert "显示 0 / 0" in view.filterStatusLabel.text()
    assert not view.locateButton.isEnabled()


def testButtonAndDoubleClickUseSelectedEntryAndExplainMissingNode(logDock):
    view = logDock.view
    calls = []
    view._onNavigate = lambda entry: calls.append(entry) or "已定位"
    first = errorEntry()
    noNode = StructuredLogEntry(2, "ERROR", "同步失败")
    view.setEntries([first, noNode])
    view.table.setCurrentCell(0, 0)
    assert view.locateButton.isEnabled()
    view.locateButton.click()
    assert calls == [first]
    assert view.navigationHint.text() == "已定位"
    view.table.cellDoubleClicked.emit(0, 0)
    assert calls == [first, first]

    view.table.cellDoubleClicked.emit(1, 0)
    assert calls == [first, first]
    assert not view.locateButton.isEnabled()
    assert "未提供节点关联" in view.navigationHint.text()
    view.searchInput.setText("图像")
    view.table.setCurrentCell(0, 0)
    view.locateButton.click()
    assert calls[-1] is first
    assert '"nodeId": "new-node"' in view.detailViewer.toPlainText()


def testNewLogsPreserveDetailTextSelectionAndNavigationFeedback(logDock):
    view = logDock.view
    view._onNavigate = lambda _entry: "已定位"
    view.setEntries([errorEntry()])
    view.table.setCurrentCell(0, 0)
    view.locateButton.click()
    view.detailViewer.selectAll()
    selected = view.detailViewer.textCursor().selectedText()
    view.appendEntry(StructuredLogEntry(2345, "INFO", "新日志"))
    view._applyPendingEntries()
    assert view.detailViewer.textCursor().selectedText() == selected
    assert view.navigationHint.text() == "已定位"


def addNode(window, value, nodeId="shared-node"):
    window.flowModel.nodes[nodeId] = FlowNode(
        nodeId, "core.literal.number", "数字", {}, {"value": "number"},
        params={"value": value},
    )
    window.flowScene.addFlowNode(FlowNodeViewModel(
        nodeId, "数字", 600, 400, {}, {"value": "number"},
    ))
    window.workflowController.captureActiveWorkflow()


def acceptedError(window, workflowId="main", projectId=None, jobId="job-1"):
    window.runtimeController.callbacks["onJobAccepted"](SimpleNamespace(job_id=jobId))
    window.appendRuntimeEvent({
        "timestampMs": 1234, "level": "ERROR", "message": "图像文件不存在",
        "eventType": "node.failed", "jobId": jobId,
        "projectId": window._currentProjectId() if projectId is None else projectId,
        "workflowId": workflowId, "workflowRunId": "workflow-run",
        "nodeId": "shared-node", "nodeRunId": "node-run", "iterationPath": [2],
        "payload": {"path": "missing.png"},
    })
    return window.logEntries[-1]


@pytest.mark.parametrize("dirty", [False, True])
def testNavigationChangesWorkflowAndSelectionWithoutApplyingParamsOrDirty(
    ownedDesignerWindow, monkeypatch, dirty,
):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    childId = window.createWorkflow("Child")
    addNode(window, 2)
    entry = acceptedError(window, childId)
    window.activateWorkflow("main")
    window.pageCoordinator.sync()
    window.pageCoordinator.session.markSaved()
    if dirty:
        window.flowModel.nodes["shared-node"].params["value"] = 3
        window.pageCoordinator.sync()
    session = window.pageCoordinator.session
    before = deepcopy(session.payload())
    history = deepcopy(session._undo)
    centered = []
    monkeypatch.setattr(window.flowView, "centerOn", lambda *point: centered.append(point))
    monkeypatch.setattr(window, "_applyEditorParams", lambda *_: pytest.fail("navigation applied params"))

    window.logDock.view.setEntries([entry])
    window.logDock.view.table.cellDoubleClicked.emit(0, 0)

    assert window.getActiveWorkflowId() == childId
    assert window.flowModel.selectedNodeId == "shared-node"
    assert centered
    assert "已定位" in window.logDock.view.navigationHint.text()
    assert session.payload() == before
    assert session.dirty is dirty
    assert session._undo == history
    assert window.nodeParamDialog is None
    assert entry.detailPayload()["projectId"] == window._currentProjectId()
    assert entry.detailPayload()["nodeRunId"] == "node-run"
    assert entry.iterationPath == (2,)
    assert entry.payload == {"path": "missing.png"}


@pytest.mark.parametrize("change,expected", [
    ({"projectId": "another-project"}, "其他工程"),
    ({"projectId": ""}, "缺少工程身份"),
    ({"projectInstanceToken": "old-instance"}, "历史会话"),
    ({"projectInstanceToken": ""}, "无法确认"),
    ({"workflowId": "removed-workflow"}, "工作流"),
    ({"workflowId": ""}, "工作流"),
    ({"nodeId": "removed-node"}, "已不存在"),
    ({"nodeId": ""}, "未提供节点关联"),
])
def testUnsafeLogIdentitiesNeverNavigate(ownedDesignerWindow, monkeypatch, change, expected):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    entry = replace(acceptedError(window), **change)
    before = window.pageCoordinator.session.payload()
    monkeypatch.setattr(window, "activateWorkflow", lambda *_: pytest.fail("unsafe workflow navigation"))
    monkeypatch.setattr(window, "navigateToNodeFromSidebar", lambda *_: pytest.fail("unsafe node navigation"))

    assert expected in window.navigateToRuntimeLogEntry(entry)
    assert window.pageCoordinator.session.payload() == before


def testUnknownOrMismatchedRuntimeEventsCannotBorrowCurrentProjectIdentity(ownedDesignerWindow):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    mismatch = acceptedError(window, projectId="another-project")
    assert mismatch.projectInstanceToken == ""
    assert "其他工程" in window.navigateToRuntimeLogEntry(mismatch)
    window.appendRuntimeEvent({
        "jobId": "unknown-job", "projectId": window._currentProjectId(),
        "workflowId": "main", "nodeId": "shared-node", "level": "ERROR",
    })
    unknown = window.logEntries[-1]
    assert unknown.projectInstanceToken == ""
    assert "无法确认" in window.navigateToRuntimeLogEntry(unknown)


def testProjectReopenRejectsHistoryAndLateEventsWithSameNodeAndProjectIds(ownedDesignerWindow):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    old = acceptedError(window)
    window._applyLoadedProjectState(None, None)

    assert "历史会话" in window.navigateToRuntimeLogEntry(old)
    window.appendRuntimeEvent(old.detailPayload())
    late = window.logEntries[-1]
    assert late.projectInstanceToken == ""
    assert "无法确认" in window.navigateToRuntimeLogEntry(late)
    fresh = acceptedError(window, jobId="fresh-job")
    assert "已定位" in window.navigateToRuntimeLogEntry(fresh)


def testKnownJobSuppliesProjectIdentityForLegacyEvent(ownedDesignerWindow):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    entry = acceptedError(window, projectId="")
    assert entry.projectId == window._currentProjectId()
    assert entry.projectInstanceToken == window._runtimeLogProjectToken
    assert "已定位" in window.navigateToRuntimeLogEntry(entry)


@pytest.mark.parametrize("destination", ["same", "parent", "alias"])
def testSavingSameCanonicalProjectPreservesLogNavigation(ownedDesignerWindow, tmp_path, destination):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    project = tmp_path / "original.emoproj"
    assert window.saveProjectToDirectory(str(project))
    entry = acceptedError(window)
    token = window._runtimeLogProjectToken
    target = project
    if destination == "parent":
        target = project.parent
    elif destination == "alias":
        (tmp_path / "alias").mkdir()
        target = tmp_path / "alias" / ".." / project.name

    assert window.saveProjectToDirectory(str(target))
    assert window._runtimeLogProjectToken == token
    assert "已定位" in window.navigateToRuntimeLogEntry(entry)


def testSaveAsRetiresOnlyLogIdentityAndRejectsLateOriginalJob(ownedDesignerWindow, tmp_path):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    assert window.saveProjectToDirectory(str(tmp_path / "original.emoproj"))
    old = acceptedError(window)
    sharedToken = window._projectInstanceToken

    assert window.saveProjectToDirectory(str(tmp_path / "copy.emoproj"))
    assert window._currentProjectId() == old.projectId
    assert window._projectInstanceToken == sharedToken
    assert window._runtimeLogProjectToken != old.projectInstanceToken
    assert window._runtimeLogJobProjects == {}
    assert "另存为前" in window.navigateToRuntimeLogEntry(old)
    window.appendRuntimeEvent(old.detailPayload())
    late = window.logEntries[-1]
    assert not late.projectInstanceToken
    assert "无法确认" in window.navigateToRuntimeLogEntry(late)
    fresh = acceptedError(window, jobId="copy-job")
    assert "已定位" in window.navigateToRuntimeLogEntry(fresh)


@pytest.mark.parametrize("failResolve", [False, True])
def testFailedSaveAsPreservesOriginalLogIdentity(ownedDesignerWindow, tmp_path, monkeypatch, failResolve):
    from emo_master.apps.designer.ui.main_window import QMessageBox

    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    assert window.saveProjectToDirectory(str(tmp_path / "original.emoproj"))
    old = acceptedError(window)
    token = window._runtimeLogProjectToken
    jobs = dict(window._runtimeLogJobProjects)
    monkeypatch.setattr(window.projectController, "saveProjectToDirectory", lambda *_: (False, None))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: None)
    if failResolve:
        originalResolve = Path.resolve

        def resolve(path, *args, **kwargs):
            if path == window.projectController.currentProjectFile:
                raise OSError("original path unavailable")
            return originalResolve(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        if failResolve:
            patch.setattr(Path, "resolve", resolve)
        assert not window.saveProjectToDirectory(str(tmp_path / "failed-copy.emoproj"))
    assert window._runtimeLogProjectToken == token
    assert window._runtimeLogJobProjects == jobs
    assert "已定位" in window.navigateToRuntimeLogEntry(old)


@pytest.mark.parametrize("phase", ["previous", "current"])
@pytest.mark.parametrize("errorType", [OSError, RuntimeError])
def testPathIdentityFailureCannotBlockSuccessfulSaveAs(
    ownedDesignerWindow, tmp_path, monkeypatch, phase, errorType,
):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    original = tmp_path / "original.emoproj"
    target = tmp_path / "recovered.emoproj"
    assert window.saveProjectToDirectory(str(original))
    old = acceptedError(window)
    sharedToken = window._projectInstanceToken
    originalResolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == (original if phase == "previous" else target):
            raise errorType("path unavailable")
        return originalResolve(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", resolve)
        assert window.saveProjectToDirectory(str(target))
    assert target.exists()
    assert window.loadedProjectPath == str(target)
    assert window._projectInstanceToken == sharedToken
    assert window._runtimeLogProjectToken != old.projectInstanceToken
    assert window._runtimeLogJobProjects == {}
    assert "另存为前" in window.navigateToRuntimeLogEntry(old)
