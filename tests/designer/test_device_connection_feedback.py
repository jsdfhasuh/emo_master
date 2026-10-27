"""Connection-test feedback uses simulated requests, never real devices."""
from concurrent.futures import Future
from copy import deepcopy
import os
from types import SimpleNamespace

import pytest
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QApplication, QLabel, QMessageBox, QTextEdit

from emo_master.apps.designer.ui.device_wizard import DeviceDefinitionDialog
from emo_master.core.contracts.device_driver import DeviceError
from tests.core.project.test_devices import declaration, definition  # noqa: F401
from tests.designer.test_device_workspace_usability import workspace


@pytest.fixture(params=['workspace', 'wizard'])
def editor(request, ownedDesignerWindow, tmp_path, definition, declaration, monkeypatch):  # noqa: F811
    client = SimpleNamespace(deviceNetworkCommand=lambda *_args, **_kwargs: None)
    if request.param == 'workspace':
        dialog = workspace(ownedDesignerWindow, tmp_path, definition, declaration)
        dialog.c.window.runtimeClient = client
        dialog._showDetails()
        result = SimpleNamespace(dialog=dialog, button=dialog.buttons['test'], status=dialog.status,
            pool=dialog.reader, ready=dialog._metadataReady, pending=lambda: dialog.pending,
            payload=dialog.c.session.payload, candidate=dialog._formValue)
    else:
        dialog = DeviceDefinitionDialog(declaration, initial=definition.model_dump(), client=client)
        dialog.show()
        result = SimpleNamespace(dialog=dialog, button=dialog.testButton, status=dialog.error,
            pool=dialog.setupPool, ready=dialog.setupReady, pending=lambda: dialog.setupPending,
            payload=lambda: dialog.value, candidate=dialog._testCandidate)
    future = Future()
    result.future = future
    monkeypatch.setattr(result.pool, 'submit', lambda *_args, **_kwargs: result.future)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    yield result
    if not result.future.done():
        result.future.set_result({'connected': True})
    if result.pending() is not None:
        result.ready()
    for notice in dialog.findChildren(QMessageBox):
        notice.close()
    if request.param == 'workspace':
        dialog.revert()
    dialog.close()


@pytest.mark.parametrize('failed', [False, True])
def testConnectionCompletionShowsVisibleResultAndKeepsDraft(editor, failed):
    before = deepcopy(editor.payload())
    candidate = deepcopy(editor.candidate())
    editor.status.setToolTip('OLD-ERROR')
    editor.button.click()
    if failed:
        editor.future.set_exception(DeviceError('E_CAMERA_CONNECT_TIMEOUT', 'simulated RTSP timeout'))
    else:
        editor.future.set_result({'connected': True})
    editor.ready()
    QApplication.processEvents()
    box = editor.dialog.findChild(QMessageBox, 'deviceConnectionTestResult')
    assert box is not None
    assert box.textFormat() == Qt.PlainText
    assert box.text() == ('连接测试失败' if failed else '连接测试通过')
    assert box.icon() == (QMessageBox.Warning if failed else QMessageBox.Information)
    assert editor.dialog.name.text() in box.informativeText()
    assert '未保存或发布配置' in box.informativeText()
    assert 'OLD-ERROR' not in editor.status.toolTip()
    if failed:
        assert 'RTSP 连接超时' in box.informativeText()
        assert 'E_CAMERA_CONNECT_TIMEOUT' in box.detailedText()
        assert 'simulated RTSP timeout' in box.detailedText()
    else:
        assert '未验证采集或业务读写' in box.informativeText()
    assert editor.payload() == before and editor.candidate() == candidate
    assert editor.pending() is None and editor.button.isEnabled()
    assert editor.button.text() == '测试连接'


@pytest.mark.skipif(os.name == 'nt' and os.environ.get('QT_QPA_PLATFORM') != 'windows',
                   reason='QMessageBox rendering requires native Windows Qt; offscreen crashes')
@pytest.mark.parametrize('failed', [False, True])
def testConnectionResultNativeLayout(editor, failed, tmp_path):
    editor.button.click()
    if failed:
        editor.future.set_exception(DeviceError('E_CAMERA_CONNECT_TIMEOUT', 'simulated RTSP timeout'))
    else:
        editor.future.set_result({'connected': True})
    editor.ready()
    QApplication.processEvents()
    box = editor.dialog.findChild(QMessageBox, 'deviceConnectionTestResult')
    assert box is not None and box.isVisible()
    for name in ('qt_msgbox_label', 'qt_msgbox_informativelabel'):
        label = box.findChild(QLabel, name)
        assert label is not None and label.isVisible()
        bounds = label.fontMetrics().boundingRect(label.contentsRect(), Qt.TextWordWrap, label.text())
        assert bounds.height() <= label.contentsRect().height() + 2
        assert label.visibleRegion().boundingRect() == label.rect()
    for button in box.buttons():
        assert button.fontMetrics().horizontalAdvance(button.text()) <= button.contentsRect().width()
    if failed:
        details = next(button for button in box.buttons() if button.text() == '详细信息')
        details.click()
        QApplication.processEvents()
        assert box.findChild(QTextEdit).isVisible()
        assert details.text() == '收起详情'
        details.click()
        QApplication.processEvents()
        assert not box.findChild(QTextEdit).isVisible()
    assert box.grab().save(str(tmp_path / 'connection-result.png'))


def testConnectionInProgressIsVisibleAndCannotBeSubmittedTwice(editor):
    editor.status.setToolTip('OLD-ERROR')
    editor.button.click()
    assert editor.pending() is editor.future
    assert '正在测试' in editor.status.text()
    assert editor.button.text() == '测试中…'
    assert not editor.button.isEnabled()
    assert not editor.status.toolTip()
    editor.button.click()
    assert editor.pending() is editor.future
    editor.future.set_result({'connected': True})
    editor.ready()
    assert editor.button.isEnabled() and editor.button.text() == '测试连接'


@pytest.mark.parametrize('failed', [False, True])
def testChangedCandidateDoesNotShowOldConnectionResult(editor, failed):
    editor.button.click()
    editor.dialog.name.setText('Changed candidate')
    if failed:
        editor.future.set_exception(DeviceError('E_CAMERA_CONNECT', 'OLD-RESULT'))
    else:
        editor.future.set_result({'connected': True, 'message': 'OLD-RESULT'})
    editor.ready()
    assert editor.dialog.findChild(QMessageBox, 'deviceConnectionTestResult') is None
    assert '输入已变化' in editor.status.text()
    assert 'OLD-RESULT' not in editor.status.text() + editor.status.toolTip()
    assert editor.button.isEnabled() and editor.button.text() == '测试连接'


def testChangedRuntimeDoesNotShowOldConnectionResult(editor, monkeypatch):
    editor.button.click()
    scope = '_scope' if hasattr(editor.dialog, 'c') else '_setupScope'
    monkeypatch.setattr(editor.dialog, scope, lambda: ('changed',))
    editor.future.set_result({'connected': True, 'message': 'OLD-RESULT'})
    editor.ready()
    assert editor.dialog.findChild(QMessageBox, 'deviceConnectionTestResult') is None
    assert editor.status.text() == '工程或 Runtime 连接已变化，请重新测试'
    assert 'OLD-RESULT' not in editor.status.text() + editor.status.toolTip()
    assert editor.button.isEnabled() and editor.button.text() == '测试连接'


def testRetryClearsPreviousFailureDetails(editor):
    editor.button.click()
    editor.future.set_exception(DeviceError('E_CAMERA_CONNECT', 'FIRST-FAILURE'))
    editor.ready()
    box = editor.dialog.findChild(QMessageBox, 'deviceConnectionTestResult')
    assert box is not None and 'FIRST-FAILURE' in box.detailedText()
    box.close()
    editor.future = Future()
    editor.button.click()
    assert not editor.status.toolTip()
    editor.future.set_result({'connected': True})
    editor.ready()
    boxes = editor.dialog.findChildren(QMessageBox, 'deviceConnectionTestResult')
    assert any(box.text() == '连接测试通过' and not box.detailedText() for box in boxes)
    assert not editor.status.toolTip()


def testCancelledConfirmationDoesNotStartTestOrShowResult(editor, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.No)
    editor.button.click()
    assert editor.pending() is None
    assert editor.dialog.findChild(QMessageBox, 'deviceConnectionTestResult') is None
    assert editor.button.isEnabled() and editor.button.text() == '测试连接'


def testBusyWorkspaceClickExplainsWhyTestDidNotStart(ownedDesignerWindow, tmp_path,
        definition, declaration, monkeypatch):  # noqa: F811
    dialog = workspace(ownedDesignerWindow, tmp_path, definition, declaration)
    pending = Future()
    dialog.pending, dialog.pendingKind = pending, 'state'
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: pytest.fail('busy request must not confirm'))
    try:
        dialog.testConnection()
        assert '请稍后重试' in dialog.status.text()
        assert dialog.pending is pending and dialog.pendingTest is None
    finally:
        dialog.pending = None


def testHiddenWorkspaceDoesNotReopenForConnectionResult(ownedDesignerWindow, tmp_path,
        definition, declaration, monkeypatch):  # noqa: F811
    dialog = workspace(ownedDesignerWindow, tmp_path, definition, declaration)
    future = Future()
    monkeypatch.setattr(dialog.reader, 'submit', lambda *_args, **_kwargs: future)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    dialog.testConnection()
    dialog.closeButton.click()
    future.set_result({'connected': True})
    dialog._metadataReady()
    assert not dialog.isVisible()
    assert dialog.findChild(QMessageBox, 'deviceConnectionTestResult') is None
    assert '通过' in dialog.status.text()
