from types import SimpleNamespace
import time

from PySide2.QtCore import QSettings, QPoint
from PySide2.QtWidgets import QDialogButtonBox, QApplication, QMessageBox

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.device_wizard import DeviceNodeDialog
from tests.core.project.test_devices import declaration, definition, project  # noqa: F401


def windowFor(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    requirements = {"vision.io.huaray_camera": {"camera": {"deviceTypeId": declaration.deviceTypeId}}}
    runtime = SimpleNamespace(listOperators=lambda: [],
                              listDeviceTypes=lambda: ({declaration.deviceTypeId: declaration}, requirements, {}))
    window = ownedDesignerWindow(runtime, settingsStore=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    source = tmp_path / "device.emoproj"
    saveProject(source, project(definition))
    assert window.loadProjectDirectory(str(source), confirmed=True)
    return window, requirements


def waitUntil(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)
    assert predicate()


def testThirdWorkspaceEditHistoryAndSwitch(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    coordinator = window.pageCoordinator
    before = coordinator.session.payload()
    coordinator.showDevices()
    devices = coordinator.devices
    waitUntil(lambda: devices.pending is None)
    assert coordinator.deviceActive()
    assert len(coordinator.chrome.workspaceActions) == 3
    assert not window.deleteShortcut.isEnabled()
    assert coordinator.session.payload() == before
    devices.table.setCurrentCell(0, 0)
    devices.name.setText("Renamed camera")
    devices.apply()
    assert coordinator.session.document().devices.definitions["camera"].name == "Renamed camera"
    coordinator.history()
    assert coordinator.session.document().devices.definitions["camera"].name == definition.name
    coordinator.history(True)
    assert devices.name.text() == "Renamed camera"
    coordinator.showFlow()
    coordinator.showDevices()
    assert devices.name.text() == "Renamed camera"


def testThirdSelectorHighlightUsesThirdButton(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    window.resize(1280, 800)
    window.show()
    window.pageCoordinator.showDevices()
    selector = window.pageCoordinator.chrome.selector
    waitUntil(lambda: selector.animation.state() == selector.animation.Stopped)
    rect = selector.selectedRect().toRect()
    assert selector.buttons[2].geometry().contains(QPoint(rect.center().x(), rect.center().y()))


def testDeviceControlNeedsConfirmationAndNeverEditsDraft(ownedDesignerWindow, tmp_path, definition, declaration, monkeypatch):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    c.showDevices()
    devices = c.devices
    waitUntil(lambda: devices.pending is None)
    devices.table.setCurrentCell(0, 0)
    before = c.session.payload()
    state = dict(state="IDLE", generation="g1", definitionRevision="r1", queued=0, activeOwner=None)
    devices.states = {"camera": state}
    devices.stateRuntimeId = "runtime"
    calls = []
    def control(*args, **kwargs):
        calls.append((args, kwargs))
        return "runtime", {"camera": dict(state, admissionState="PAUSED", connectionState="DISCONNECTED")}
    window.runtimeClient.controlDevice = control
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.No)
    devices.control("disconnect")
    assert not calls
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    devices.buttons["disconnect"].click()
    waitUntil(lambda: devices.pending is None)
    assert len(calls) == 1 and calls[0][0][-1] == "disconnect" and calls[0][1] == {"confirmed": True}
    assert c.session.payload() == before
    assert "手动暂停" in devices.table.item(0, 3).text()


def testDeviceStateResponseIsDiscardedAfterScopeChanges(ownedDesignerWindow, tmp_path, definition, declaration, monkeypatch):  # noqa: F811
    from concurrent.futures import Future
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    window.pageCoordinator.showDevices()
    devices = window.pageCoordinator.devices
    waitUntil(lambda: devices.pending is None)
    scope = devices._scope()
    assert scope[-1] == window.pageCoordinator.session.document().project.projectId
    future = Future()
    devices.pending, devices.pendingKind, devices.pendingScope = future, "state", scope
    future.set_result(("old-runtime", {"camera": {"state": "IDLE"}}))
    monkeypatch.setattr(devices, "_scope", lambda: (*scope[:-1], "another-project"))
    devices._metadataReady()
    assert devices.pending is None and not devices.states and not devices.stateRuntimeId


def testWizardCancelAndSourceCommit(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    window, requirements = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    c.sync()
    before = c.session.payload()
    node = next(node for node in before["workflows"]["main"]["nodes"] if node["nodeId"] == "capture")
    dialog = DeviceNodeDialog(c, node, requirements[node["operatorId"]], window)
    waitUntil(lambda: dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())
    modes, form, _variable, _input = dialog.sources["camera"].rows["exposureUs"]
    modes.setCurrentIndex(modes.findData("constant"))
    form._controls["value"].setText("12000")
    dialog.reject()
    assert c.session.payload() == before
    dialog = DeviceNodeDialog(c, node, requirements[node["operatorId"]], window)
    waitUntil(lambda: dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())
    modes, form, _variable, _input = dialog.sources["camera"].rows["exposureUs"]
    modes.setCurrentIndex(modes.findData("constant"))
    form._controls["value"].setText("12000")
    dialog.accept()
    assert dialog.result() == dialog.Accepted, dialog.error.text()
    assert window.flowModel.nodes["capture"].deviceOverrides["camera"]["exposureUs"] == 12000
    c.history()
    assert window.flowModel.nodes["capture"].deviceOverrides == {}


def testNormalDropWizardAtomicFinishAndCancel(ownedDesignerWindow, tmp_path, definition, declaration, monkeypatch):  # noqa: F811
    window, requirements = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    c.sync()
    before = c.session.payload()
    payload = dict(operatorId="vision.io.huaray_camera", displayName="Bound camera", inputPorts={}, outputPorts={},
                   paramSchema={}, deviceRequirements=requirements["vision.io.huaray_camera"])
    def cancel(dialog):
        dialog.reject()
        return dialog.Rejected
    monkeypatch.setattr(DeviceNodeDialog, "exec_", cancel)
    window.addNodeFromOperatorDrop(payload, 160, 240)
    assert c.session.payload() == before
    depth = len(c.session._undo)
    def complete(dialog):
        waitUntil(lambda: dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())
        combo = dialog.slots["camera"][0]
        combo.setCurrentIndex(combo.findData("camera"))
        dialog.accept()
        assert dialog.result() == dialog.Accepted, dialog.error.text()
        return dialog.result()
    monkeypatch.setattr(DeviceNodeDialog, "exec_", complete)
    window.addNodeFromOperatorDrop(payload, 160, 240)
    assert len(c.session._undo) == depth + 1
    newId = next(key for key in window.flowModel.nodes if key not in {n["nodeId"] for n in before["workflows"]["main"]["nodes"]})
    assert window.flowModel.nodes[newId].deviceBindings == {"camera": {"deviceId": "camera"}}
    assert c.session.workflows.get("main").layout["nodePositions"][newId] == dict(x=160., y=240.)
    c.history()
    assert newId not in window.flowModel.nodes
