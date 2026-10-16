"""Native QDialog completion routes must retire the pinned debug owner."""
import pytest
import emo_master  # noqa: F401
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QDialog
from shiboken2 import isValid

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from emo_master.apps.designer.ui.workflow_debug_window import WorkflowDebugWindow
from tests.designer.test_operator_debug_window import editorFor, wait
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_workflow_debug_control import nestedProject


@pytest.mark.parametrize('kind', ['operator', 'workflow'])
@pytest.mark.parametrize('route', ['escape', 'reject', 'accept', 'done', 'window_close', 'end'])
def testEveryDialogExitRetiresWorkerBeforeDeletion(runtime, retainedQtApplication, monkeypatch, kind, route):
    editor = None
    if kind == 'operator':
        editor, *_ = editorFor(runtime)
        dialog = OperatorDebugWindow(editor)
        editor._debugWindow = dialog
        ready = dialog.run.isEnabled
    else:
        dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), 'main')
        ready = dialog.startButton.isEnabled
    dialog.show()
    connection = dialog.connection
    calls = []
    original = connection.call
    def observed(method, **fields):
        calls.append(method)
        return original(method, **fields)
    monkeypatch.setattr(connection, 'call', observed)
    second = None
    try:
        wait(ready)
        assert runtime.operatorDebugManager.ownsResources()
        if route == 'escape':
            QTest.keyClick(dialog, Qt.Key_Escape)
        elif route == 'end':
            QTest.mouseClick(dialog.end, Qt.LeftButton)
        elif route == 'done':
            dialog.done(QDialog.Rejected)
        elif route == 'window_close':
            dialog.close()
        else:
            getattr(dialog, route)()
        wait(lambda: not isValid(dialog))
        assert connection.stop.is_set(), 'Window vanished without stopping its lease renewer'
        assert connection.renewer is None or not connection.renewer.is_alive()
        assert not runtime.operatorDebugManager.ownsResources()
        assert calls.count('CloseOperatorDebugSession') == 1
        second = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), 'main')
        second.show()
        wait(lambda: second.startButton.isEnabled())
    finally:
        if second is not None and isValid(second):
            second.close()
            wait(lambda: not isValid(second))
        if isValid(dialog):
            dialog.close()
            wait(lambda: not isValid(dialog))
        elif not connection.stop.is_set():
            connection.close()
        if editor is not None:
            editor.forceClose()
        wait(lambda: not runtime.operatorDebugManager.ownsResources())


@pytest.mark.parametrize('kind', ['operator', 'workflow'])
def testRepeatedEscapeDuringSlowOpenWaitsForOriginalOwner(runtime, retainedQtApplication, monkeypatch, kind):
    import threading
    from emo_master.apps.designer.services.operator_debug import DebugConnection
    from emo_master.apps.designer.services.workflow_debug import WorkflowDebugConnection
    entered, release = threading.Event(), threading.Event()
    cls, method = (DebugConnection, 'open') if kind == 'operator' else (WorkflowDebugConnection, 'openWorkflow')
    original = getattr(cls, method)
    def slowOpen(self, *args, **kwargs):
        entered.set()
        assert release.wait(10), 'Test gate was never released'
        return original(self, *args, **kwargs)
    monkeypatch.setattr(cls, method, slowOpen)
    editor = None
    if kind == 'operator':
        editor, *_ = editorFor(runtime)
        dialog = OperatorDebugWindow(editor)
        editor._debugWindow = dialog
    else:
        dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), 'main')
    dialog.show()
    connection = dialog.connection
    try:
        wait(entered.is_set)
        QTest.keyClick(dialog, Qt.Key_Escape)
        dialog.reject()
        dialog.close()
        assert dialog.isVisible() and dialog.closing
        assert not dialog.disposed
        release.set()
        wait(lambda: not isValid(dialog))
        assert connection.stop.is_set()
        assert not connection.renewer.is_alive()
        assert not runtime.operatorDebugManager.ownsResources()
    finally:
        release.set()
        if isValid(dialog):
            dialog.close()
            wait(lambda: not isValid(dialog))
        if editor is not None:
            editor.forceClose()


@pytest.mark.parametrize('kind', ['operator', 'workflow'])
def testFailedCloseStaysVisibleAndExplicitCloseCanRecover(runtime, retainedQtApplication, monkeypatch, kind):
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError
    editor = None
    if kind == 'operator':
        editor, *_ = editorFor(runtime)
        dialog = OperatorDebugWindow(editor)
        editor._debugWindow = dialog
        ready = dialog.run.isEnabled
    else:
        dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), 'main')
        ready = dialog.startButton.isEnabled
    dialog.show()
    original = dialog.connection.close
    attempts = []
    def failOnce():
        attempts.append('close')
        if len(attempts) == 1:
            raise RuntimeClientError('E_RESOURCE_CLEANUP_FAILED', 'Injected unavailable cleanup acknowledgement')
        return original()
    monkeypatch.setattr(dialog.connection, 'close', failOnce)
    try:
        wait(ready)
        QTest.keyClick(dialog, Qt.Key_Escape)
        wait(lambda: not dialog.closing)
        assert dialog.isVisible() and not dialog.disposed
        assert runtime.operatorDebugManager.ownsResources()
        assert 'E_RESOURCE_CLEANUP_FAILED' in dialog.status.text()
        assert len(attempts) == 1  # no automatic replay
        QTest.mouseClick(dialog.end, Qt.LeftButton)
        wait(lambda: not isValid(dialog))
        assert len(attempts) == 2
        assert not runtime.operatorDebugManager.ownsResources()
    finally:
        if isValid(dialog):
            dialog.close()
            wait(lambda: not isValid(dialog))
        if editor is not None:
            editor.forceClose()


@pytest.mark.parametrize('force', [False, True])
def testParentWaitsForInFlightCleanupFailureAndExplicitRecovery(runtime, retainedQtApplication, monkeypatch, force):
    import threading
    from PySide2.QtCore import QCoreApplication, QEvent
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError
    editor, *_ = editorFor(runtime)
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    entered, release = threading.Event(), threading.Event()
    attempts, closed = [], []
    editor._onClosed = lambda *_: closed.append('closed')
    original = dialog.connection.close
    def close():
        attempts.append('close')
        if len(attempts) == 1:
            entered.set()
            assert release.wait(10), 'Cleanup fault-injection gate was not released'
            raise RuntimeClientError('E_RESOURCE_CLEANUP_FAILED', 'Injected pending close failure')
        return original()
    monkeypatch.setattr(dialog.connection, 'close', close)
    try:
        wait(dialog.run.isEnabled)
        dialog.close()
        wait(entered.is_set)
        editor.forceClose() if force else editor.close()
        assert editor.isVisible() and dialog.isVisible()
        assert not editor._disposed and not dialog.disposed
        assert not editor._applyButton.isEnabled()
        release.set()
        wait(lambda: not dialog.closing and not editor._waitingForDebug)
        assert editor._applyButton.isEnabled() and editor._closeButton.isEnabled()
        assert editor.isVisible() and dialog.isVisible()
        assert 'E_RESOURCE_CLEANUP_FAILED' in dialog.status.text()
        assert runtime.operatorDebugManager.ownsResources()
        assert attempts == ['close'] and not closed
        assert not editor._forceClosing
        # After a failed forced close, a normal close must again ask about edits.
        from PySide2.QtWidgets import QMessageBox
        monkeypatch.setattr(editor, 'isDirty', lambda: True)
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Cancel)
        editor.close()
        assert editor.isVisible() and dialog.isVisible()
        assert attempts == ['close'] and not editor._waitingForDebug
        monkeypatch.setattr(editor, 'isDirty', lambda: False)
        # A fresh, explicit parent-close action requests cleanup once.
        editor.close()
        def retired():
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            return not isValid(dialog) and not isValid(editor)
        wait(retired)
        assert attempts == ['close', 'close'] and closed == ['closed']
        assert not runtime.operatorDebugManager.ownsResources()
    finally:
        release.set()
        if isValid(dialog):
            dialog.close()
            wait(lambda: not isValid(dialog))
        if isValid(editor):
            editor.forceClose()
