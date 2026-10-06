"""Workspace exclusivity and independently owned execution/viewing lifetimes."""
import time
import threading
from types import SimpleNamespace

import pytest
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication

from test_workspace import designer  # noqa: F401
from test_pending_inputs import setupPage
from test_preview import waitFor


def testSharedActionsAndShortcutsCannotEditHiddenFlow(designer, monkeypatch):  # noqa: F811
    c, e, _ = setupPage(designer)
    chrome = c.chrome
    assert chrome.selector.text() == '当前：页面设计'
    assert not chrome.menus['运行'].menuAction().isVisible()
    calls = []
    monkeypatch.setattr(designer.flowScene, 'layoutNodesFlow', lambda: calls.append('layout'))
    for action in chrome.flowActions:
        assert not action.isEnabled() and not action.isVisible()
    designer._toolbarActions['自动布局'].trigger()
    QTest.keyClick(designer, Qt.Key_Delete)
    assert not calls
    assert not designer.deleteShortcut.isEnabled()
    designer.loadedProjectPath = 'test-project'
    designer.updateToolbarState()
    assert not designer._toolbarActions['开始运行'].isEnabled()
    assert chrome.previewAction.isVisible()
    before = c.session.payload()
    for _ in range(5):
        c.showFlow()
        assert chrome.selector.text() == '当前：流程设计'
        assert not e.isVisible() and not chrome.previewAction.isVisible()
        c.showPages()
    assert c.session.payload() == before
    assert [a.text() for a in designer.mainToolbar.actions() if a.isVisible() and a.text()] == [
        '打开项目', '保存项目', '撤销', '重做', '预览页面']


def testInvalidInputKeepsWorkspaceSelectionAndText(designer):  # noqa: F811
    c, e, key = setupPage(designer)
    e.tools.fields['fontSize'].setValue(1)
    before = c.session.payload()
    c.chrome.workspaceActions[0].trigger()
    assert c.pageActive() and c.chrome.workspaceActions[1].isChecked()
    assert c.chrome.selector.text() == '当前：页面设计'
    assert e.tools.fields['fontSize'].value() == 1 and e.tools.selected == key
    assert e.tools.propertyError.text() == '字号请选择“自动”，或输入 8–48。'
    assert 'validation error' in e.tools.propertyError.toolTip()
    assert c.session.payload() == before
    e.tools.fields['fontSize'].setValue(16)


def testPreviewCloseAndSamplesNeverStopOwnedTest(designer):  # noqa: F811
    c, e, _ = setupPage(designer)
    p = c.preview
    calls = []
    backend = SimpleNamespace(close=lambda: calls.append('stop'))
    p.backend = backend
    view = p.openObserver()
    assert view.designExamples and e.renderer.editing and e.renderer.hub is None
    view.close()
    waitFor(lambda: not p.busy)
    assert p.backend is backend and calls == []
    view = p.openObserver()
    p.stopViewing(view.showSamples)
    waitFor(lambda: not p.busy)
    assert view.designExamples and p.backend is backend and calls == []
    c.showFlow()
    assert not view.isVisible()
    c.chrome.testActions['停止测试'].trigger()
    waitFor(lambda: not p.active())
    assert calls == ['stop']


def testClosingDuringConnectionCleansLateSessionWithoutStoppingTest(designer):  # noqa: F811
    c, _e, _key = setupPage(designer)
    p = c.preview
    calls = []
    p.backend = SimpleNamespace(close=lambda: calls.append('stop'))
    view = p.openObserver()
    session = SimpleNamespace(close=lambda: calls.append('view-close'))
    def late():
        time.sleep(.03)
        p.session = session
        return {'kind': 'attached'}
    p.launch(late)
    view.close()
    waitFor(lambda: not p.busy)
    assert p.session is None and calls == ['view-close']
    assert p.backend is not None
    p.stopTest()
    waitFor(lambda: not p.active())
    assert calls == ['view-close', 'stop']


def testSinglePreviewDefaultsAndCompactPanels(designer):  # noqa: F811
    c, e, key = setupPage(designer)
    designer.resize(1280, 720)
    QApplication.processEvents()
    assert e.pageList.height() < 70
    assert e.tools.title.text() != key
    assert e.tools.applyButton.isVisible() and e.tools.applyButton.parent() is e.propertyScroll.parent()
    assert all(not button.isVisible() for button in e.renderer.buttons.values())
    view = c.preview.openObserver()
    assert view.designExamples and view.hub is None and c.preview.backend is None
    assert c.preview.openObserver() is view
    c.showFlow()
    assert not view.isVisible()
    c.showPages()
    assert not view.isVisible() and e.tools.selected == key
    assert c.preview.openObserver() is view and view.isVisible()


def testViewingFailureDoesNotRestoreSampleValues(designer):  # noqa: F811
    c, _e, _ = setupPage(designer)
    p = c.preview
    view = p.openObserver()
    assert view.designExamples
    with pytest.raises(ValueError):
        p.watchCurrent()
    assert not view.designExamples and view.source.currentIndex() == 1
    assert p.session is None and p.backend is None


def testProjectCloseEscalatesInFlightViewCleanup(designer):  # noqa: F811
    c, _e, _key = setupPage(designer)
    p = c.preview
    calls = []
    closing = threading.Event()
    release = threading.Event()
    def closeSession():
        closing.set()
        assert release.wait(3)
        calls.append('view-close')
    p.session = SimpleNamespace(close=closeSession)
    p.backend = SimpleNamespace(close=lambda: calls.append('stop-test'))
    p.stopViewing()
    assert closing.wait(3)
    p.closeAsync(lambda: calls.append('project-close'))
    release.set()
    waitFor(lambda: not p.active())
    assert calls == ['view-close', 'stop-test', 'project-close']
    assert not p.closing


def testConnectionFailureDuringCloseCompletesCleanup(designer):  # noqa: F811
    c, _e, _key = setupPage(designer)
    p = c.preview
    ready = threading.Event()
    release = threading.Event()
    calls = []
    def connect():
        ready.set()
        assert release.wait(3)
        raise ValueError('connection failed')
    p.launch(connect)
    assert ready.wait(3)
    p.closeAsync(lambda: calls.append('project-close'))
    release.set()
    waitFor(lambda: not p.active())
    assert calls == ['project-close'] and not p.closing


def testClosingPreviewCancelsPendingSampleCallback(designer):  # noqa: F811
    c, _e, _key = setupPage(designer)
    p = c.preview
    view = p.openObserver()
    ready = threading.Event()
    release = threading.Event()
    callbacks = []
    def closeSession():
        ready.set()
        assert release.wait(3)
    p.session = SimpleNamespace(close=closeSession)
    p.stopViewing(lambda: callbacks.append('show-deleted-preview'))
    assert ready.wait(3)
    view.close()
    release.set()
    waitFor(lambda: not p.active())
    assert callbacks == [] and p.observer is None


def testImageTestStatusBelongsToFlowWithoutOpeningPageEditor(designer):  # noqa: F811
    c = designer.pageCoordinator
    job = SimpleNamespace(status='COMPLETED', message='')
    c.preview.backend = SimpleNamespace(jobId='owned-test', close=lambda: None,
        runtime=SimpleNamespace(jobRepository=SimpleNamespace(getCurrentSnapshot=lambda _key: job)))
    c.preview.refreshJobStatus()
    assert c.editor is None and c.chrome.testStatus.isVisible()
    assert c.chrome.testStatus.text() == '图片测试：已完成'
    job.status, job.message = 'FAILED', '测试图片无法读取'
    c.preview.refreshJobStatus()
    assert c.chrome.testStatus.text() == '图片测试：失败 · 测试图片无法读取'
    c.preview.closeAsync()
    waitFor(lambda: not c.preview.active())


def testDuplicateInputNamesStillChooseExactImageNode(designer, tmp_path, monkeypatch):  # noqa: F811
    from PySide2.QtWidgets import QInputDialog, QFileDialog
    from examples.runtime_pages_p2 import sampleProject
    from emo_master.apps.designer.page_designer import resources
    c = designer.pageCoordinator
    document = sampleProject(tmp_path)
    source = next(node for node in document.workflows['main'].nodes if node.operatorId == 'vision.io.image_loader')
    second = source.model_copy(deep=True)
    second.nodeId = 'second-image-input'
    document.workflows['main'].nodes.append(second)
    designer.workflowController.loadPayload(document.model_dump())
    c.session.acceptLoaded()
    c.directory = tmp_path
    chosen = []
    def choose(_parent, _title, _label, labels, *_args):
        assert len(labels) == len(set(labels)) == 2
        return labels[1], True
    monkeypatch.setattr(QInputDialog, 'getItem', choose)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: ('fixture.png', ''))
    monkeypatch.setattr(resources, 'registerImage', lambda _s, _root, workflow, node, _path: chosen.append((workflow, node)))
    c.importInput()
    assert chosen == [('main', 'second-image-input')]
