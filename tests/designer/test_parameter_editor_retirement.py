"""Every parameter-editor dismissal preserves the dirty-edit contract."""
import json

import pytest
import emo_master  # noqa: F401
from PySide2.QtCore import QCoreApplication, QEvent, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QDialog, QLineEdit, QMessageBox
from shiboken2 import isValid

from emo_master.apps.designer.operator_editors import OperatorEditorManager
from tests.designer.test_operator_debug_window import wait
from tests.designer.test_operator_editor_manager import _Settings, _Runtime


@pytest.mark.parametrize('route', ['escape', 'reject', 'accept', 'done'])
@pytest.mark.parametrize('answer', [QMessageBox.Cancel, QMessageBox.Discard, QMessageBox.Save])
def testCompletionRoutesRespectDirtyPromptAndUnregister(tmp_path, monkeypatch, route, answer):
    model = {'value': 7}
    project = tmp_path / 'original.emoproj'
    project.write_text(json.dumps(model))
    original = project.read_bytes()
    applied, questions, closed = [], [], []
    def apply(_key, params):
        applied.append(dict(params))
        model.update(params)
        return True
    manager = OperatorEditorManager(runtimeClient=_Runtime(), settingsStore=_Settings(),
        applyParams=apply, appendLog=lambda *_: None, cacheRoot=tmp_path/'cache')
    args = dict(projectId='project', workflowId='main', nodeId='number',
        operatorId='vision.value.number', displayName='数值',
        schema={'type': 'object', 'properties': {'value': {'type': 'number'}}})
    dialog = manager.open(**args, values=dict(model))
    notify = dialog._onClosed
    def onClosed(key, window):
        closed.append(key)
        notify(key, window)
    dialog._onClosed = onClosed
    def question(*args, **kwargs):
        questions.append(args[1])
        return answer
    monkeypatch.setattr(QMessageBox, 'question', question)
    field = dialog._schemaForm.findChild(QLineEdit)
    field.setFocus()
    QTest.keyClick(field, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(field, '99')
    QTest.keyClick(field, Qt.Key_Tab)
    assert dialog.isDirty() and dialog.collectParams() == {'value': 99}
    try:
        if route == 'escape':
            QTest.keyClick(dialog, Qt.Key_Escape)
        elif route == 'done':
            dialog.done(QDialog.Rejected)
        else:
            getattr(dialog, route)()
        assert questions == ['未保存的参数']
        if answer == QMessageBox.Cancel:
            assert isValid(dialog) and dialog.isVisible() and not dialog._disposed
            assert dialog.collectParams() == {'value': 99}
            assert model == {'value': 7} and manager.count() == 1 and not closed
            assert manager.open(**args, values=dict(model)) is dialog
            dialog.forceClose()
        def deleted():
            # This editor calls QDialog.closeEvent, which posts DeferredDelete.
            # processEvents alone does not deliver it without app.exec().
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            return not isValid(dialog)
        wait(deleted)
        assert manager.count() == 0 and len(closed) == 1
        assert model == {'value': 99 if answer == QMessageBox.Save else 7}
        assert applied == ([{'value': 99}] if answer == QMessageBox.Save else [])
        assert project.read_bytes() == original  # Apply is not implicit Save.
        reopened = manager.open(**args, values=dict(model))
        assert isValid(reopened) and reopened is not dialog
        assert reopened.collectParams() == model
    finally:
        manager.closeAll()
