from copy import deepcopy
from types import SimpleNamespace
from threading import Event

import pytest
from PySide2.QtCore import QUrl, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QMessageBox, QFileDialog, QApplication

from emo_master.apps.designer.ui.device_wizard import DeviceDefinitionDialog
from emo_master.apps.designer.ui.managed_camera_debug import ManagedCameraDebugDialog
from emo_master.core.project.snapshots import revisionOf
from tests.designer.test_device_workspace import windowFor, waitUntil
from tests.core.project.test_devices import declaration, definition  # noqa: F401


def testSearchAndCategoryNeverRetainHiddenCommands(ownedDesignerWindow, tmp_path, definition, declaration, monkeypatch):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    window.pageCoordinator.showDevices()
    d = window.pageCoordinator.devices
    waitUntil(lambda: d.pending is None)
    d.table.setCurrentCell(0, 0)
    assert d.tree.currentItem() is d.instanceItems['camera']
    calls = []
    window.runtimeClient.controlDevice = lambda *args, **kwargs: calls.append(args)
    d.states = {'camera': dict(state='IDLE', generation='g', definitionRevision='r', queued=0, activeOwner=None)}
    d.stateRuntimeId = 'runtime'
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    d.search.setText('no matching device')
    assert d.table.isRowHidden(0) and d.selectedId is None and not d.name.text()
    for key in ('connect', 'disconnect', 'maintenance', 'recover', 'diagnostics', 'network'):
        assert not d.buttons[key].isEnabled()
        command = d.buttons[key]
        (command.click if hasattr(command, 'click') else command.trigger)()
    assert not calls
    d.search.clear()
    d.tree.setCurrentItem(d.instanceItems['camera'])
    assert d.selectedId == 'camera' and d.table.currentRow() == 0
    d.name.setText('')
    d.search.setText('hide')
    assert d.search.text() == '' and not d.table.isRowHidden(0)
    d.revert()


def testReferenceNavigationAndPackageNativeUndoRedo(ownedDesignerWindow, tmp_path, definition, declaration, monkeypatch):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    c.showDevices()
    d = c.devices
    waitUntil(lambda: d.pending is None)
    d.table.setCurrentCell(0, 0)
    assert '继承设备' in d.references.toPlainText()
    destination = tmp_path / 'camera.emodev'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_: (str(destination), ''))
    d.buttons['export'].click()
    assert destination.exists(), d.status.text()
    before = c.session.payload()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *_: (str(destination), ''))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.No)
    d.buttons['import'].click()
    assert c.session.payload() == before
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    d.buttons['import'].click()
    assert len(c.session.document().devices.definitions) == 2, d.status.text()
    imported = d.selectedId
    assert not c.session.document().devices.definitions[imported].enabled
    c.history()
    assert c.session.payload() == before
    c.history(True)
    assert imported in c.session.document().devices.definitions
    d.table.setCurrentCell(0, 0)
    d.references.anchorClicked.emit(QUrl('main/capture'))
    assert not d.isVisible() and c.flowActive()
    assert window.activeWorkflowId == 'main'
    assert c.session.document().devices.definitions['camera'].name == definition.name


def testInstanceSelectionAndSearchStayOnVisibleDevice(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    second = definition.model_dump()
    second['name'] = 'Camera B'
    second['connection']['source'] = 'second-source'
    with c.session.transaction():
        c.session.workflows.devices['definitions']['second'] = second
    window.show()
    c.showDevices()
    d = c.devices
    waitUntil(lambda: d.pending is None)
    d.tree.setCurrentItem(d.instanceItems['second'])
    assert d.selectedId == 'second' and d.table.item(d.table.currentRow(), 0).data(Qt.UserRole) == 'second'
    d.search.setText('Camera A')
    assert d.selectedId is None and not d.buttons['connect'].isEnabled() and not d.name.text()
    assert d.table.currentRow() == -1
    d.search.clear()
    d.tree.setCurrentItem(d.instanceItems['camera'])
    assert d.selectedId == 'camera' and d.table.item(d.table.currentRow(), 0).data(Qt.UserRole) == 'camera'


def testWindowMonitorDoesNotReplaceEditModel(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    before = c.session.payload()
    requests = []
    def offline(*args):
        requests.append(args)
        raise ValueError('offline simulated Runtime')
    window.runtimeClient.getOnlineGraphSnapshot = offline
    window.show()
    deleteEnabled = window.designerActions.actions['delete'].isEnabled()
    active = c.stack.currentWidget()
    c.chrome.monitorAction.trigger()
    monitor = c.monitor
    waitUntil(lambda: monitor.pending is None)
    assert c.stack.currentWidget() is active and monitor.isWindow()
    assert monitor.parentWidget() is window and not monitor.isModal()
    assert not hasattr(c, 'flowModes') and not hasattr(c, 'flowStack')
    assert requests[0][0] == ''
    assert window.designerActions.actions['delete'].isEnabled() == deleteEnabled
    assert not monitor.view.isInteractive()
    assert c.session.payload() == before
    QTest.keyClick(monitor, Qt.Key_Escape)
    assert not monitor.isVisible() and not monitor.timer.isActive()
    assert c.monitor is None
    assert c.flowActive() and window.designerActions.actions['delete'].isEnabled() == deleteEnabled
    assert c.session.payload() == before


def testWizardConnectionConfirmationAndCancelLeaveNoDraft(retainedQtApplication, declaration, definition, monkeypatch):  # noqa: F811
    calls = []
    def command(_project, _runtime, action, **kwargs):
        calls.append((action, kwargs))
        return dict(projectId='online', runtimeInstanceId='instance', deviceSetupVersion=1) if action == 'info' else dict(connected=True)
    dialog = DeviceDefinitionDialog(declaration, initial=definition.model_dump(), client=SimpleNamespace(deviceNetworkCommand=command))
    dialog.show()
    try:
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.No)
        dialog.testButton.click()
        assert not calls
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
        dialog.testButton.click()
        waitUntil(lambda: dialog.setupPending is None)
        assert [action for action, _ in calls] == ['info', 'test_connection']
        assert calls[-1][1]['payload']['confirmed']
        assert dialog.value is None
        dialog.reject()
        assert dialog.value is None and not dialog.isVisible()
    finally:
        dialog.close()


def testManagedReadTrialSignalsAndOldRuntimeMessage(retainedQtApplication, monkeypatch):
    from tests.runtime.test_managed_imv import definition as imvDefinition
    d = imvDefinition()
    before = deepcopy(d.model_dump())
    calls = []
    def diagnostic(*args, **kwargs):
        calls.append((args[4], kwargs))
        metadata = {'cameraTuningVersion': 1} if args[4] == 'open' else {'actual': {'ExposureTime': 8000}}
        return dict(sessionId='session', metadata=metadata, preview=b'')
    client = SimpleNamespace(getDeviceStates=lambda _: ('runtime', {'camera': dict(generation='g', definitionRevision=revisionOf(before))}),
                             cameraDiagnostic=diagnostic)
    dialog = ManagedCameraDebugDialog(client, 'project', 'camera', d)
    dialog.show()
    waitUntil(lambda: dialog.pending is None)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    dialog.readButton.click()
    waitUntil(lambda: dialog.pending is None)
    assert calls[-1][0] == 'read_parameters' and '8000' in dialog.actual.toPlainText()
    dialog.exposure.setValue(16000)
    dialog.trialButton.click()
    waitUntil(lambda: dialog.pending is None)
    assert calls[-1][0] == 'trial_capture' and calls[-1][1]['changes']['exposureUs'] == 16000
    assert d.model_dump() == before
    dialog.tuningVersion = 0
    count = len(calls)
    dialog.readButton.click()
    assert len(calls) == count and '升级' in dialog.status.text()
    dialog.reject()
    waitUntil(lambda: calls[-1][0] == 'close')


def testWizardDiscoveryAdoptsDraftWithoutNetworkMutation(retainedQtApplication, monkeypatch):
    from emo_master.apps.designer.ui.device_network import DeviceNetworkDialog
    from emo_master.core.project.device_resources import trustedDeviceTypes
    cameraType = trustedDeviceTypes()['device.camera.huaray_imv']
    calls = []
    def command(_project, _runtime, action, **kwargs):
        calls.append(action)
        if action == 'info':
            return dict(projectId='', runtimeInstanceId='instance', deviceSetupVersion=1)
        assert action == 'discover'
        return dict(devices=[dict(cameraKey='SIMULATED-CAMERA', cameraType=0)])
    def discover(dialog):
        dialog.show()
        assert dialog.discoveryOnly and not dialog.forceButton.isVisible()
        dialog.discoverButton.click()
        waitUntil(lambda: dialog.pending is None)
        dialog.table.setCurrentCell(0, 0)
        dialog.adoptButton.click()
        return dialog.result()
    monkeypatch.setattr(DeviceNetworkDialog, 'exec_', discover)
    dialog = DeviceDefinitionDialog(cameraType, client=SimpleNamespace(deviceNetworkCommand=command))
    dialog.show()
    try:
        dialog.discoverButton.click()
        waitUntil(lambda: dialog.forms['connection'].getValues().get('cameraKey') == 'SIMULATED-CAMERA')
        assert calls == ['info', 'discover']
        assert dialog.value is None
        dialog.reject()
        assert dialog.value is None
    finally:
        dialog.close()


@pytest.mark.parametrize('loadedProject', ['', 'other-online-project'])
def testDeviceWorkspaceOfflineDiscoveryOnlyAdoptsDraft(ownedDesignerWindow, tmp_path, monkeypatch, loadedProject):
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError
    from emo_master.apps.designer.ui.device_network import DeviceNetworkDialog
    from emo_master.core.project.device_resources import trustedDeviceTypes
    from tests.runtime.test_managed_imv import definition as imvDefinition

    cameraType = trustedDeviceTypes()['device.camera.huaray_imv']
    window, _ = windowFor(ownedDesignerWindow, tmp_path, imvDefinition(), cameraType)
    calls, dialogs = [], []
    def states(_project):
        raise RuntimeClientError('E_CONFIG_PROJECT' if loadedProject else 'E_CONFIG_NOT_LOADED', 'simulated offline draft')
    def command(projectId, runtimeId, action, **kwargs):
        calls.append(action)
        if action == 'info':
            assert projectId == runtimeId == ''
            return dict(projectId=loadedProject, runtimeInstanceId='instance', deviceSetupVersion=1)
        assert action == 'discover'
        assert (projectId, runtimeId, kwargs['deviceId']) == (loadedProject, 'instance', 'camera')
        return dict(devices=[dict(cameraKey='OFFLINE-DISCOVERED-CAMERA', cameraType=0)])
    def discover(dialog):
        dialogs.append(dialog)
        dialog.show()
        assert dialog.discoveryOnly and not dialog.forceButton.isVisible()
        assert not dialog.receiptButton.isVisible()
        assert calls == ['info']
        dialog.discoverButton.click()
        waitUntil(lambda: dialog.pending is None)
        dialog.table.setCurrentCell(0, 0)
        dialog.adoptButton.click()
        return dialog.result()

    window.runtimeClient.getDeviceStates = states
    window.runtimeClient.deviceNetworkCommand = command
    monkeypatch.setattr(DeviceNetworkDialog, 'exec_', discover)
    c = window.pageCoordinator
    c.chrome.deviceAction.trigger()
    d = c.devices
    waitUntil(lambda: d.pending is None and bool(d.stateNotice))
    d.table.setCurrentCell(0, 0)
    assert not d.stateRuntimeId and d.buttons['network'].isEnabled()
    assert not d.buttons['connect'].isEnabled() and not d.buttons['disconnect'].isEnabled()
    assert not calls and not dialogs  # Opening/refreshing never discovers hardware.
    before = c.session.payload()
    path = tmp_path / 'device.emoproj'
    disk = path.read_bytes()
    d.buttons['network'].trigger()
    waitUntil(lambda: c.session.workflows.devices['definitions']['camera']['connection']['cameraKey'] == 'OFFLINE-DISCOVERED-CAMERA')
    expected = deepcopy(before)
    expected['devices']['definitions']['camera']['connection']['cameraKey'] = 'OFFLINE-DISCOVERED-CAMERA'
    assert c.session.payload() == expected and c.session.dirty
    assert len(dialogs) == 1 and calls == ['info', 'discover']
    assert path.read_bytes() == disk
    d.closeButton.click()
    assert not d.isVisible() and path.read_bytes() == disk
    assert calls == ['info', 'discover']


def testOfflineDiscoveryReportsOldRuntimeInsteadOfStaleUnpublishedNotice(ownedDesignerWindow, tmp_path):
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError
    from emo_master.core.project.device_resources import trustedDeviceTypes
    from tests.runtime.test_managed_imv import definition as imvDefinition

    cameraType = trustedDeviceTypes()['device.camera.huaray_imv']
    window, _ = windowFor(ownedDesignerWindow, tmp_path, imvDefinition(), cameraType)
    calls = []
    def states(_project):
        raise RuntimeClientError('E_CONFIG_NOT_LOADED', 'empty Runtime')
    def command(_project, _runtime, action, **_kwargs):
        calls.append(action)
        assert action == 'info'
        return dict(projectId='', runtimeInstanceId='old-instance', deviceSetupVersion=0)
    window.runtimeClient.getDeviceStates = states
    window.runtimeClient.deviceNetworkCommand = command
    c = window.pageCoordinator
    c.chrome.deviceAction.trigger()
    d = c.devices
    waitUntil(lambda: d.pending is None and bool(d.stateNotice))
    d.table.setCurrentCell(0, 0)
    before = c.session.payload()
    d.buttons['network'].trigger()
    waitUntil(lambda: d.pending is None)
    assert calls == ['info'] and '升级 Runtime' in d.status.text()
    assert '未下发' not in d.status.text() and d.buttons['network'].isEnabled()
    assert c.session.payload() == before


@pytest.mark.parametrize('closeMode', ['close', 'reopen', 'invalid-close'])
def testClosingDeviceWorkspaceAbandonsOnlyPendingDiscoveryIntent(ownedDesignerWindow, tmp_path, monkeypatch, closeMode):
    from PySide2.QtWidgets import QDialog
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError
    from emo_master.apps.designer.ui.device_network import DeviceNetworkDialog
    from emo_master.core.project.device_resources import trustedDeviceTypes
    from tests.runtime.test_managed_imv import definition as imvDefinition

    cameraType = trustedDeviceTypes()['device.camera.huaray_imv']
    window, _ = windowFor(ownedDesignerWindow, tmp_path, imvDefinition(), cameraType)
    entered, release = Event(), Event()
    opened = []
    def states(_project):
        raise RuntimeClientError('E_CONFIG_NOT_LOADED', 'empty isolated Runtime')
    def command(_project, _runtime, action, **_kwargs):
        assert action == 'info'
        entered.set()
        assert release.wait(5)
        return dict(projectId='', runtimeInstanceId='instance', deviceSetupVersion=1)
    def modal(dialog):
        opened.append(dict(parentVisible=dialog.parentWidget().isVisible(), discoveryOnly=dialog.discoveryOnly))
        dialog.reject()
        return QDialog.Rejected
    window.runtimeClient.getDeviceStates = states
    window.runtimeClient.deviceNetworkCommand = command
    monkeypatch.setattr(DeviceNetworkDialog, 'exec_', modal)
    c = window.pageCoordinator
    c.chrome.deviceAction.trigger()
    d = c.devices
    waitUntil(lambda: d.pending is None and bool(d.stateNotice))
    d.table.setCurrentCell(0, 0)
    try:
        d.buttons['network'].trigger()
        assert entered.wait(2)
        if closeMode == 'invalid-close':
            d.name.setText('')
        d.closeButton.click()
        assert d.isVisible() == (closeMode == 'invalid-close')
        if closeMode == 'reopen':
            c.showDevices()
            assert d.isVisible()
        release.set()
        waitUntil(lambda: d.pending is None)
        assert opened == ([dict(parentVisible=True, discoveryOnly=True)] if closeMode == 'invalid-close' else [])
        d.revert()
        c.showDevices()
        waitUntil(lambda: d.pending is None)
        d.table.setCurrentCell(0, 0)
        count = len(opened)
        d.buttons['network'].trigger()
        waitUntil(lambda: d.pending is None)
        assert len(opened) == count + 1 and opened[-1]['parentVisible']
    finally:
        release.set()


@pytest.mark.parametrize('width,height', [(1280, 800), (900, 650)])
def testDeviceDialogLayoutEvidence(ownedDesignerWindow, tmp_path, definition, declaration, width, height):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    window.show()
    window.pageCoordinator.showDevices()
    d = window.pageCoordinator.devices
    waitUntil(lambda: d.pending is None)
    d.resize(width, height)
    d.table.setCurrentCell(0, 0)
    QApplication.processEvents()
    assert d.table.columnCount() == 8 and d.rect().contains(d.closeButton.geometry())
    assert d.grab().save(str(tmp_path / f'device-completion-{width}.png'))
    print('DEVICE_COMPLETION_SCREENSHOT=' + str(tmp_path / f'device-completion-{width}.png'))
