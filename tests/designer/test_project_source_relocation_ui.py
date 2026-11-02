"""The explicit Designer write can relocate a project without changing its JSON."""
from pathlib import Path
import os

from PySide2.QtCore import QSettings
from PySide2.QtWidgets import QApplication, QDialog, QMessageBox

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.models import ProjectDocument
from tests.designer.test_device_workspace import waitUntil
from tests.runtime.test_project_source_relocation import relocatedProjects, loadOnline, runImage, DB


def testNativeCompareCancelRelocateAndRunAtTheNewDirectory(ownedDesignerWindow, tmp_path, monkeypatch):
    first, second, document = relocatedProjects(tmp_path)
    runtime = RuntimeService(dbPath=tmp_path / 'runtime/runtime.db', workspaceRoot=tmp_path / 'runtime/jobs')
    client = RuntimeClient(runtime)
    try:
        with ownedDesignerWindow as windows:
            window = windows(client, settingsStore=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
            assert window.loadProjectSelection(str(second))
            waitUntil(lambda: window.operatorCatalogController.state == 'ready')
            window.pageCoordinator.sync()
            before = window.pageCoordinator.session.payload()
            snapshot = loadOnline(client, runtime, first, ProjectDocument.model_validate(before))
            disk = second.read_bytes()
            # Opening or pressing Run cannot silently relocate or start the old directory.
            configured = []
            with monkeypatch.context() as patch:
                patch.setattr(window.pageCoordinator, 'configureRuntime', lambda **kw: configured.append(kw) or QDialog.Rejected)
                assert not window._syncRuntimeProjectBeforeRun()
            assert configured == [{'allowRun': True}]
            assert client.getOnlineProjectSnapshot() == snapshot and not runtime.jobRepository.all()
            dialog = ProjectConfigurationDialog(window.pageCoordinator, window, allowRun=True)
            try:
                waitUntil(lambda: dialog.pending is None)
                assert dialog.table.rowCount() == 0
                assert str(first) in dialog.sourceLocation.text() and str(second) in dialog.sourceLocation.text()
                assert dialog.useLocalSource.isChecked() and dialog.writeButton.isEnabled()
                assert not dialog.runButton.isEnabled()
                dialog.show()
                QApplication.processEvents()
                assert dialog.rect().contains(dialog.closeButton.geometry())
                assert dialog.useLocalSource.geometry().bottom() < dialog.table.geometry().top()
                output = os.getenv('EMO_SOURCE_SCREEN_DIR')
                if output:
                    directory = Path(output)
                    directory.mkdir(parents=True, exist_ok=True)
                    assert dialog.grab().save(str(directory / 'compare-directory.png'))
                # The user can explicitly keep and run the online version instead.
                dialog.useLocalSource.click()
                assert not dialog.writeButton.isEnabled() and dialog.runButton.isEnabled()
                dialog.useLocalSource.click()
                confirmations = []
                def cancel(_self, title, message, *_args):
                    confirmations.append(message)
                    return QMessageBox.No
                monkeypatch.setattr(QMessageBox, 'question', cancel)
                dialog.writeButton.click()
                waitUntil(lambda: dialog.pending is None and bool(confirmations))
                assert str(first) in confirmations[-1] and str(second) in confirmations[-1]
                assert client.getOnlineProjectSnapshot() == snapshot
                assert second.read_bytes() == disk and window.pageCoordinator.session.payload() == before
                monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.Yes)
                dialog.writeButton.click()
                waitUntil(lambda: dialog.pending is None and not dialog.writePending and runtime.loadedProjectFile == str(second))
                waitUntil(lambda: dialog.snapshot['sourcePath'] == str(second))
                assert not dialog.useLocalSource.isVisible() and dialog.runButton.isEnabled()
                assert window._syncRuntimeProjectBeforeRun()
                assert not runtime.jobRepository.all()  # Writing never starts a job.
                assert second.read_bytes() == disk and window.pageCoordinator.session.payload() == before
                if output:
                    assert dialog.grab().save(str(directory / 'directory-committed.png'))
            finally:
                dialog.reject()
            runImage(client, runtime, document.project.projectId)
            assert (second.parent / DB).is_file() and (second.parent / 'outputs/result.png').is_file()
            assert not (first.parent / 'outputs/result.png').exists()
    finally:
        client.close()
        runtime.close()
