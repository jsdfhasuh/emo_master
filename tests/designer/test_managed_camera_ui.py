from copy import deepcopy
from types import SimpleNamespace

import pytest

from PySide2.QtCore import QSettings
from PySide2.QtWidgets import QDialogButtonBox, QMessageBox

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.device_wizard import DeviceNodeDialog
from emo_master.apps.designer.ui.managed_camera_debug import ManagedCameraDebugDialog
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.snapshots import revisionOf
from tests.runtime.test_managed_imv import ROOT, cameraProject, definition
from tests.designer.test_device_workspace import waitUntil


def testCameraDiagnosticsOpenWithoutHardwareAndConfirmEveryCapture(monkeypatch):
    d = definition()
    calls = []
    state = dict(generation="generation", definitionRevision=revisionOf(d.model_dump()))

    def diagnostic(*args, **kwargs):
        calls.append((args[4], kwargs))
        return dict(sessionId="session", metadata={}, preview=b"")

    client = SimpleNamespace(getDeviceStates=lambda project: ("runtime", {"camera": state}), cameraDiagnostic=diagnostic)
    dialog = ManagedCameraDebugDialog(client, "project", "camera", d)
    dialog.show()
    waitUntil(lambda: dialog.pending is None)
    assert [call[0] for call in calls] == ["open"]
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    dialog.capture()
    assert len(calls) == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    dialog.capture()
    waitUntil(lambda: dialog.pending is None)
    assert calls[-1] == ("capture", dict(sessionId="session", sequence=1, confirmed=True))
    dialog.reject()
    waitUntil(lambda: calls[-1][0] == "close")


@pytest.mark.parametrize("unlimited", [False, True])
def testCameraWizardPreservesLegacyKeepAsExplicitOverride(ownedDesignerWindow, tmp_path, monkeypatch, unlimited):
    scan = PluginRegistry("0.6.1").scan(ROOT)
    descriptor = scan.activeOperators["vision.io.huaray_camera"]
    requirements = {descriptor.manifest.operatorId: {"camera": {"deviceTypeId": "device.camera.huaray_imv"}}}
    client = SimpleNamespace(listOperators=lambda: [], listDeviceTypes=lambda: (scan.deviceTypes, requirements, {}))
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    payload = cameraProject()
    legacy = payload["workflows"]["main"]["nodes"][1]
    legacy.pop("deviceBindings")
    legacy["params"] = dict(selectionMode="cameraKey", cameraKey="FAKE-KEY", exposureMode="keep", retryCount=2)
    if unlimited:
        legacy["params"].update(waitMode="hardware", triggerMode="hardware")
    path = tmp_path / "project.emoproj"
    saveProject(path, payload)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    c = window.pageCoordinator
    c.sync()
    before = c.session.payload()
    draft = deepcopy(legacy)
    draft["paramSchema"] = descriptor.manifest.paramSchema
    dialog = DeviceNodeDialog(c, draft, requirements[draft["operatorId"]], window)
    dialog.show()
    waitUntil(lambda: dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())
    combo = dialog.slots["camera"][0]
    combo.setCurrentIndex(combo.findData("camera"))
    assert not dialog.params._controls
    assert dialog.sources["camera"].rows["exposureMode"][0].currentData() == "constant"
    if unlimited:
        waitUntil(lambda: dialog.sources["camera"].isVisible())
        from PySide2.QtWidgets import QApplication
        QApplication.processEvents()
        assert dialog.grab().save(str(tmp_path / "camera-migration-unlimited.png"))
    prompts = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: prompts.append(args[2]) or QMessageBox.Yes)
    dialog.accept()
    assert dialog.result() == dialog.Accepted, dialog.error.text()
    node = c.session.document().workflows["main"].nodes[1]
    assert node.params == {} and node.deviceOverrides["camera"]["exposureMode"] == "keep"
    assert "keep" in prompts[0]
    if unlimited:
        assert node.deviceOverrides["camera"]["captureTimeoutMs"] == 0
        assert node.deviceOverrides["camera"]["triggerMode"] == "hardware"
        assert "超时 0" in prompts[0]
    c.history()
    assert c.session.payload() == before


def testCameraMigrationMovesVariableAndSiteTargetsInOneUndo(ownedDesignerWindow, tmp_path, monkeypatch):
    scan = PluginRegistry("0.6.1").scan(ROOT)
    descriptor = scan.activeOperators["vision.io.huaray_camera"]
    requirements = {descriptor.manifest.operatorId: {"camera": {"deviceTypeId": "device.camera.huaray_imv"}}}
    client = SimpleNamespace(listOperators=lambda: [], listDeviceTypes=lambda: (scan.deviceTypes, requirements, {}))
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    payload = cameraProject()
    payload["globalVariables"] = {"exposure": dict(name="Exposure", type="number", initialValue=8000.0)}
    legacy = payload["workflows"]["main"]["nodes"][1]
    legacy.pop("deviceBindings")
    legacy["params"] = dict(selectionMode="cameraKey", cameraKey="FAKE-KEY", exposureMode="manual", exposureUs=8000.0)
    legacy["globalVariableBindings"] = [dict(parameterPath=["exposureUs"], variableId="exposure")]
    payload["resources"]["siteBindings"] = [dict(target=dict(workflowId="main", nodeId=legacy["nodeId"],
        parameterPath=["cameraKey"]), field="camera.key", purpose="camera_serial", required=True)]
    path = tmp_path / "project.emoproj"
    saveProject(path, payload)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    c = window.pageCoordinator
    c.sync()
    before, disk = c.session.payload(), path.read_bytes()
    draft = deepcopy(legacy)
    draft["paramSchema"] = descriptor.manifest.paramSchema
    dialog = DeviceNodeDialog(c, draft, requirements[draft["operatorId"]], window)
    waitUntil(lambda: dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())
    combo = dialog.slots["camera"][0]
    combo.setCurrentIndex(combo.findData("camera"))
    assert dialog.sources["camera"].rows["exposureUs"][0].currentData() == "variable", dialog.error.text()
    prompts = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: prompts.append(args[2]) or QMessageBox.Yes)
    dialog.accept()
    assert dialog.result() == dialog.Accepted, dialog.error.text()
    document = c.session.document()
    node = document.workflows["main"].nodes[1]
    assert not node.globalVariableBindings and not node.params
    assert node.deviceValueBindings["camera"]["exposureUs"].variableId == "exposure"
    assert "exposureUs" not in node.deviceOverrides["camera"]
    assert document.resources.siteBindings[0].target.deviceId == "camera"
    assert "camera/connection/cameraKey" in prompts[0]
    assert path.read_bytes() == disk
    assert c.session.undo() and c.session.payload() == before
