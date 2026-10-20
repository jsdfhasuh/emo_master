from types import SimpleNamespace
import time

from PySide2.QtCore import QSettings, Qt
from PySide2.QtWidgets import QDialog, QDialogButtonBox, QApplication, QMessageBox
from PySide2.QtTest import QTest

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


def testDeviceDialogEditHistoryAndReopen(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    coordinator = window.pageCoordinator
    before = coordinator.session.payload()
    active = coordinator.stack.currentWidget()
    coordinator.showDevices()
    devices = coordinator.devices
    waitUntil(lambda: devices.pending is None)
    assert coordinator.deviceActive()
    assert isinstance(devices, QDialog) and devices.isWindow()
    assert devices.parentWidget() is window and devices.windowModality() == Qt.WindowModal
    assert coordinator.stack.indexOf(devices) == -1 and coordinator.stack.currentWidget() is active
    assert len(coordinator.chrome.workspaceActions) == 2
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
    devices.closeButton.click()
    assert not devices.isVisible() and coordinator.stack.currentWidget() is active
    coordinator.showDevices()
    assert coordinator.devices is devices
    assert devices.name.text() == "Renamed camera"


def testDeviceMenuOpensDialogWithoutChangingWorkspace(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    window.resize(1280, 800)
    window.show()
    c = window.pageCoordinator
    action = c.chrome.deviceAction
    assert action in window.menuBar().actions() and not action.isCheckable()
    active, count = c.stack.currentWidget(), c.stack.count()
    action.trigger()
    devices = c.devices
    waitUntil(lambda: devices.pending is None)
    assert devices.isVisible() and devices.isWindow()
    assert c.stack.currentWidget() is active and c.stack.count() == count
    assert len(c.chrome.selector.buttons) == 2 and c.chrome.workspaceActions[0].isChecked()
    action.trigger()
    assert c.devices is devices and len(window.findChildren(QDialog)) == 1
    devices.table.setCurrentCell(0, 0)
    QApplication.processEvents()
    assert devices.rect().contains(devices.closeButton.geometry())
    assert devices.grab().save(str(tmp_path / 'device-dialog.png'))
    print('DEVICE_DIALOG_SCREENSHOT=' + str(tmp_path / 'device-dialog.png'))
    QTest.keyClick(devices, Qt.Key_Escape)
    assert not devices.isVisible() and c.flowActive() and not c.deviceActive()
    # The canonical QAction owns Delete; the legacy QShortcut stays disabled.
    assert not window.deleteShortcut.isEnabled()
    assert not window.designerActions.blockedReason('layout')
    action.trigger()
    assert c.devices is devices and devices.isVisible()
    assert devices.close() and not devices.isVisible()


def testDeviceDialogReturnsToPagesAndRetiresOnProjectSwitch(ownedDesignerWindow, tmp_path, definition, declaration, monkeypatch):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    c.showPages()
    active = c.stack.currentWidget()
    c.chrome.deviceAction.trigger()
    devices = c.devices
    waitUntil(lambda: devices.pending is None)
    assert c.pageActive() and c.stack.currentWidget() is active
    devices.closeButton.click()
    assert c.pageActive() and c.chrome.workspaceActions[1].isChecked()
    c.chrome.deviceAction.trigger()
    source = tmp_path / 'another.emoproj'
    payload = project(definition)
    payload['project']['projectId'] = 'another-project'
    saveProject(source, payload)
    assert window.loadProjectDirectory(str(source), confirmed=True)
    assert not devices.isVisible() and not devices.timer.isActive() and c.devices is None
    c.chrome.deviceAction.trigger()
    assert c.devices is not devices
    waitUntil(lambda: c.devices.pending is None)
    assert c.devices._scope()[-1] == 'another-project'


def testDeviceDialogCloseKeepsDraftAndRejectsInvalidInput(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    disk = (tmp_path / 'device.emoproj').read_bytes()
    c.showDevices()
    devices = c.devices
    waitUntil(lambda: devices.pending is None)
    devices.table.setCurrentCell(0, 0)
    devices.name.setText('Dialog edit')
    devices.closeButton.click()
    assert not devices.isVisible()
    assert c.session.document().devices.definitions['camera'].name == 'Dialog edit'
    assert c.session.dirty and (tmp_path / 'device.emoproj').read_bytes() == disk
    c.showDevices()
    devices.name.setText('')
    assert not devices.close() and devices.isVisible()
    assert devices.status.text()
    devices.buttons['revert'].click()
    assert devices.close()


def testDeviceDialogCloseDuringMetadataReadCanReopen(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    from threading import Event
    window, requirements = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    entered, release = Event(), Event()
    def metadata():
        entered.set()
        assert release.wait(5)
        return {declaration.deviceTypeId: declaration}, requirements, {}
    window.runtimeClient.listDeviceTypes = metadata
    c = window.pageCoordinator
    try:
        c.chrome.deviceAction.trigger()
        devices = c.devices
        assert entered.wait(2)
        devices.closeButton.click()
        assert not devices.isVisible() and not c.deviceActive()
        release.set()
        waitUntil(lambda: devices.pending is None)
        c.chrome.deviceAction.trigger()
        assert c.devices is devices and devices.isVisible()
        devices.buttons['refresh'].click()
        waitUntil(lambda: devices.pending is None)
        assert declaration.deviceTypeId in devices.deviceTypes
    finally:
        release.set()


def testDeviceControlNeedsConfirmationAndNeverEditsDraft(ownedDesignerWindow, tmp_path, definition, declaration, monkeypatch):  # noqa: F811
    declaration = declaration.model_copy(update={'capabilities': ['connect', 'disconnect', 'capture']})
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    c.showDevices()
    devices = c.devices
    waitUntil(lambda: devices.pending is None)
    devices.table.setCurrentCell(0, 0)
    before = c.session.payload()
    state = dict(state="IDLE", generation="g1", definitionRevision="r1", queued=0, activeOwner=None,
                 lifecycleConnected=True, driverState={'connectionState': 'CONNECTED'},
                 availableControls=['disconnect', 'maintenance', 'recover'])
    devices.states = {"camera": state}
    devices.stateRuntimeId = "runtime"
    calls = []
    def control(*args, **kwargs):
        calls.append((args, kwargs))
        return "runtime", {"camera": dict(state, admissionState="PAUSED", lifecycleConnected=False,
            driverState={'connectionState': 'DISCONNECTED'}, availableControls=['connect', 'resume', 'recover'])}
    window.runtimeClient.controlDevice = control
    devices._showStates()
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
    assert window.flowModel.nodes[newId].deviceBindings == {"camera": {"deviceId": "camera", "selectors": {}}}
    assert c.session.workflows.get("main").layout["nodePositions"][newId] == dict(x=160., y=240.)
    c.history()
    assert newId not in window.flowModel.nodes
