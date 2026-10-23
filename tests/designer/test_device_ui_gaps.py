"""Plan-to-UI regressions. All endpoints and device identities are simulated."""
from copy import deepcopy
from types import SimpleNamespace

from PySide2.QtCore import Qt
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer.ui.device_wizard import SourceEditor
from tests.core.project.test_devices import declaration, definition  # noqa: F401
from tests.designer.test_device_workspace import windowFor, waitUntil
from tests.designer.test_project_configuration_ui import setupDialog


def testOpeningAndRefreshingReadsKnownStatesWithoutLosingEdits(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    calls = []
    state = dict(state='IDLE', generation='g', definitionRevision='r', queued=0, activeOwner=None,
                 connectionState='DISCONNECTED', lifecycleConnected=False)
    def states(project):
        calls.append(project)
        return 'instance', {'camera': deepcopy(state)}
    window.runtimeClient.getDeviceStates = states
    window.pageCoordinator.showDevices()
    d = window.pageCoordinator.devices
    waitUntil(lambda: d.pending is None)
    assert calls and '未连接' in d.table.item(0, 3).text()
    d.table.setCurrentCell(0, 0)
    d.name.setText('Unapplied editor value')
    state.update(queued=2, activeOwner='simulated-job', connectionState='CONNECTED', lifecycleConnected=True)
    d.stateTimer.timeout.emit()
    waitUntil(lambda: d.pending is None)
    assert d.name.text() == 'Unapplied editor value'
    assert '2' in d.table.item(0, 5).text() and '已连接' in d.table.item(0, 3).text()
    d.buttons['refresh'].click()
    waitUntil(lambda: d.pending is None)
    assert len(calls) >= 3
    assert 'on_first_use' not in d.table.item(0, 4).text()
    assert not d.table.item(0, 0).icon().isNull()
    assert declaration.displayName in d.typeInfo.text() and declaration.version in d.typeInfo.text()
    assert '"generation"' not in d.onlineState.toPlainText()


def testCapabilityControlsAndExistingDraftConnectionTest(ownedDesignerWindow, tmp_path, definition, declaration, monkeypatch):  # noqa: F811
    declaration = declaration.model_copy(update={'capabilities': ['connect', 'disconnect', 'capture']})
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    calls = []
    def command(project, runtime, action, **kwargs):
        calls.append((action, kwargs))
        return dict(projectId='online', runtimeInstanceId='instance', deviceSetupVersion=1)
    window.runtimeClient.deviceNetworkCommand = command
    window.pageCoordinator.showDevices()
    d = window.pageCoordinator.devices
    waitUntil(lambda: d.pending is None)
    d.table.setCurrentCell(0, 0)
    assert not d.buttons['network'].isEnabled()
    assert not d.buttons['diagnostics'].isEnabled()
    before = window.pageCoordinator.session.payload()
    d.forms['connection']._controls['source'].setText('draft-only')
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.No)
    d.buttons['test'].click()
    assert not calls
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    d.buttons['test'].click()
    waitUntil(lambda: d.pending is None)
    assert [action for action, _ in calls] == ['info', 'test_connection']
    assert calls[-1][1]['payload']['definition']['connection']['source'] == 'draft-only'
    assert window.pageCoordinator.session.payload() == before
    assert '通过' in d.status.text()


def testSourceFieldComparesOnlineRevisionAndDynamicSource(retainedQtApplication, declaration, definition):  # noqa: F811
    local = dict(nodeId='capture', deviceBindings={'camera': {'deviceId': 'camera'}})
    online = deepcopy(definition.model_dump())
    online['defaults']['exposureUs'] = 12000
    editor = SourceEditor(declaration, definition.model_dump(), local, 'camera', {})
    editor.setOnline(online, local, revision=8, deviceId='camera')
    label = editor.onlineLabels['exposureUs']
    assert '12000' in label.text() and 'R8' in label.text() and '未应用' in label.text()
    modes, form, _, _ = editor.rows['exposureUs']
    modes.setCurrentIndex(modes.findData('constant'))
    form._controls['value'].setText('12000')
    assert '未应用' in label.text()  # Source differs even when the numeric value matches.
    editor.setOnline(online, dict(local, deviceOverrides={'camera': {'exposureUs': 12000}}), revision=9, deviceId='camera')
    assert '一致' in label.text() and 'R9' in label.text()
    editor.setOnline(online, dict(local, deviceValueBindings={'camera': {
        'exposureUs': {'source': 'input', 'inputName': 'exposure'}}}), revision=10, deviceId='camera')
    assert 'exposure' in label.text() and '运行时' in label.text()
    editor.deleteLater()


def testValidatedEffectsAreNextToSelectedChanges(ownedDesignerWindow, tmp_path):
    window, d, _calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        path = '/devices/definitions/camera/defaults/exposureUs'
        row = next(r for r in range(d.table.rowCount()) if d.table.item(r, 0).data(Qt.UserRole) == path)
        d.table.item(row, 0).setCheckState(Qt.Checked)
        def validate(*args, **kwargs):
            assert kwargs['validate']
            return dict(cycleSafe=True, affectedJobs=['job-1'], disconnectDevices=[],
                        changeEffects={path: dict(effect='next_cycle', devices=['camera'],
                                                 workflows=['main'], jobs=['job-1'])})
        window.runtimeClient.writeProjectConfiguration = validate
        d.validateButton.click()
        waitUntil(lambda: d.pending is None)
        assert d.table.columnCount() == 6
        assert '下一检测周期' in d.table.item(row, 4).text()
        assert d.local['devices']['definitions']['camera']['name'] in d.table.item(row, 5).text()
        assert d.local['workflows']['main']['name'] in d.table.item(row, 5).text()
        assert 'job-1' in d.table.item(row, 5).text()
        d.table.item(row, 0).setCheckState(Qt.Unchecked)
        assert d.impact.text() == '尚未检查选中改动的影响'
    finally:
        d.reject()


def testWritePreflightsAndShowsImpactBeforeSingleConfirmation(ownedDesignerWindow, tmp_path, monkeypatch):
    window, d, _calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    calls = []
    path = '/devices/definitions/camera/defaults/exposureUs'
    def write(*args, **kwargs):
        calls.append(kwargs['validate'])
        return (dict(cycleSafe=True, changeEffects={path: dict(effect='next_cycle', devices=['camera'])})
                if kwargs['validate'] else dict(state='COMMITTED'))
    window.runtimeClient.writeProjectConfiguration = write
    try:
        row = next(r for r in range(d.table.rowCount()) if d.table.item(r, 0).data(Qt.UserRole) == path)
        d.table.item(row, 0).setCheckState(Qt.Checked)
        def cancel(_parent, _title, text, *_args):
            assert '无需停止' in text and '下一检测周期' in d.table.item(row, 4).text()
            return QMessageBox.No
        monkeypatch.setattr(QMessageBox, 'question', cancel)
        d.writeButton.click()
        waitUntil(lambda: d.pending is None)
        assert calls == [True] and not d.requestId
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
        d.writeButton.click()
        waitUntil(lambda: d.pending is None)
        assert calls == [True, False]
    finally:
        d.reject()


def testCameraWatchingNeverCapturesAndPreviewIsSingleFlight(retainedQtApplication, monkeypatch):
    from concurrent.futures import Future
    from emo_master.apps.designer.ui.managed_camera_debug import ManagedCameraDebugDialog
    from emo_master.core.project.snapshots import revisionOf
    from tests.runtime.test_managed_imv import definition as cameraDefinition
    camera = cameraDefinition()
    calls = []
    def diagnostic(*args, **kwargs):
        action = args[4]
        calls.append(action)
        metadata = (dict(cameraTuningVersion=1, cameraPreviewVersion=1) if action == 'open' else
                    dict(productionActive=False, watchSequence=0) if action == 'watch' else {})
        return dict(sessionId='session', preview=b'', metadata=metadata)
    client = SimpleNamespace(getDeviceStates=lambda _: ('runtime', {'camera': dict(
        generation='g', definitionRevision=revisionOf(camera.model_dump()))}), cameraDiagnostic=diagnostic)
    d = ManagedCameraDebugDialog(client, 'project', 'camera', camera)
    d.show()
    try:
        waitUntil(lambda: d.pending is None)
        d.previewTimer.timeout.emit()
        waitUntil(lambda: d.pending is None)
        assert 'watch' in calls and 'capture' not in calls and 'preview_capture' not in calls
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
        d.liveButton.click()
        waitUntil(lambda: d.pending is None)
        d.previewTimer.timeout.emit()
        waitUntil(lambda: d.pending is None)
        assert 'preview_capture' in calls
        before = len(calls)
        blocked = Future()
        d.pending = blocked
        for _ in range(3):
            d.previewTimer.timeout.emit()
        assert len(calls) == before
        d.pending = None
        d.liveButton.click()
        assert not d.liveButton.isChecked()
    finally:
        d.reject()
        waitUntil(lambda: calls[-1] == 'close')
    assert not d.previewTimer.isActive()


def testNativeDeviceDialogProductionViewingWithRealRuntime(ownedDesignerWindow, tmp_path, monkeypatch):
    import json
    from time import monotonic
    from PySide2.QtCore import QSettings, QTimer
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.apps.designer.ui.managed_camera_debug import ManagedCameraDebugDialog
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from tests.runtime.imv_test_support import SimulatedImvRuntimeService
    from tests.runtime.test_managed_imv import cameraProject
    service = SimulatedImvRuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    client = RuntimeClient(service)
    path = tmp_path / 'camera.emoproj'
    path.write_text(json.dumps(cameraProject()), encoding='utf-8')
    errors = []
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        manager = service.deviceManager
        service.deviceNetwork.discovery = (monotonic(), dict(devices=[dict(cameraKey='FAKE-KEY',
            modelName='Simulated IMV', serialNumber='SIM-001', macAddress='00:00:00:00:00:01')]))
        window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / 'ui.ini'), QSettings.IniFormat))
        assert window.loadProjectDirectory(str(path), confirmed=True)
        window.show()
        window.pageCoordinator.showDevices()
        d = window.pageCoordinator.devices
        waitUntil(lambda: d.pending is None)
        d.table.setCurrentCell(0, 0)
        assert d.buttons['network'].isEnabled() and d.buttons['diagnostics'].isEnabled()
        assert d.hardwareFields['modelName'].text() == 'Simulated IMV'
        assert d.hardwareFields['serialNumber'].text() == 'SIM-001'
        before = window.pageCoordinator.session.payload()
        for width, height in [(900, 650), (1280, 800)]:
            d.resize(width, height)
            from PySide2.QtWidgets import QApplication
            QApplication.processEvents()
            assert d.size().width() == width and d.rect().contains(d.closeButton.geometry())
            image = tmp_path / f'gap-device-{width}.png'
            assert d.grab().save(str(image))
            print('GAP_SCREENSHOT=' + str(image))
        def exercise(monitor):
            try:
                waitUntil(lambda: monitor.pending is None)
                monitor.previewTimer.timeout.emit()
                waitUntil(lambda: monitor.pending is None)
                assert not manager.permissions
                manager.authorize('simulated-production', ['camera'])
                cycle = service.projectConfiguration.beginCycle('simulated-production', -1)
                descriptor = manager.execute('simulated-production', 'camera', 'capture',
                    manager.definitions['camera'].defaults, cycleId=cycle['cycleId'],
                    source=dict(workflowId='main', nodeId='capture', slot='camera'))
                manager.frames.release(descriptor['frameId'], 'simulated-production')
                monitor.previewTimer.timeout.emit()
                waitUntil(lambda: monitor.pending is None)
                assert monitor.previewPixmap is not None and not monitor.previewPixmap.isNull()
                assert 'simulated-production' in monitor.status.text() and cycle['cycleId'] in monitor.status.text()
                monitor.liveButton.click()
                waitUntil(lambda: monitor.pending is None)
                assert not monitor.liveButton.isChecked() and monitor.sequence == 0
                assert not manager.frames.frames and not manager.maintenanceOwners
                image = tmp_path / 'gap-camera-production.png'
                assert monitor.grab().save(str(image))
                print('GAP_SCREENSHOT=' + str(image))
                service.projectConfiguration.endCycle('simulated-production', cycle['cycleId'])
                manager.retire('simulated-production')
                monitor.previewTimer.timeout.emit()
                waitUntil(lambda: monitor.pending is None)
                assert not monitor.productionActive
            except BaseException as error:
                errors.append(error)
            finally:
                monitor.reject()
        original = ManagedCameraDebugDialog.exec_
        def nativeExec(monitor):
            QTimer.singleShot(0, lambda: exercise(monitor))
            return original(monitor)
        monkeypatch.setattr(ManagedCameraDebugDialog, 'exec_', nativeExec)
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
        d.buttons['diagnostics'].trigger()
        assert not errors, errors
        waitUntil(lambda: not manager.maintenanceOwners)
        assert window.pageCoordinator.session.payload() == before
        waitUntil(lambda: not any(not s.closed.is_set() for s in service.cameraDiagnostics.sessions.values()))
    finally:
        client.close()
        service.close()


def testNodeWizardLoadsOnlineSnapshotAndPreservesDraft(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    from PySide2.QtWidgets import QDialogButtonBox
    from emo_master.apps.designer.ui.device_wizard import DeviceNodeDialog
    window, requirements = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    c.sync()
    before = c.session.payload()
    online = deepcopy(before)
    online['devices']['definitions']['camera']['defaults']['exposureUs'] = 12000
    window.runtimeClient.getOnlineProjectSnapshot = lambda _: dict(payload=online, documentRevision=17)
    node = next(n for n in before['workflows']['main']['nodes'] if n['nodeId'] == 'capture')
    dialog = DeviceNodeDialog(c, node, requirements[node['operatorId']], window)
    try:
        waitUntil(lambda: dialog.buttons.button(QDialogButtonBox.Ok).isEnabled() and dialog.onlinePending is None)
        text = dialog.sources['camera'].onlineLabels['exposureUs'].text()
        assert 'R17' in text and '12000' in text and '未应用' in text
        dialog.onlineButton.click()
        waitUntil(lambda: dialog.onlinePending is None)
        assert c.session.payload() == before
    finally:
        dialog.reject()
