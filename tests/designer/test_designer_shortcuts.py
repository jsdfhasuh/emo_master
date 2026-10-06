"""Send real Qt keys through project, flow, page and input focus boundaries."""
from copy import deepcopy
import json
import time

import pytest
import emo_master  # noqa: F401 - preserve native Windows bootstrap order
from PySide2.QtCore import QEvent, Qt
from PySide2.QtGui import QKeyEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import (
    QApplication, QDialog, QFileDialog, QLineEdit, QMessageBox, QSpinBox,
    QTableWidget, QTableWidgetItem,
)

from emo_master.apps.designer.ui.designer_actions import SHORTCUTS
from tests.designer.test_node_context_menu import canvas, Client, PAYLOAD, actions  # noqa: F401
from tests.designer.test_run_inspector_ui import deliver


def focusCanvas(window):
    window.resize(1280, 720)
    window.show()
    window.activateWindow()
    editor = window.designerActions.pageEditor()
    surface = editor.renderer if editor else window.flowView
    surface.setFocusPolicy(Qt.StrongFocus)
    surface.setFocus()
    QApplication.processEvents()
    assert QApplication.activeWindow() is window
    return surface


def press(window, key, modifiers=Qt.NoModifier):
    QTest.keyClick(focusCanvas(window), key, modifiers)
    QApplication.processEvents()


def waitUntil(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.005)
    assert predicate()


def makePage(window, monkeypatch):
    original = QMessageBox.question
    monkeypatch.setattr(QMessageBox, 'question', lambda parent, title, *args, **kwargs:
                        QMessageBox.Yes if title == '启用页面设计' else original(parent, title, *args, **kwargs))
    coordinator = window.pageCoordinator
    coordinator.showPages()
    editor = coordinator.editor
    editor.pageId = editor.store.createPage('快捷键运行页')
    key = editor.tools.commands().add(editor.pageId, 'text', 0, 0)
    editor.refresh()
    editor.tools.select(key)
    coordinator.sync()
    return editor, key


def testCanonicalActionsOwnKeysAndContextMenuOnlyDisplaysThem(canvas):  # noqa: F811
    window, (first, _second), _client = canvas
    registry = window.designerActions
    assert set(registry.actions) == set(SHORTCUTS)
    assert window._toolbarActions['保存项目'] is registry.actions['save']
    assert window._toolbarActions['开始运行'] is registry.actions['run']
    assert window._menuActions['复制所选算子'] is registry.actions['copy']
    assert window.deleteShortcut.key().isEmpty() and window.backspaceDeleteShortcut.key().isEmpty()
    menu = actions(window.nodeContextMenu.buildMenu(first))
    for key, action in menu.items():
        assert registry.shortcutLabel(key) in action.text()
        assert action.shortcut().isEmpty()  # No competing menu shortcut registration.
    assert len([a for a in window.actions() if a.shortcut().toString() == 'Ctrl+Z']) == 1


@pytest.mark.parametrize('key', [Qt.Key_Delete, Qt.Key_Backspace])
def testDeleteKeyUsesSingleUndoTransaction(canvas, key):  # noqa: F811
    window, (first, _second), client = canvas
    original = set(window.flowModel.nodes)
    depth = len(window.pageCoordinator.session._undo)
    window.flowScene.setNodeSelected(first)
    press(window, key)
    assert set(window.flowModel.nodes) == original - {first}
    assert len(window.pageCoordinator.session._undo) == depth + 1
    press(window, Qt.Key_Z, Qt.ControlModifier)
    assert set(window.flowModel.nodes) == original
    assert client.starts == client.stops == 0


@pytest.mark.parametrize('redoKey,modifiers', [(Qt.Key_Y, Qt.ControlModifier),
                                           (Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)])
def testCopyUndoAndBothRedoKeys(canvas, redoKey, modifiers):  # noqa: F811
    window, (first, _second), _client = canvas
    original = set(window.flowModel.nodes)
    originalParams = deepcopy(window.flowModel.nodes[first].params)
    depth = len(window.pageCoordinator.session._undo)
    window.flowScene.setNodeSelected(first)
    press(window, Qt.Key_D, Qt.ControlModifier)
    copied, = set(window.flowModel.nodes) - original
    assert window.flowModel.nodes[copied].params == originalParams
    assert len(window.pageCoordinator.session._undo) == depth + 1
    press(window, Qt.Key_Z, Qt.ControlModifier)
    assert set(window.flowModel.nodes) == original
    press(window, redoKey, modifiers)
    assert set(window.flowModel.nodes) == original | {copied}


def testF2OpensActualExistingEditor(canvas):  # noqa: F811
    window, (first, _second), client = canvas
    window.flowScene.setNodeSelected(first)
    press(window, Qt.Key_F2)
    assert window.activeParamNodeId == first
    assert window.nodeParamDialog.isVisible()
    assert window.operatorEditorManager.count() == 1
    assert client.starts == client.stops == 0


@pytest.mark.parametrize('key', [Qt.Key_Return, Qt.Key_Enter])
def testResultKeysReuseReadonlyZeroResultWhileRunning(canvas, key):  # noqa: F811
    window, (first, _second), client = canvas
    window.flowScene.setNodeSelected(first)
    window._setCurrentJobId('job')
    window.nodeResultCoordinator.accepted('job')
    deliver(window, first, value=0)
    before = window.pageCoordinator.session.payload()
    window._setIsJobRunning(True)
    try:
        press(window, key, Qt.ControlModifier)
        assert 'count = 0' in window.nodeResultCoordinator.panel.values.toPlainText()
        assert window.pageCoordinator.session.payload() == before
        assert client.starts == client.stops == 0
    finally:
        window._setIsJobRunning(False)


def testOpenAndSaveKeysUseExistingDialogsAndSaveActualProject(canvas, monkeypatch, tmp_path):  # noqa: F811
    window, _nodes, _client = canvas
    dialogs = []
    def openFile(*args):
        dialogs.append('open')
        return '', ''
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', openFile)
    press(window, Qt.Key_O, Qt.ControlModifier)
    assert dialogs == ['open']
    directory = tmp_path / '中文 快捷键项目'
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *args: str(directory))
    press(window, Qt.Key_S, Qt.ControlModifier)
    payload = json.loads((directory / 'project.json').read_text(encoding='utf-8'))
    assert payload['workflows']['main']['nodes']
    assert not window.pageCoordinator.session.dirty


@pytest.mark.parametrize('kind', ['line', 'spin'])
def testInputDeleteAndUndoRemainNativeAndNeverChangeGraph(canvas, kind):  # noqa: F811
    window, (first, _second), _client = canvas
    window.flowScene.setNodeSelected(first)
    focusCanvas(window)
    control = QLineEdit(window) if kind == 'line' else QSpinBox(window)
    if kind == 'spin':
        control.setRange(0, 9999)
    control.show()
    control.setFocus()
    field = control if kind == 'line' else control.lineEdit()
    field.setText('12')
    field.setCursorPosition(2)
    before = window.pageCoordinator.session.payload()
    QApplication.processEvents()
    QTest.keyClicks(field, '3')
    assert field.text() == '123'
    QTest.keyClick(field, Qt.Key_Backspace)
    assert field.text() == '12'
    QTest.keyClick(field, Qt.Key_Z, Qt.ControlModifier)
    assert field.text() == '123'
    for key, modifiers in [(Qt.Key_D, Qt.ControlModifier), (Qt.Key_F2, Qt.NoModifier),
                           (Qt.Key_Delete, Qt.NoModifier), (Qt.Key_Home, Qt.NoModifier)]:
        QTest.keyClick(field, key, modifiers)
    assert window.pageCoordinator.session.payload() == before
    assert window.operatorEditorManager.count() == 0


@pytest.mark.parametrize('key,modifiers', [(Qt.Key_D, Qt.ControlModifier), (Qt.Key_Delete, Qt.NoModifier),
    (Qt.Key_Backspace, Qt.NoModifier), (Qt.Key_F2, Qt.NoModifier), (Qt.Key_L, Qt.ControlModifier),
    (Qt.Key_Z, Qt.ControlModifier), (Qt.Key_Y, Qt.ControlModifier)])
def testRunningKeysDoNotMutateGraphOrHistory(canvas, key, modifiers):  # noqa: F811
    window, (first, _second), _client = canvas
    window.flowScene.setNodeSelected(first)
    before = window.pageCoordinator.session.payload()
    positions = window.flowScene.getNodePositions()
    depth = len(window.pageCoordinator.session._undo)
    window._setIsJobRunning(True)
    try:
        press(window, key, modifiers)
        assert window.pageCoordinator.session.payload() == before
        assert window.flowScene.getNodePositions() == positions
        assert len(window.pageCoordinator.session._undo) == depth
        assert window.operatorEditorManager.count() == 0
    finally:
        window._setIsJobRunning(False)


def testRunStopKeysUseOriginalControllerAndDoNotRepeatOnHeldF5(canvas, monkeypatch):  # noqa: F811
    window, _nodes, _client = canvas
    calls = []
    monkeypatch.setattr(window.runtimeController, 'startJob', lambda: calls.append('start'))
    monkeypatch.setattr(window.runtimeController, 'stopJob', lambda: calls.append('stop'))
    press(window, Qt.Key_F5)
    press(window, Qt.Key_F6)
    assert calls == []
    window.loadedProjectPath = 'test-fixture-project'
    window.updateToolbarState()
    surface = focusCanvas(window)
    QApplication.sendEvent(surface, QKeyEvent(QEvent.KeyPress, Qt.Key_F5, Qt.NoModifier))
    for _ in range(4):
        QApplication.sendEvent(surface, QKeyEvent(QEvent.KeyPress, Qt.Key_F5, Qt.NoModifier, '', True))
    assert calls == ['start']
    window._setIsJobRunning(True)
    window._setCurrentJobId('test-fixture-job')
    window.updateToolbarState()
    try:
        press(window, Qt.Key_F6)
        assert calls == ['start', 'stop']
    finally:
        window._setIsJobRunning(False)


def testLayoutFitAndLogsKeysCallExistingCommands(canvas, monkeypatch):  # noqa: F811
    window, _nodes, _client = canvas
    called = []
    monkeypatch.setattr(window.flowScene, 'layoutNodesFlow', lambda: called.append('layout'))
    monkeypatch.setattr(window.flowView, 'fitContent', lambda *args, **kwargs: called.append('fit'))
    press(window, Qt.Key_L, Qt.ControlModifier)
    assert called.count('layout') == 1
    previous = called.count('fit')
    press(window, Qt.Key_Home)
    assert called.count('fit') == previous + 1
    press(window, Qt.Key_L, Qt.ControlModifier | Qt.ShiftModifier)
    assert window.logDock.isVisible()


def testPageCopyDeleteUndoNeverModifyHiddenWorkflow(canvas, monkeypatch):  # noqa: F811
    window, _nodes, client = canvas
    flow = deepcopy(window.flowModel.toProjectGraph())
    editor, first = makePage(window, monkeypatch)
    depth = len(window.pageCoordinator.session._undo)
    press(window, Qt.Key_D, Qt.ControlModifier)
    copied = editor.tools.selected
    assert copied != first
    assert len(editor.store.snapshot().pages[editor.pageId].components) == 2
    assert len(window.pageCoordinator.session._undo) == depth + 1
    press(window, Qt.Key_Delete)
    assert len(editor.store.snapshot().pages[editor.pageId].components) == 1
    press(window, Qt.Key_Z, Qt.ControlModifier)
    assert len(editor.store.snapshot().pages[editor.pageId].components) == 2
    assert window.flowModel.toProjectGraph() == flow
    assert client.starts == client.stops == 0


def testPageSaveKeyCommitsPendingFieldAndRejectsInvalidField(canvas, monkeypatch, tmp_path):  # noqa: F811
    window, _nodes, _client = canvas
    editor, first = makePage(window, monkeypatch)
    directory = tmp_path / '页面快捷保存'
    assert window.saveProjectToDirectory(str(directory))
    focusCanvas(window)
    field = editor.tools.fields['text']
    field.setText('Ctrl+S 保存的待提交内容')
    field.setFocus()
    QApplication.processEvents()
    QTest.keyClick(field, Qt.Key_S, Qt.ControlModifier)
    saved = (directory / 'project.json').read_bytes()
    payload = json.loads(saved)
    assert payload['presentation']['pages'][editor.pageId]['components'][0]['props']['text'] == field.text()
    assert not window.pageCoordinator.session.dirty
    editor.tools.fields['fontSize'].setValue(1)
    QTest.keyClick(field, Qt.Key_S, Qt.ControlModifier)
    assert (directory / 'project.json').read_bytes() == saved
    assert editor.tools.fields['fontSize'].value() == 1
    assert editor.tools.propertyError.text()
    editor.tools.fields['fontSize'].setValue(16)


def testPageFlowOnlyKeysAndPreviewKeepTaskOwnership(canvas, monkeypatch):  # noqa: F811
    window, _nodes, client = canvas
    editor, _key = makePage(window, monkeypatch)
    window.loadedProjectPath = 'test-project'
    before = window.pageCoordinator.session.payload()
    for key, modifiers in [(Qt.Key_F5, Qt.NoModifier), (Qt.Key_F6, Qt.NoModifier),
                           (Qt.Key_F2, Qt.NoModifier), (Qt.Key_Return, Qt.ControlModifier),
                           (Qt.Key_L, Qt.ControlModifier)]:
        press(window, key, modifiers)
    assert window.pageCoordinator.session.payload() == before
    press(window, Qt.Key_P, Qt.ControlModifier | Qt.ShiftModifier)
    observer = window.pageCoordinator.preview.observer
    if hasattr(window.pageCoordinator, 'chrome'):
        assert observer.isVisible()
        observer.close()
        waitUntil(lambda: not window.pageCoordinator.preview.busy)
    else:
        # The committed legacy preview needs a previously observed real Job.
        # Its validation must remain visible, never replaced by a fake result.
        assert observer is None
        assert '请先观看当前工程任务' in editor.message.text()
    assert client.starts == client.stops == 0
    assert editor.tools.selected is not None


def testModalAndOtherDesignerWindowDoNotShareShortcuts(canvas, ownedDesignerWindow):  # noqa: F811
    window, (first, _second), _client = canvas
    window.flowScene.setNodeSelected(first)
    before = window.pageCoordinator.session.payload()
    focusCanvas(window)
    dialog = QDialog(window)
    dialog.setModal(True)
    dialog.show()
    QApplication.processEvents()
    QTest.keyClick(dialog, Qt.Key_D, Qt.ControlModifier)
    assert window.pageCoordinator.session.payload() == before
    dialog.close()
    other = ownedDesignerWindow(Client())
    other.operatorCatalogController.refreshOperators(other._classifyOperator)
    other.addNodeFromOperatorPayload(PAYLOAD)
    target = next(key for key, node in other.flowModel.nodes.items() if node.kind == 'operator')
    other.flowScene.setNodeSelected(target)
    count = len(other.flowModel.nodes)
    press(other, Qt.Key_D, Qt.ControlModifier)
    assert len(other.flowModel.nodes) == count + 1
    assert window.pageCoordinator.session.payload() == before


def testClickPageCardTransfersPropertyFocusBeforeCopy(canvas, monkeypatch):  # noqa: F811
    window, _nodes, client = canvas
    editor, key = makePage(window, monkeypatch)
    focusCanvas(window)
    field = editor.tools.fields['text']
    field.setFocus()
    QApplication.processEvents()
    assert window.designerActions.inputFocused()
    card = editor.renderer.widgets[editor.pageId][key][1].parentWidget()
    QTest.mouseClick(card, Qt.LeftButton)
    QApplication.processEvents()
    assert not window.designerActions.inputFocused()
    assert window.designerActions.keyboardAllowed('copy')
    depth = len(window.pageCoordinator.session._undo)
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_D, Qt.ControlModifier)
    QApplication.processEvents()
    assert len(editor.store.snapshot().pages[editor.pageId].components) == 2
    assert len(window.pageCoordinator.session._undo) == depth + 1
    assert client.starts == client.stops == 0


def testPageSidebarAndTableDoNotDeleteCanvasSelection(canvas, monkeypatch):  # noqa: F811
    window, _nodes, _client = canvas
    editor, key = makePage(window, monkeypatch)
    focusCanvas(window)
    table = QTableWidget(1, 1, editor)
    table.setItem(0, 0, QTableWidgetItem('文本值'))
    table.show()
    before = window.pageCoordinator.session.payload()
    for field in (editor.pageList, table):
        field.setFocus()
        QApplication.processEvents()
        for button in (Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Home):
            QTest.keyClick(field, button)
        assert window.pageCoordinator.session.payload() == before
        assert editor.tools.selected == key
    table.close()


def testPendingRefreshIsBoundedAndRetiredWithWindow(canvas):  # noqa: F811
    window, _nodes, client = canvas
    registry = window.designerActions
    focusCanvas(window)
    for _ in range(20):
        registry.scheduleRefresh()
    assert registry.refreshTimer.isSingleShot() and registry.refreshTimer.isActive()
    window.hide()
    assert not registry.refreshTimer.isActive()
    window.close()
    assert window.runtimeController._closed
    registry.scheduleRefresh()
    assert not registry.refreshTimer.isActive()
    for key in SHORTCUTS:
        registry.invoke(key)
    assert client.starts == client.stops == 0


def testCloseInProgressBlocksKeyboardAndRetainedActions(canvas):  # noqa: F811
    window, (first, _second), _client = canvas
    window.flowScene.setNodeSelected(first)
    before = window.pageCoordinator.session.payload()
    window.runtimeController._closing = True
    try:
        press(window, Qt.Key_D, Qt.ControlModifier)
        window.designerActions.invoke('copy')
        assert window.pageCoordinator.session.payload() == before
        assert not window.designerActions.actions['copy'].isEnabled()
    finally:
        window.runtimeController._closing = False
