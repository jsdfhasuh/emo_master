from copy import deepcopy
from types import SimpleNamespace

from PySide2.QtCore import QSettings
from PySide2.QtWidgets import QDialogButtonBox, QMessageBox

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.device_wizard import DeviceNodeDialog
from emo_master.apps.designer.ui.managed_plc_debug import ManagedPlcDebugDialog
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.devices import DeviceDefinition
from emo_master.plugins.builtins._plc_editor import PlcEditorController
from tests.runtime.test_managed_slmp import ROOT, plcProject
from tests.designer.test_device_workspace import waitUntil


def testManagedDiagnosticUsesFixedDeviceAndConfirmsEveryWrite(monkeypatch):
    scan = PluginRegistry("0.6.1").scan(ROOT)
    declaration = scan.deviceTypes["device.plc.mitsubishi_slmp"]
    definition = declaration.validateDefinition(DeviceDefinition.model_validate(plcProject(10001)["devices"]["definitions"]["plc"]))
    opened = []
    client = SimpleNamespace(openPlcDebugSession=lambda *args, **kwargs: opened.append(args))
    dialog = ManagedPlcDebugDialog(client, "project", "plc", definition)
    dialog.show()
    controller = dialog.controller
    assert controller.tabs.currentWidget() is controller.runtimePage
    assert not opened
    assert all(not control.isEnabled() for control in controller._endpointControls.values())
    assert not controller.reloadButton.isEnabled()
    controller.context.openPlcDebugSession({"host": "changed"}, "open")
    assert opened[0][4]["deviceId"] == "plc" and "host" not in opened[0][4]
    writes = []
    monkeypatch.setattr(controller, "_canWrite", lambda: True)
    monkeypatch.setattr(PlcEditorController, "write", lambda self: writes.append(self._readParams()))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    controller.write()
    assert not writes
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    controller.write()
    assert len(writes) == 1 and writes[0]["confirmed"] is True
    dialog.reject()
    assert controller._disposed


def testLegacyPlcWizardShowsActionOnlyAndRequiresExplicitMigration(ownedDesignerWindow, tmp_path, monkeypatch):
    scan = PluginRegistry("0.6.1").scan(ROOT)
    declaration = scan.deviceTypes["device.plc.mitsubishi_slmp"]
    descriptor = scan.activeOperators["communication.plc.slmp_read"]
    requirements = {descriptor.manifest.operatorId: {"plc": {"deviceTypeId": declaration.deviceTypeId}}}
    client = SimpleNamespace(listOperators=lambda: [], listDeviceTypes=lambda: (scan.deviceTypes, requirements, {}))
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    payload = plcProject(10001)
    legacy = payload["workflows"]["main"]["nodes"][0]
    legacy.pop("deviceBindings")
    legacy["params"].update(host="127.0.0.1", port=10001, retryCount=2)
    path = tmp_path / "project.emoproj"
    saveProject(path, payload)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    c = window.pageCoordinator
    c.sync()
    before = c.session.payload()
    draft = deepcopy(legacy)
    draft["paramSchema"] = descriptor.manifest.paramSchema
    dialog = DeviceNodeDialog(c, draft, requirements[draft["operatorId"]], window)
    waitUntil(lambda: dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())
    assert set(dialog.params._controls) == set(descriptor.manifest.paramSchema["xDeviceActionFields"])
    combo = dialog.slots["plc"][0]
    combo.setCurrentIndex(combo.findData("plc"))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    dialog.accept()
    assert c.session.payload() == before
    prompts = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: prompts.append(args[2]) or QMessageBox.Yes)
    dialog.accept()
    assert dialog.result() == dialog.Accepted, dialog.error.text()
    node = c.session.document().workflows["main"].nodes[0]
    assert node.deviceBindings["plc"].deviceId == "plc"
    assert "host" not in node.params and "retryCount" not in node.params
    assert node.params["startAddress"] == 800
    assert "自动重试取消" in prompts[0]
    c.history()
    assert c.session.payload() == before
