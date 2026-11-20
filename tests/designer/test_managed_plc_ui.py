from copy import deepcopy
from types import SimpleNamespace

from PySide2.QtCore import QSettings
from PySide2.QtWidgets import QDialogButtonBox, QLineEdit, QMessageBox

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.device_wizard import DeviceNodeDialog
from emo_master.apps.designer.ui.managed_plc_debug import ManagedPlcDebugDialog
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.devices import DeviceDefinition
from emo_master.plugins.builtins._plc_editor import PlcEditorController
from tests.runtime.test_managed_slmp import ROOT, plcProject
from tests.designer.test_device_workspace import waitUntil


def testFixedWriteValueInSavedPlcNodeDialog(ownedDesignerWindow, tmp_path):
    scan = PluginRegistry("0.6.1").scan(ROOT)
    writeId = "communication.plc.slmp_write"
    descriptor = scan.activeOperators[writeId].manifest
    requirements = {writeId: {"plc": {"deviceTypeId": "device.plc.mitsubishi_slmp"}}}
    client = SimpleNamespace(listOperators=lambda: [SimpleNamespace(**descriptor.model_dump())],
                             listDeviceTypes=lambda: (scan.deviceTypes, requirements, {}))
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    payload = plcProject(10001, write=True)
    node = payload["workflows"]["main"]["nodes"][0]
    node["params"].update(startAddress=1031, values=[])
    node["paramSchema"] = deepcopy(descriptor.paramSchema)
    node["paramSchema"]["properties"]["values"] = {
        "title": "写入值", "type": "array", "items": {"type": "number"}, "default": [],
    }
    path = tmp_path / "plc.emoproj"
    saveProject(path, payload)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    coordinator = window.pageCoordinator

    def openWriteDialog():
        coordinator.sync()
        saved = coordinator.session.document().workflows["main"].nodes[0]
        dialog = DeviceNodeDialog(coordinator, saved.model_dump(), requirements[writeId], window)
        dialog.show()
        waitUntil(lambda: dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())
        control = dialog.params._controls["values"]
        assert isinstance(control, QLineEdit)
        return dialog, control

    dialog, control = openWriteDialog()
    assert control.text() == ""
    assert "2" in control.placeholderText() and "留空" in control.placeholderText()
    control.setText("2")
    dialog.buttons.button(QDialogButtonBox.Ok).click()
    assert dialog.result() == dialog.Accepted, dialog.error.text()
    assert coordinator.session.document().workflows["main"].nodes[0].params["values"] == [2]
    assert window.saveProjectToDirectory(str(path))
    assert window.loadProjectDirectory(str(path), confirmed=True)

    dialog, control = openWriteDialog()
    assert control.text() == "2"
    for widget in (dialog.params._layout.labelForField(control), control):
        for detail in ("uint16", "D1031", "D1032", "[2]", "留空"):
            assert detail in widget.toolTip()
    for text, expected in (("0", [0]), ("1，2, 3", [1, 2, 3]), ("[2]", [2])):
        control.setText(text)
        assert dialog.params.getValues()["values"] == expected
    control.setText("2,,3")
    dialog.buttons.button(QDialogButtonBox.Ok).click()
    assert dialog.isVisible() and dialog.error.text()
    assert control.text() == "2,,3"
    assert coordinator.session.document().workflows["main"].nodes[0].params["values"] == [2]
    control.clear()
    dialog.buttons.button(QDialogButtonBox.Ok).click()
    assert dialog.result() == dialog.Accepted, dialog.error.text()
    assert coordinator.session.document().workflows["main"].nodes[0].params["values"] == []


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
    dialog = DeviceNodeDialog(c, draft, requirements[draft["operatorId"]], window, migrateLegacy=True)
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
    assert "连接恢复由设备策略控制" in prompts[0]
    assert "业务读写不自动重放" in prompts[0]
    c.history()
    assert c.session.payload() == before
