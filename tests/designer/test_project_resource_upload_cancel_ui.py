"""Active upload cancellation uses the worker's existing cooperative boundary."""
from copy import deepcopy
from threading import Event

from PySide2.QtCore import QSettings, Qt
from PySide2.QtWidgets import QMessageBox
import pytest

from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
from tests.designer.test_device_workspace import waitUntil
from tests.runtime.test_targeted_resource_repair import (
    normalUploadRuntime, pendingUpload, uploadCandidate,  # noqa: F401
)


@pytest.fixture
def uploadDialog(normalUploadRuntime, ownedDesignerWindow, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr('emo_master.apps.designer.state.workflow_store.utc_now_iso',
                        lambda: '2026-10-19T00:00:00Z')
    runtime, client, project = normalUploadRuntime
    snapshot = client.getOnlineProjectSnapshot()
    candidate = uploadCandidate(snapshot, project.parent, names=('first',))
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
    assert window.loadProjectDirectory(str(project), confirmed=True)
    window.pageCoordinator.sync()
    session = window.pageCoordinator.session
    with session.transaction():
        session.workflows.projectExtensions['resources'] = candidate['resources']
    with session.transaction():
        session.workflows.project['name'] = 'unsaved name'
    assert session.undo() and session.dirty and session._undo and session._redo
    draft, history, disk = session.payload(), deepcopy((session._undo, session._redo)), project.read_bytes()
    dialog = ProjectConfigurationDialog(window.pageCoordinator, window, allowRun=True)
    dialog.show()
    waitUntil(lambda: dialog.pending is None)
    for row in range(dialog.table.rowCount()):
        item = dialog.table.item(row, 0)
        if item.data(Qt.UserRole).startswith('/resources/'):
            item.setCheckState(Qt.Checked)
    selection = [(dialog.table.item(row, 0).data(Qt.UserRole), dialog.table.item(row, 0).checkState())
                 for row in range(dialog.table.rowCount())]
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    try:
        yield dialog, runtime, client, snapshot
        assert dialog.isVisible()
        assert selection == [(dialog.table.item(row, 0).data(Qt.UserRole), dialog.table.item(row, 0).checkState())
                             for row in range(dialog.table.rowCount())]
        assert session.payload() == draft and project.read_bytes() == disk and session.dirty
        assert (session._undo, session._redo) == history
        assert client.getOnlineProjectSnapshot() == snapshot and not runtime.jobRepository.all()
    finally:
        waitUntil(lambda: dialog.pending is None)
        dialog.reject()


@pytest.mark.parametrize('stage,cancel,lostReply', [
    ('LOOKUP', True, False), ('CHUNK', False, False), ('CHUNK', True, False),
    ('COMMIT', True, False), ('COMMIT', True, True),
])
def testNativeActiveUploadCanCancelWithoutClosing(uploadDialog, monkeypatch, stage, cancel, lostReply):
    dialog, runtime, client, _snapshot = uploadDialog
    entered, release = Event(), Event()
    original, calls = client.transferProjectResource, []

    def transfer(snapshot, transferId, action, **kwargs):
        calls.append((transferId, action))
        if action == stage == 'LOOKUP':
            entered.set()
            assert release.wait(5), 'UI did not release LOOKUP'
        result = original(snapshot, transferId, action, **kwargs)
        if action == stage and stage != 'LOOKUP':
            # The final bytes or COMMIT have reached Runtime; hold only its reply.
            entered.set()
            assert release.wait(5), 'UI did not release the upload reply'
            if lostReply:
                raise TimeoutError('commit reply lost after cancellation')
        return result

    monkeypatch.setattr(client, 'transferProjectResource', transfer)
    try:
        dialog.uploadButton.click()
        waitUntil(entered.is_set)
        assert dialog.pending is not None and not dialog.pending.done()
        assert dialog.cancelUploadButton.isEnabled()
        assert not dialog.uploadButton.isEnabled() and not dialog.queryButton.isEnabled()
        assert not dialog.cancelButton.isEnabled() and not dialog.writeButton.isEnabled()
        if stage == 'LOOKUP':
            assert not getattr(client, 'pendingResourceTransfers', {})
        if cancel:
            def unexpectedConfirmation(*args):
                pytest.fail('Stopping the active batch should not open a pending-transfer confirmation')
            monkeypatch.setattr(QMessageBox, 'question', unexpectedConfirmation)
            future = dialog.pending
            dialog.cancelUploadButton.click()
            assert dialog.uploadCancelled.is_set()
            assert dialog.pending is future and dialog.isVisible()
            assert not dialog.cancelUploadButton.isEnabled()
            assert '已请求取消' in dialog.status.text() and '等待' in dialog.status.text()
            assert '原上传已取消' not in dialog.status.text()
            dialog.cancelUploadButton.click()
            assert dialog.pending is future
    finally:
        release.set()
        waitUntil(lambda: dialog.pending is None)

    actions = [action for _, action in calls]
    tid = calls[0][0]
    assert all(transferId == tid for transferId, _ in calls)
    if cancel and stage != 'COMMIT':
        assert actions == ['LOOKUP', 'BEGIN'] + (['CHUNK'] if stage == 'CHUNK' else []) + ['CANCEL']
        assert runtime.projectResourceTransfers._read(tid)[3] == 'CANCELLED'
        assert '完成 0 项，取消 1 项，仍待核对 0 项' in dialog.status.text()
    else:
        assert actions == ['LOOKUP', 'BEGIN', 'CHUNK', 'COMMIT'] + (['STATUS'] if lostReply else [])
        assert '完成 1 项，取消 0 项，仍待核对 0 项' in dialog.status.text()
        assert '原上传已取消' not in dialog.status.text()
    assert not client.pendingResourceTransfers and dialog.uploadButton.isEnabled()
    assert not dialog.cancelUploadButton.isEnabled()
    # A new explicit upload resets the cancellation event and can proceed.
    monkeypatch.setattr(client, 'transferProjectResource', original)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    dialog.uploadButton.click()
    waitUntil(lambda: dialog.pending is None)
    assert not dialog.uploadCancelled.is_set()
    assert '完成 1 项，取消 0 项，仍待核对 0 项' in dialog.status.text()


@pytest.mark.parametrize('action', ['STATUS', 'CANCEL'])
def testNativeQueryAndSettledCancelCannotCancelAnotherWorker(uploadDialog, monkeypatch, action):
    dialog, _runtime, client, snapshot = uploadDialog
    pendingUpload(client, snapshot, 'original-upload')
    dialog._controls()
    entered, release = Event(), Event()
    original, calls = client.transferProjectResource, []

    def transfer(saved, transferId, requestedAction, **kwargs):
        calls.append((saved['runtimeInstanceId'], transferId, requestedAction))
        entered.set()
        assert release.wait(5), 'UI did not release the original request'
        return original(saved, transferId, requestedAction, **kwargs)

    monkeypatch.setattr(client, 'transferProjectResource', transfer)
    try:
        (dialog.queryButton if action == 'STATUS' else dialog.cancelUploadButton).click()
        waitUntil(entered.is_set)
        future = dialog.pending
        assert not dialog.cancelUploadButton.isEnabled() and not dialog.queryButton.isEnabled()
        dialog.cancelUploads()
        assert not dialog.uploadCancelled.is_set() and dialog.pending is future
    finally:
        release.set()
        waitUntil(lambda: dialog.pending is None)
    assert calls == [(snapshot['runtimeInstanceId'], 'original-upload', action)]
    assert dialog.uploadButton.isEnabled() == (action == 'CANCEL')
    assert dialog.cancelUploadButton.isEnabled() == (action == 'STATUS')
    assert dialog.queryButton.isEnabled() == (action == 'STATUS')
    assert ('仍待核对 1 项' if action == 'STATUS' else '取消 1 项，仍待核对 0 项') in dialog.status.text()
