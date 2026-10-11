"""Main-window boundaries retain debug owners until confirmed retirement."""
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QCoreApplication, QEvent, Qt
from PySide2.QtWidgets import QDialog
from shiboken2 import isValid

from tests.designer.test_operator_debug_window import wait
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from emo_master.apps.designer.operator_editors import EditorKey


class PendingDebug(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.disposed = False
        self.closing = False
        self.attempts = 0
        self.show()

    def closeEvent(self, event):
        if self.disposed:
            event.accept()
            return
        event.ignore()
        if not self.closing:
            self.closing = True
            self.attempts += 1

    def fail(self):
        self.closing = False

    def retire(self):
        self.disposed = True
        self.close()
        QCoreApplication.sendPostedEvents(self, QEvent.DeferredDelete)


def addOperator(window, debug):
    window.operatorEditorManager._windows[EditorKey('detached-test', 'main', 'node')] = SimpleNamespace(
        _debugWindow=debug, context=SimpleNamespace(operatorId='vision.value.number',
            invalidatePreviewSources=lambda: None), forceClose=lambda: None)


@pytest.mark.parametrize('kind', ['operator', 'workflow'])
@pytest.mark.parametrize('entry', ['directory', 'runtime', 'startup'])
def testProjectReplacementRefusesLiveDebugBeforeMutations(ownedDesignerWindow, monkeypatch, kind, entry):
    client = SimpleNamespace(listOperators=lambda: [])
    window = ownedDesignerWindow(client)
    debug = PendingDebug(window)
    if kind == 'operator':
        addOperator(window, debug)
    else:
        window._workflowDebugWindow = debug
    called = []
    monkeypatch.setattr(window.operatorEditorManager, 'resolveSqlitePending', lambda *_: called.append('dirty'))
    monkeypatch.setattr(window.projectController, 'loadProjectDirectory', lambda *_args, **_kw: called.append('load'))
    monkeypatch.setattr(window.projectController, 'handleStartupProjectEntry', lambda *_: called.append('startup'))
    client.loadProject = lambda *_: called.append('runtime')
    before = window.loadedProjectPath
    try:
        if entry == 'directory':
            result = window.loadProjectDirectory('/not-opened.emoproj', confirmed=True)
        elif entry == 'runtime':
            result = window.loadProjectFromPath('/not-opened.emoproj', 'loaded', 'failed')
        else:
            result = window.showStartupProjectEntry()
        assert result is False and not called
        assert window.loadedProjectPath == before
        assert debug.attempts == 0 and isValid(debug)
    finally:
        debug.retire()
        window.operatorEditorManager._windows.clear()
        monkeypatch.undo()


def testMainCloseWaitsForAllDebugOwnersWithoutSiblingFailureReplay(ownedDesignerWindow):
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: []))
    window.show()
    operator = PendingDebug(window)
    workflow = PendingDebug(window)
    addOperator(window, operator)
    window._workflowDebugWindow = workflow
    try:
        assert not window.close()
        assert window.isVisible() and not window.runtimeController._closed
        assert operator.attempts == workflow.attempts == 1
        operator.fail()
        workflow.retire()
        # A successful sibling's queued callback must not re-close the failed owner.
        for _ in range(3):
            QCoreApplication.processEvents()
        assert operator.attempts == 1
        assert window.isVisible() and not window.runtimeController._closed
        assert not window.close()  # explicit retry
        assert operator.attempts == 2
        operator.retire()
        wait(lambda: window.runtimeController._closed)
        assert not window.isVisible()
    finally:
        for debug in (operator, workflow):
            if isValid(debug):
                debug.retire()
        window.operatorEditorManager._windows.clear()


def testNoProjectNoDebugStillAllowsNormalClose(ownedDesignerWindow):
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: []))
    assert window.loadedProjectPath is None
    assert window._allowProjectReplacement()
    window.show()
    assert window.close()
    assert window.runtimeController._closed


def testMainCloseRetainsRealRuntimeUntilExplicitCleanupRecovery(runtime, ownedDesignerWindow, monkeypatch):
    import threading
    from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
    from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
    from tests.designer.test_operator_debug_window import editorFor
    window = ownedDesignerWindow(RuntimeClient(runtime))
    editor, *_ = editorFor(runtime)
    editor.setParent(window, Qt.Dialog)
    window.operatorEditorManager._windows[editor.key] = editor
    editor._onClosed = window.operatorEditorManager._onWindowClosed
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    window.show()
    editor.show()
    dialog.show()
    entered, release = threading.Event(), threading.Event()
    attempts = []
    original = dialog.connection.close
    def close():
        attempts.append('close')
        if len(attempts) == 1:
            entered.set()
            assert release.wait(10)
            raise RuntimeClientError('E_RESOURCE_CLEANUP_FAILED', 'Injected close failure')
        return original()
    monkeypatch.setattr(dialog.connection, 'close', close)
    try:
        wait(dialog.run.isEnabled)
        assert not window.close()
        wait(entered.is_set)
        assert window.isVisible() and editor.isVisible() and dialog.isVisible()
        assert not window.runtimeController._closed
        release.set()
        wait(lambda: not dialog.closing)
        assert runtime.operatorDebugManager.ownsResources()
        assert window.isVisible() and not window.runtimeController._closed
        assert attempts == ['close']
        assert not window.close()
        wait(lambda: window.runtimeController._closed)
        assert attempts == ['close', 'close']
        assert not runtime.operatorDebugManager.ownsResources()
        assert not window.isVisible()
    finally:
        release.set()
        if isValid(dialog):
            dialog.close()
            wait(lambda: not isValid(dialog))
        if isValid(editor):
            editor.forceClose()
