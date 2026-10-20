from PySide2.QtCore import QSettings
from PySide2.QtWidgets import QMessageBox
from copy import deepcopy
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
from tests.runtime.test_targeted_resource_repair import brokenRuntime, pendingUpload
from tests.designer.test_device_workspace import waitUntil


def testNativeRealUploadFailureReportsProgressAndContinues(tmp_path, ownedDesignerWindow, monkeypatch):
    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import QApplication
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.runtime.test_review_resource_lifecycle import resourceProject
    from tests.runtime.test_targeted_resource_repair import uploadCandidate
    monkeypatch.setattr('emo_master.apps.designer.state.workflow_store.utc_now_iso', lambda: '2026-10-19T00:00:00Z')
    project, _source = resourceProject(tmp_path / 'source')
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    dialog = None
    try:
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None).ok
        client = RuntimeClient(runtimeService=runtime)
        snapshot = client.getOnlineProjectSnapshot()
        candidate = uploadCandidate(snapshot, project.parent, size=600 * 1024)
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
        original, calls, ids = client.transferProjectResource, [], {}
        offline = False
        def transfer(snap, tid, action, **kwargs):
            nonlocal offline
            calls.append((snap, tid, action))
            if action == 'LOOKUP':
                ids[kwargs['resource']['path']] = tid
            if tid == ids.get('second.bin') and action == 'CHUNK' and kwargs['offset']:
                offline = True
            if offline:
                raise TimeoutError('upload connection lost')
            return original(snap, tid, action, **kwargs)
        monkeypatch.setattr(client, 'transferProjectResource', transfer)
        monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
        dialog.uploadButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert '完成 1 项，取消 0 项，仍待核对 1 项' in dialog.status.text(), dialog.status.text()
        assert 'second CHUNK' in dialog.status.text() and 'upload connection lost' in dialog.status.text()
        assert '本批已中止' in dialog.status.text() and 'third.bin' not in ids
        tid = ids['second.bin']
        assert tid in client.pendingResourceTransfers and not dialog.uploadButton.isEnabled()
        assert dialog.queryButton.isEnabled() and dialog.cancelUploadButton.isEnabled()
        QApplication.processEvents()
        assert dialog.status.height() >= dialog.status.heightForWidth(dialog.status.width())
        assert dialog.grab().save(str(tmp_path / 'upload-partial-failure.png'))
        print('F02_P2_SCREENSHOT=' + str(tmp_path / 'upload-partial-failure.png'))
        offline = False
        dialog.queryButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert '仍待核对 1 项' in dialog.status.text()
        dialog.cancelUploadButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert '取消 1 项，仍待核对 0 项' in dialog.status.text()
        assert [a for _, t, a in calls if t == tid] == ['LOOKUP', 'BEGIN', 'CHUNK', 'CHUNK', 'CANCEL', 'STATUS', 'CANCEL']
        assert not client.pendingResourceTransfers and dialog.uploadButton.isEnabled()
        # The next explicit upload reuses first; only the remaining files send bytes.
        calls.clear()
        monkeypatch.setattr(client, 'transferProjectResource', original)
        def retry(snap, transferId, action, **kwargs):
            calls.append((snap, transferId, action))
            return original(snap, transferId, action, **kwargs)
        monkeypatch.setattr(client, 'transferProjectResource', retry)
        dialog.uploadButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert '完成 3 项，取消 0 项，仍待核对 0 项' in dialog.status.text(), dialog.status.text()
        assert [a for _, _, a in calls].count('BEGIN') == 2
        assert [a for _, _, a in calls].count('CHUNK') == 4
        assert client.getOnlineProjectSnapshot() == snapshot and not runtime.jobRepository.all()
        assert session.payload() == draft and project.read_bytes() == disk and session.dirty
        assert (session._undo, session._redo) == history
        for button in [dialog.uploadButton, dialog.cancelUploadButton, dialog.queryButton, dialog.closeButton]:
            assert dialog.rect().contains(button.geometry())
    finally:
        if dialog is not None:
            waitUntil(lambda: dialog.pending is None)
            dialog.reject()
        runtime.close()


def testNativeRepairConfirmationAndRecoveryKeepDraftAndDisk(tmp_path, ownedDesignerWindow, monkeypatch):
    monkeypatch.setattr('emo_master.apps.designer.state.workflow_store.utc_now_iso', lambda: '2026-10-18T00:00:00Z')
    runtime, project, source, old = brokenRuntime(tmp_path)
    dialog = None
    try:
        client = RuntimeClient(runtimeService=runtime)
        window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
        assert window.loadProjectDirectory(str(project), confirmed=True)
        window.pageCoordinator.sync()
        session = window.pageCoordinator.session
        draft, disk = session.payload(), project.read_bytes()
        dialog = ProjectConfigurationDialog(window.pageCoordinator, window, allowRun=True)
        dialog.show()
        waitUntil(lambda: dialog.pending is None)
        assert dialog.recoverButton.isEnabled() and not dialog.runButton.isEnabled()
        monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.No)
        dialog.uploadButton.click()
        assert old.read_bytes() == b'corrupt' and runtime.onlineRecoveryError
        monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
        dialog.uploadButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert '完成 1 项，取消 0 项，仍待核对 0 项' in dialog.status.text(), dialog.status.text()
        assert runtime.onlineRecoveryError and not dialog.runButton.isEnabled()
        dialog.recoverButton.click()
        waitUntil(lambda: dialog.pending is None and not dialog.writePending)
        assert not runtime.onlineRecoveryError, dialog.status.text()
        assert dialog.runButton.isEnabled() and not dialog.recoverButton.isEnabled()
        assert not runtime.jobRepository.all()
        assert session.payload() == draft and project.read_bytes() == disk
        assert dialog.grab().save(str(tmp_path / 'repair-native.png'))
        for button in [dialog.uploadButton, dialog.recoverButton, dialog.queryButton, dialog.closeButton]:
            assert dialog.rect().contains(button.geometry())
    finally:
        if dialog is not None:
            waitUntil(lambda: dialog.pending is None)
            dialog.reject()
        runtime.close()


@pytest.mark.parametrize('mode', ['server-cancelled', 'pending-cancel', 'lost-cancel'])
def testNativeUploadQueryCancelAndContinuePreserveProject(tmp_path, ownedDesignerWindow, monkeypatch, mode):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.runtime.test_review_resource_lifecycle import resourceProject
    monkeypatch.setattr('emo_master.apps.designer.state.workflow_store.utc_now_iso', lambda: '2026-10-19T00:00:00Z')
    project, _source = resourceProject(tmp_path / 'source')
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    dialog = None
    try:
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None).ok
        client = RuntimeClient(runtimeService=runtime)
        window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
        assert window.loadProjectDirectory(str(project), confirmed=True)
        window.pageCoordinator.sync()
        session = window.pageCoordinator.session
        draft, disk = session.payload(), project.read_bytes()
        history = deepcopy((session._undo, session._redo))
        snapshot = client.getOnlineProjectSnapshot()
        pendingUpload(client, snapshot, 'original-upload', 'CANCELLED' if mode == 'server-cancelled' else 'PENDING')
        dialog = ProjectConfigurationDialog(window.pageCoordinator, window, allowRun=True)
        dialog.show()
        waitUntil(lambda: dialog.pending is None)
        assert not dialog.uploadButton.isEnabled()
        assert dialog.queryButton.isEnabled() and dialog.cancelUploadButton.isEnabled()
        assert not dialog.cancelButton.isEnabled() and not dialog.recoverButton.isEnabled()
        original, calls = client.transferProjectResource, []
        failCancel = mode == 'lost-cancel'
        def transfer(snap, transferId, action, **kwargs):
            nonlocal failCancel
            calls.append((snap['runtimeInstanceId'], transferId, action))
            result = original(snap, transferId, action, **kwargs)
            if action == 'CANCEL' and failCancel:
                failCancel = False
                raise TimeoutError('cancel reply unavailable')
            return result
        monkeypatch.setattr(client, 'transferProjectResource', transfer)
        dialog.queryButton.click()
        waitUntil(lambda: dialog.pending is None)
        if mode != 'server-cancelled':
            assert '仍待核对 1 项' in dialog.status.text() and 'original-upload' in dialog.status.text()
            monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.No)
            dialog.cancelUploadButton.click()
            assert len(calls) == 1 and client.pendingResourceTransfers
            monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
            dialog.cancelUploadButton.click()
            waitUntil(lambda: dialog.pending is None)
            if mode == 'lost-cancel':
                assert 'cancel reply unavailable' in dialog.status.text()
                assert client.pendingResourceTransfers and dialog.queryButton.isEnabled()
                assert not dialog.uploadButton.isEnabled()
                dialog.queryButton.click()
                waitUntil(lambda: dialog.pending is None)
        assert '完成 0 项，取消 1 项，仍待核对 0 项' in dialog.status.text()
        assert not client.pendingResourceTransfers and dialog.uploadButton.isEnabled()
        assert not dialog.cancelUploadButton.isEnabled() and not dialog.recoverButton.isEnabled()
        assert all(identity == snapshot['runtimeInstanceId'] and tid == 'original-upload' for identity, tid, _ in calls)
        assert [action for _, _, action in calls] == {
            'server-cancelled': ['STATUS'], 'pending-cancel': ['STATUS', 'CANCEL'],
            'lost-cancel': ['STATUS', 'CANCEL', 'STATUS']}[mode]
        monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
        dialog.uploadButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert '完成 1 项，取消 0 项，仍待核对 0 项' in dialog.status.text()
        assert calls[-1][2] == 'LOOKUP' and not any(action in {'BEGIN', 'COMMIT'} for _, _, action in calls)
        assert client.getOnlineProjectSnapshot() == snapshot
        assert session.payload() == draft and project.read_bytes() == disk
        assert (session._undo, session._redo) == history
        assert not runtime.jobRepository.all()
        for button in [dialog.uploadButton, dialog.cancelUploadButton, dialog.queryButton, dialog.cancelButton]:
            assert dialog.rect().contains(button.geometry())
        assert dialog.grab().save(str(tmp_path / 'upload-normal-operation.png'))
    finally:
        if dialog is not None:
            waitUntil(lambda: dialog.pending is None)
            dialog.reject()
        runtime.close()
