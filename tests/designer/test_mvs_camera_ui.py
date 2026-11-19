from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QSettings
from PySide2.QtWidgets import QDialogButtonBox, QMessageBox

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.device_migration import DeviceMigrationDialog
from emo_master.apps.designer.ui.device_network import MvsDiscoveryDialog
from emo_master.apps.designer.ui.device_wizard import DeviceDefinitionDialog, DeviceNodeDialog, DeviceTypeDialog
from emo_master.apps.designer.ui.managed_camera_debug import ManagedCameraDebugDialog
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.devices import DeviceDefinition
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.snapshots import revisionOf
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.designer.qt_wait import waitForCatalog
from tests.core.project.test_devices import legacyPayload
from tests.designer.test_device_workspace import waitUntil
from tests.runtime.test_managed_imv import ROOT, cameraProject


MVS = "device.camera.hikrobot_mvs"
IMV = "device.camera.huaray_imv"
CAMERA = "vision.io.huaray_camera"


def testMvsDeviceDiscoveryFillsSelectedSerialOnly(retainedQtApplication, monkeypatch):
    declarations, rejected = PluginRegistry("0.6.1").scanDeviceTypes([ROOT])
    assert not rejected
    calls = []

    def command(project, runtime, action, **kwargs):
        calls.append(action)
        if action == "info":
            return dict(projectId="project", runtimeInstanceId="runtime")
        assert action == "discover_mvs" and (project, runtime) == ("project", "runtime")
        return dict(devices=[dict(serialNumber="GIGE-001", modelName="Camera A", transport="GigE"),
                             dict(serialNumber="USB-002", modelName="Camera B", transport="USB3")])

    def select(dialog):
        waitUntil(lambda: dialog.pending is None)
        assert dialog.forceButton.isHidden() and dialog.table.rowCount() == 2
        dialog.table.selectRow(1)
        dialog.adoptButton.click()
        return dialog.result()

    monkeypatch.setattr(MvsDiscoveryDialog, "exec_", select)
    dialog = DeviceDefinitionDialog(declarations[MVS], client=SimpleNamespace(deviceNetworkCommand=command))
    dialog.show()
    assert not calls
    dialog.discoverButton.click()
    serial = dialog.forms["connection"]._controls["serialNumber"]
    waitUntil(lambda: serial.text() == "USB-002")
    dialog.accept()
    assert dialog.value["connection"]["serialNumber"] == "USB-002" and calls == ["info", "discover_mvs"]


def testExistingCameraNodeCanRegisterMvsAndSwitchBackBeforeSaving(ownedDesignerWindow, tmp_path, monkeypatch):
    scan = PluginRegistry("0.6.1").scan(ROOT)
    manifest = scan.activeOperators[CAMERA].manifest
    requirements = {CAMERA: {"camera": manifest.deviceRequirements["camera"].model_dump()}}
    client = SimpleNamespace(listOperators=lambda: [], listDeviceTypes=lambda: (scan.deviceTypes, requirements, {}))
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    payload = cameraProject()
    cameraNode = payload["workflows"]["main"]["nodes"][1]
    cameraNode["deviceOverrides"] = {"camera": {"gainMode": "manual", "gainRaw": 8}}
    project = tmp_path / "camera.emoproj"
    saveProject(project, payload)
    assert window.loadProjectDirectory(str(project), confirmed=True)
    c = window.pageCoordinator
    c.sync()
    draft = deepcopy(cameraNode)
    draft["paramSchema"] = manifest.paramSchema
    before = c.session.payload()
    dialog = DeviceNodeDialog(c, draft, requirements[CAMERA], window)
    dialog.show()
    waitUntil(lambda: dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())
    assert not dialog.migrateLegacy
    combo = dialog.slots["camera"][0]
    assert combo.currentData() == "camera"

    def choose(dialog):
        assert {dialog.selection.itemData(i) for i in range(dialog.selection.count())} == {IMV, MVS}
        index = dialog.selection.findData(MVS)
        assert "海康" in dialog.selection.itemText(index)
        dialog.selection.setCurrentIndex(index)
        return dialog.Accepted

    def configure(dialog):
        assert dialog.discoverButton.isEnabled()
        serial = dialog.forms["connection"]._controls["serialNumber"]
        assert not serial.text()
        serial.setText("TEST-MVS")
        assert dialog.forms["defaults"].getValues()["triggerMode"] == "software"
        dialog.accept()
        assert dialog.result() == dialog.Accepted, dialog.error.text()
        return dialog.result()

    monkeypatch.setattr(DeviceTypeDialog, "exec_", choose)
    monkeypatch.setattr(DeviceDefinitionDialog, "exec_", configure)
    dialog._create("camera")
    newId = combo.currentData()
    assert newId != "camera" and "gainRaw" in dialog._sourceHints["camera"].text()
    assert "gainDb" in dialog.sources["camera"].rows
    combo.setCurrentIndex(combo.findData("camera"))
    oldGain, oldForm, _, _ = dialog.sources["camera"].rows["gainRaw"]
    assert oldGain.currentData() == "constant" and oldForm.getValues()["value"] == 8
    combo.setCurrentIndex(combo.findData(newId))
    sources = dialog.sources["camera"].rows
    for key, value in (("exposureMode", "manual"), ("exposureUs", "1350")):
        modes, form, _, _ = sources[key]
        modes.setCurrentIndex(modes.findData("constant"))
        control = form._controls["value"]
        assert control.isEnabled()
        if key == "exposureMode":
            control.setCurrentIndex(control.findData(value))
        else:
            control.setText(value)
    assert c.session.payload() == before
    dialog.accept()
    assert dialog.result() == dialog.Accepted, dialog.error.text()
    saveProject(project, c.session.payload())
    assert window.loadProjectDirectory(str(project), confirmed=True)
    document = window.pageCoordinator.session.document()
    persisted = next(n for n in document.workflows["main"].nodes if n.nodeId == cameraNode["nodeId"])
    assert persisted.operatorId == CAMERA and persisted.deviceBindings["camera"].deviceId == newId
    assert persisted.deviceOverrides["camera"] == {"exposureMode": "manual", "exposureUs": 1350}
    assert document.devices.definitions[newId].connection["serialNumber"] == "TEST-MVS"
    assert document.devices.definitions["camera"].deviceTypeId == IMV


def testMvsDiagnosticsOfferCaptureWithoutImvTuning(retainedQtApplication, monkeypatch):
    definition = DeviceDefinition(name="海康测试", deviceTypeId=MVS, driverVersion="1.0.0",
                                  connection={"serialNumber": "TEST-MVS"})
    state = dict(generation="g", definitionRevision=revisionOf(definition.model_dump()))
    calls = []

    def diagnostic(*args, **kwargs):
        calls.append((args[4], kwargs))
        return dict(sessionId="session", metadata=dict(cameraTuningVersion=0, cameraPreviewVersion=1), preview=b"")

    client = SimpleNamespace(getDeviceStates=lambda _: ("runtime", {"camera": state}), cameraDiagnostic=diagnostic)
    dialog = ManagedCameraDebugDialog(client, "project", "camera", definition)
    dialog.show()
    try:
        waitUntil(lambda: dialog.pending is None)
        assert dialog.captureButton.isEnabled() and dialog.liveButton.isEnabled()
        assert dialog.readButton.isHidden() and dialog.trialButton.isHidden()
        dialog.previewTimer.stop()
        assert [action for action, _ in calls] == ["open"]
        monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
        dialog.captureButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert calls[-1][0] == "capture" and calls[-1][1]["confirmed"]
    finally:
        dialog.reject()
        waitUntil(lambda: calls[-1][0] == "close")


@pytest.mark.parametrize("entry,legacySource,target", [
    ("inventory", "historical_schema", "mvs"), ("double_click", "minimal_schema", "mvs"),
    ("double_click", "variable_only", "mvs"), ("double_click", "site_only", "camera"),
    ("double_click", "bound_residue", "camera"),
])
def testLegacyCameraMigrationSurvivesReopenAndCompiles(
        ownedDesignerWindow, tmp_path, monkeypatch, entry, legacySource, target):
    scan = PluginRegistry("0.6.1").scan(ROOT)
    manifest = scan.activeOperators[CAMERA].manifest
    requirements = {CAMERA: {"camera": manifest.deviceRequirements["camera"].model_dump()}}
    catalog = SimpleNamespace(operatorId=CAMERA, displayName=manifest.displayName, paramSchema=manifest.paramSchema,
                              deviceRequirements=requirements[CAMERA], outputPorts=manifest.outputPorts)
    client = SimpleNamespace(listOperators=lambda: [catalog], listDeviceTypes=lambda: (scan.deviceTypes, requirements, {}))
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    waitForCatalog(window)
    payload = cameraProject()
    payload["devices"]["definitions"]["mvs"] = dict(name="海康测试", deviceTypeId=MVS, driverVersion="1.0.0",
                                                    connection={"serialNumber": "MVS-TARGET"})
    legacy = payload["workflows"]["main"]["nodes"][1]
    if legacySource != "bound_residue":
        legacy.pop("deviceBindings")
    if legacySource in {"historical_schema", "minimal_schema", "bound_residue"}:
        legacy["params"] = dict(selectionMode="cameraKey", cameraKey="OLD-IMV-IDENTITY", gainRaw=8)
    if legacySource in {"historical_schema", "bound_residue"}:
        # The pre-managed 1.2.0 schema at 63070277133 matches these fields exactly.
        legacy["paramSchema"] = deepcopy(manifest.paramSchema)
        del legacy["paramSchema"]["xDeviceActionFields"]
    if legacySource == "bound_residue":
        payload["globalVariables"] = {key: dict(name=key, type="number", initialValue=value)
                                      for key, value in (("oldExposure", 4333.0), ("currentGain", 3.0))}
        legacy["globalVariableBindings"] = [dict(parameterPath=["exposureUs"], variableId="oldExposure")]
        legacy["deviceOverrides"] = {"camera": {"exposureMode": "manual", "exposureUs": 1800, "gainMode": "manual"}}
        legacy["deviceValueBindings"] = {"camera": {"gainRaw": {"source": "variable", "variableId": "currentGain"}}}
    elif legacySource == "variable_only":
        payload["globalVariables"] = {"exp": dict(name="Exposure", type="number", initialValue=1500.0)}
        legacy["globalVariableBindings"] = [dict(parameterPath=["exposureUs"], variableId="exp")]
        payload["devices"]["definitions"]["mvs"]["defaults"] = {"exposureMode": "manual"}
    elif legacySource == "site_only":
        payload["resources"]["siteBindings"] = [dict(target=dict(workflowId="main", nodeId=legacy["nodeId"],
            parameterPath=["cameraKey"]), field="site.key", purpose="camera_serial", required=True)]
    project = tmp_path / "legacy.emoproj"
    saveProject(project, payload)
    assert window.loadProjectDirectory(str(project), confirmed=True)
    c = window.pageCoordinator
    c.sync()
    before = c.session.payload()
    disk = project.read_bytes()
    messages = []
    def edit(dialog):
        waitUntil(lambda: dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())
        assert dialog.migrateLegacy and dialog.actionFields == []
        assert not dialog.params._controls
        combo = dialog.slots["camera"][0]
        if legacySource != "bound_residue":
            dialog.savePending()
            assert "先选择设备" in dialog.error.text() and c.session.payload() == before
        else:
            assert combo.currentData() == target
        combo.setCurrentIndex(combo.findData(target))
        dialog.accept()
        if confirm:
            assert dialog.result() == dialog.Accepted, dialog.error.text()
        else:
            assert c.session.payload() == before and project.read_bytes() == disk
            dialog.reject()
        return dialog.result()

    def answer(_, title, msg, *args):
        assert title == "确认设备迁移"
        messages.append(msg)
        return QMessageBox.Yes if confirm else QMessageBox.No

    monkeypatch.setattr(DeviceNodeDialog, "exec_", edit)
    monkeypatch.setattr(QMessageBox, "question", answer)
    for confirm in (False, True):
        if entry == "inventory":
            migration = DeviceMigrationDialog(c, requirements, window)
            migration.configureSelected()
            migration.close()
        else:
            window.openNodeParamDialog(legacy["nodeId"])
    assert len(messages) == 2
    if legacySource in {"historical_schema", "minimal_schema"}:
        assert "MVS-TARGET" in messages[0] and "cameraKey = OLD-IMV-IDENTITY" in messages[0]
    elif legacySource == "variable_only":
        assert "exposureUs" in messages[0] and "exp" in messages[0]
    elif legacySource == "bound_residue":
        assert "oldExposure" in messages[0] and "cameraKey = OLD-IMV-IDENTITY" in messages[0]
    else:
        assert "camera/connection/cameraKey" in messages[0]
    saveProject(project, c.session.payload())
    assert window.loadProjectDirectory(str(project), confirmed=True)
    document = c.session.document()
    node = next(n for n in document.workflows["main"].nodes if n.nodeId == legacy["nodeId"])
    assert node.deviceBindings["camera"].deviceId == target and not node.params and not node.globalVariableBindings
    assert node.paramSchema == manifest.paramSchema
    if target == "mvs":
        assert not node.deviceOverrides and document.devices.definitions[target].connection["serialNumber"] == "MVS-TARGET"
    if legacySource == "variable_only":
        assert node.deviceValueBindings["camera"]["exposureUs"].variableId == "exp"
    if legacySource == "site_only":
        assert document.resources.siteBindings[0].target.deviceId == target
    if legacySource == "bound_residue":
        assert node.deviceOverrides == legacy["deviceOverrides"]
        assert node.deviceValueBindings["camera"]["gainRaw"].variableId == "currentGain"
        assert "exposureUs" not in node.deviceValueBindings["camera"]
    WorkflowCompiler(scan.activeOperators, deviceTypes=scan.deviceTypes).compile(document)


@pytest.mark.parametrize("savedSchema", ["old_defaults", "modern", "empty"])
def testDefaultOnlyHistoricalCameraAndModernEmptyDraft(ownedDesignerWindow, tmp_path, monkeypatch, savedSchema):
    scan = PluginRegistry("0.6.1").scan(ROOT)
    manifest = scan.activeOperators[CAMERA].manifest
    requirements = {CAMERA: {key: value.model_dump() for key, value in manifest.deviceRequirements.items()}}
    catalog = SimpleNamespace(operatorId=CAMERA, displayName=manifest.displayName, paramSchema=manifest.paramSchema,
                              deviceRequirements=requirements[CAMERA])
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: [catalog],
        listDeviceTypes=lambda: (scan.deviceTypes, requirements, {})))
    waitForCatalog(window)
    if savedSchema == "old_defaults":
        payload = migrateProjectPayload(legacyPayload(), enableCommunication=True)
        assert payload["schemaVersion"] == "2.5"
        node = dict(nodeId="old", operatorId=CAMERA, paramSchema=deepcopy(manifest.paramSchema), params={})
        del node["paramSchema"]["xDeviceActionFields"]
        payload["workflows"]["main"]["nodes"].append(node)
    else:
        payload = cameraProject()
        node = payload["workflows"]["main"]["nodes"][1]
        node.pop("deviceBindings")
        node["paramSchema"] = manifest.paramSchema if savedSchema == "modern" else {}
    project = tmp_path / "defaults.emoproj"
    saveProject(project, payload)
    assert window.loadProjectDirectory(str(project), confirmed=True)
    c = window.pageCoordinator
    c.sync()
    before = c.session.payload()
    prompts = []
    def confirm(_, title, msg, *args):
        assert title == "确认设备迁移"
        prompts.append(msg)
        return QMessageBox.No if len(prompts) == 1 else QMessageBox.Yes

    def chooseType(dialog):
        dialog.selection.setCurrentIndex(dialog.selection.findData(IMV))
        return dialog.Accepted

    def define(dialog):
        dialog.forms["connection"]._controls["cameraKey"].setText("TEST-IMV")
        dialog.accept()
        return dialog.result()

    def edit(dialog):
        waitUntil(lambda: dialog.pendingButton.isEnabled())
        if savedSchema != "old_defaults":
            assert not dialog.migrateLegacy
            dialog.savePending()
        else:
            assert dialog.migrateLegacy
            dialog._create("camera")
            dialog.accept()
            assert len(prompts) == 1 and c.session.payload() == before
            dialog.accept()
        assert dialog.result() == dialog.Accepted, dialog.error.text()
        return dialog.result()

    monkeypatch.setattr(QMessageBox, "question", confirm)
    monkeypatch.setattr(DeviceTypeDialog, "exec_", chooseType)
    monkeypatch.setattr(DeviceDefinitionDialog, "exec_", define)
    monkeypatch.setattr(DeviceNodeDialog, "exec_", edit)
    window.openNodeParamDialog(node["nodeId"])
    if savedSchema != "old_defaults":
        assert not prompts
        return
    assert len(prompts) == 2 and "triggerMode=hardware" in prompts[0] and "exposureMode=keep" in prompts[0]
    saveProject(project, c.session.payload())
    assert window.loadProjectDirectory(str(project), confirmed=True)
    document = c.session.document()
    migrated = next(n for n in document.workflows["main"].nodes if n.nodeId == node["nodeId"])
    assert migrated.deviceOverrides["camera"]["triggerMode"] == "hardware"
    assert migrated.deviceOverrides["camera"]["exposureMode"] == "keep"
    WorkflowCompiler(scan.activeOperators, deviceTypes=scan.deviceTypes).compile(document)
