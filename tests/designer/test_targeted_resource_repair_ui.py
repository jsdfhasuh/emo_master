from PySide2.QtCore import QSettings
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
from tests.runtime.test_targeted_resource_repair import brokenRuntime
from tests.designer.test_device_workspace import waitUntil


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
        assert '修复并校验完成' in dialog.status.text(), dialog.status.text()
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
