"""Native Qt first-import actions using a real gRPC test Runtime."""
from pathlib import Path

from PySide2.QtWidgets import QMessageBox

from tests.designer.runtime_feedback_fixture import messages  # noqa: F401
from tests.designer.test_device_workspace import waitUntil
from tests.designer.test_runtime_menu_publication import menuDialog
from tests.runtime.test_remote_project_import import candidate, remoteImport  # noqa: F401


def testFirstRemoteImportNeedsExplicitUploadAndCommit(
        ownedDesignerWindow, remoteImport, tmp_path, monkeypatch, messages):  # noqa: F811
    service, client = remoteImport
    path, _ = candidate(tmp_path)
    disk = path.read_bytes()
    window = ownedDesignerWindow(client)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    draft = window.pageCoordinator.session.payload()
    dialog = menuDialog(window, monkeypatch)
    try:
        assert dialog.initial and dialog.uploadButton.isEnabled()
        dialog.validateButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert not dialog.initialValidated and dialog.initialImportId
        assert dialog.cancelButton.isEnabled() and dialog.queryButton.isEnabled()
        assert service.projectConfiguration.current is None and not service.jobRepository.all()
        assert not list(service.projectResourceTransfers.root.glob('*.png'))
        assert messages and 'Runtime 缺少' in messages[-1]['text']
        assert '上传候选资源' in messages[-1]['text']
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.No)
        dialog.uploadButton.click()
        assert dialog.pending is None and not list(service.projectResourceTransfers.root.glob('*.png'))
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
        dialog.uploadButton.click()
        waitUntil(lambda: dialog.pending is None)
        importId = dialog.initialImportId
        assert importId and not dialog.initialValidated and service.projectConfiguration.current is None
        dialog.validateButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert dialog.initialValidated and dialog.initialImportId == importId
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.No)
        dialog.writeButton.click()
        assert service.projectConfiguration.current is None
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
        dialog.writeButton.click()
        waitUntil(lambda: dialog.pending is None and not dialog.writePending and not dialog.initial)
        assert dialog.requestId == importId
        assert Path(service.loadedProjectFile).is_relative_to(service.workspaceRoot.parent / 'online-projects')
        assert client.getProjectWriteStatus(importId)['state'] == 'COMMITTED'
        assert not service.jobRepository.all()
        assert path.read_bytes() == disk and window.pageCoordinator.session.payload() == draft
    finally:
        dialog.reject()


def testInitialCandidateHasNormalCancelExitBeforeUpload(
        ownedDesignerWindow, remoteImport, tmp_path, monkeypatch, messages):  # noqa: F811
    service, client = remoteImport
    path, _ = candidate(tmp_path)
    window = ownedDesignerWindow(client)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    dialog = menuDialog(window, monkeypatch)
    try:
        dialog.validateButton.click()
        waitUntil(lambda: dialog.pending is None)
        requestId = dialog.requestId
        assert requestId and dialog.cancelButton.isEnabled()
        dialog.cancelButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert client.getProjectWriteStatus(requestId)['state'] == 'CANCELLED'
        assert not dialog.initialImportId and not dialog.initialValidated
        assert dialog.uploadButton.isEnabled() and dialog.validateButton.isEnabled()
        assert not service.loadedDocument and not service.jobRepository.all()
    finally:
        dialog.reject()
