from copy import deepcopy
from types import SimpleNamespace

from PySide2.QtCore import Qt, QSettings
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
from tests.runtime.test_device_runtime import simulatedProject
from tests.designer.test_device_workspace import waitUntil
from tests.designer.runtime_feedback_fixture import messages  # noqa: F401


def setupDialog(ownedDesignerWindow, tmp_path, *, allowRun=False):
    local = simulatedProject()
    online = deepcopy(local)
    online["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 12000
    snapshot = dict(runtimeInstanceId="instance", documentRevision=8, executionRevision=5, payload=online)
    calls = []
    def write(snapshot, payload, paths, requestId, *, validate=False):
        calls.append((deepcopy(payload), paths, requestId, validate))
        return {"candidate": payload} if validate else {"state": "COMMITTED", "documentRevision": 9}
    client = SimpleNamespace(listOperators=lambda: [], getOnlineProjectSnapshot=lambda projectId: snapshot,
                             writeProjectConfiguration=write)
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    path = tmp_path / "project.emoproj"
    saveProject(path, local)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    window.pageCoordinator.sync()
    dialog = ProjectConfigurationDialog(window.pageCoordinator, window, allowRun=allowRun)
    waitUntil(lambda: dialog.pending is None)
    return window, dialog, calls, path


def testRecoveryErrorAllowsExplicitRepairButNeverRun(ownedDesignerWindow, tmp_path, monkeypatch):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path, allowRun=True)
    try:
        before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
        dialog.snapshot["recoveryError"] = "resource missing or size mismatch"
        dialog._controls()
        assert dialog.writeButton.isEnabled() and dialog.validateButton.isEnabled()
        assert not dialog.runButton.isEnabled()
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            if item.data(Qt.UserRole) == "/devices/definitions/camera/defaults/exposureUs":
                item.setCheckState(Qt.Checked)
        dialog.validateButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert calls[-1][3]
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.writeButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert len(calls) == 2 and not calls[-1][3]
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
    finally:
        dialog.reject()


def testCompareDoesNotSaveOrMutateAndSelectionIsExplicit(ownedDesignerWindow, tmp_path, monkeypatch, messages):  # noqa: F811
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        before = window.pageCoordinator.session.payload()
        disk = path.read_bytes()
        assert not calls
        assert dialog.table.rowCount() > 0
        dialog.write()
        assert not calls and len(messages) == 1 and '请选择' in messages[0]['text']
        pathToSelect = "/devices/definitions/camera/defaults/exposureUs"
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            if item.data(Qt.UserRole) == pathToSelect:
                item.setCheckState(Qt.Checked)
        dialog.write(validate=True)
        waitUntil(lambda: dialog.pending is None)
        assert calls[-1][1] == [pathToSelect] and calls[-1][3]
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.write()
        waitUntil(lambda: dialog.pending is None)
        assert calls[-1][1] == [pathToSelect] and not calls[-1][3]
        assert window.pageCoordinator.session.payload() == before
        assert path.read_bytes() == disk
    finally:
        dialog.reject()


def testReadBackIsUndoableDraftNotDiskWrite(ownedDesignerWindow, tmp_path, monkeypatch):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        before = window.pageCoordinator.session.payload()
        disk = path.read_bytes()
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.readBack()
        assert window.pageCoordinator.session.document().devices.definitions["camera"].defaults["exposureUs"] == 12000
        assert window.pageCoordinator.session.dirty
        assert not calls and path.read_bytes() == disk
        window.pageCoordinator.history()
        assert window.pageCoordinator.session.payload() == before
    finally:
        dialog.reject()


def testRuntimeSwitchFencesOldDialog(ownedDesignerWindow, tmp_path):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        before = window.pageCoordinator.session.payload()
        dialog.table.item(0, 0).setCheckState(Qt.Checked)
        original = window.runtimeClient
        window.runtimeClient = SimpleNamespace()
        dialog.write()
        dialog.readBack()
        assert not calls and window.pageCoordinator.session.payload() == before
        window.runtimeClient = original
    finally:
        dialog.reject()


def testDifferentOnlineProjectSelectsCompleteReplacementAndReadBackRequiresConsent(ownedDesignerWindow, tmp_path, monkeypatch):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
        dialog.snapshot["payload"]["project"]["projectId"] = "different-online-project"
        dialog._showSnapshot(dialog.snapshot)
        dialog._controls()
        assert dialog.replacementPolicy.currentData() == "stopAffected"
        assert all(dialog.table.item(row, 0).checkState() == Qt.Checked for row in range(dialog.table.rowCount()))
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
        dialog.readButton.click()
        assert window.pageCoordinator.session.payload() == before
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.readButton.click()
        assert window.pageCoordinator.session.document().project.projectId == "different-online-project"
        assert dialog._valid() and path.read_bytes() == disk and not calls
        window.pageCoordinator.history()
        assert window.pageCoordinator.session.payload() == before
    finally:
        dialog.reject()


def testDeviceDependenciesRequireConfirmationAndLeaveUnselectedDraftOut(ownedDesignerWindow, tmp_path, monkeypatch):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        disk = path.read_bytes()
        before = window.pageCoordinator.session.payload()
        local = dialog.local
        device = deepcopy(local["devices"]["definitions"]["camera"])
        device["connection"]["serial"] = "NEW"
        local["devices"]["definitions"]["new-camera"] = device
        capture = next(node for node in local["workflows"]["main"]["nodes"] if node["nodeId"] == "capture")
        capture["deviceBindings"]["camera"]["deviceId"] = "new-camera"
        dialog._showSnapshot(dialog.snapshot)
        bindingPath = "/workflows/main/nodes/capture/deviceBindings/camera/deviceId"
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            if item.data(Qt.UserRole) == bindingPath:
                item.setCheckState(Qt.Checked)
                assert dialog.table.item(row, 3).text() == "设备引用"
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
        dialog.write(validate=True)
        assert not calls
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.write(validate=True)
        waitUntil(lambda: dialog.pending is None)
        assert set(calls[-1][1]) == {bindingPath, "/devices/definitions/new-camera"}
        assert path.read_bytes() == disk and window.pageCoordinator.session.payload() == before
    finally:
        dialog.reject()


def testResourceUploadRequiresConfirmationAndDoesNotPublishDraft(ownedDesignerWindow, tmp_path, monkeypatch):
    import hashlib
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    uploads = []
    def upload(snapshot, payload, root, cancelled, *, repairConfirmed=False):
        assert repairConfirmed
        uploads.append((snapshot, payload, root, cancelled))
        return dict(uploaded=[dict(resourceId="model", state="READY")])
    dialog.client.uploadProjectResources = upload
    try:
        before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
        dialog.local["resources"]["items"]["model"] = dict(path="models/model.bin", size=3,
            sha256=hashlib.sha256(b"abc").hexdigest(), purpose="model")
        dialog._showSnapshot(dialog.snapshot)
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            if item.data(Qt.UserRole) == "/resources/items/model":
                item.setCheckState(Qt.Checked)
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
        dialog.uploadResources()
        assert not uploads
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.uploadResources()
        waitUntil(lambda: dialog.pending is None)
        assert len(uploads) == 1 and not calls
        assert uploads[0][1]["resources"]["items"]["model"]["size"] == 3
        assert uploads[0][1]["devices"] == dialog.snapshot["payload"]["devices"]
        assert "在线配置未改变" in dialog.status.text()
        dialog.show()
        from PySide2.QtWidgets import QApplication
        QApplication.processEvents()
        assert dialog.grab().save(str(tmp_path / "resource-upload-comparison.png"))
        for button in (dialog.uploadButton, dialog.queryButton, dialog.cancelButton, dialog.graphButton, dialog.closeButton):
            assert dialog.rect().contains(button.geometry())
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
    finally:
        dialog.reject()
    assert uploads[0][3].is_set()
