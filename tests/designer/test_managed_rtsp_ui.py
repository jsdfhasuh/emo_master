from copy import deepcopy
from dataclasses import asdict
import os
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QSettings, Qt
from PySide2.QtWidgets import QApplication, QDialogButtonBox, QLineEdit, QMessageBox

from emo_master.apps.designer.state.device_catalog import DeviceCatalogEditor
from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.rtsp_migration import LEGACY_OPERATOR, MANAGED_OPERATOR, migrationDraft
from emo_master.apps.designer.ui.device_migration import DeviceMigrationDialog
from emo_master.apps.designer.ui.device_wizard import DeviceDefinitionDialog, DeviceNodeDialog, deviceErrorMessage
from emo_master.core.contracts.device_driver import DeviceError
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.designer.test_device_workspace import waitUntil
from tests.runtime.rtsp_fixture import definition, project, scan


def requirements(result):
    return {key: {slot: value.model_dump() for slot, value in descriptor.manifest.deviceRequirements.items()}
            for key, descriptor in result.activeOperators.items() if descriptor.manifest.deviceRequirements}


def windowFor(ownedDesignerWindow, tmp_path, monkeypatch, *, existing=False):
    result = scan()
    required = requirements(result)
    client = SimpleNamespace(listOperators=lambda: [], listDeviceTypes=lambda: (result.deviceTypes, required, {}))
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / 'ui.ini'), QSettings.IniFormat))
    definitions = {key: asdict(value.manifest) for key, value in result.activeOperators.items()}
    for key, value in definitions.items():
        value['deviceRequirements'] = required.get(key, {})
    monkeypatch.setattr(window, '_operatorDefinition', lambda key: definitions.get(key, {}))
    payload = project(legacy=True).model_dump(mode='json')
    if existing:
        payload['devices']['definitions']['camera'] = definition().model_dump()
    path = tmp_path / 'legacy-rtsp.emoproj'
    saveProject(path, payload)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    window.pageCoordinator.sync()
    return window, result, required, path


def ready(dialog):
    waitUntil(lambda: dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())


def testInventoryMasksSecretsAndCancelLeavesProjectUntouched(ownedDesignerWindow, tmp_path, monkeypatch):
    window, result, required, path = windowFor(ownedDesignerWindow, tmp_path, monkeypatch)
    c = window.pageCoordinator
    before, disk = c.session.payload(), path.read_bytes()
    inventory = DeviceMigrationDialog(c, required, window)
    assert len(inventory.rows) == 1
    assert inventory.rows[0][1]['operatorId'] == LEGACY_OPERATOR
    text = inventory.table.item(0, 3).text() + inventory.details.toPlainText()
    assert 'fixture-password' not in text and 'fixture-token' not in text
    assert 'camera.invalid' in text and '******' in text
    def cancel(dialog):
        ready(dialog)
        assert dialog.node['operatorId'] == MANAGED_OPERATOR
        assert dialog.slots['camera'][0].currentData() is None
        dialog.reject()
        return dialog.result()
    monkeypatch.setattr(DeviceNodeDialog, 'exec_', cancel)
    inventory.configureSelected()
    inventory.reject()
    assert c.session.payload() == before and path.read_bytes() == disk


@pytest.mark.parametrize('newDevice', [False, True])
def testMigrationPreservesPortsEdgesOverridesAndOneUndo(ownedDesignerWindow, tmp_path, monkeypatch, newDevice):
    window, result, required, path = windowFor(ownedDesignerWindow, tmp_path, monkeypatch, existing=not newDevice)
    c = window.pageCoordinator
    before, disk = c.session.payload(), path.read_bytes()
    old = next(n for n in before['workflows']['main']['nodes'] if n['nodeId'] == 'capture')
    prompts = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: prompts.append(args[2]) or QMessageBox.Yes)
    def create(dialog):
        assert dialog.forms['connection'].getValues()['password'] == 'fixture-password'
        assert dialog.forms['connection'].getValues()['openTimeoutMs'] == 1200
        assert dialog.forms['defaults'].getValues()['colorMode'] == 'grayscale'
        dialog.accept()
        assert dialog.result() == dialog.Accepted, dialog.error.text()
        return dialog.result()
    monkeypatch.setattr(DeviceDefinitionDialog, 'exec_', create)
    def complete(dialog):
        ready(dialog)
        assert not dialog.params._controls
        hint = dialog._sourceHints['camera']
        if newDevice:
            dialog._create('camera')
        else:
            combo = dialog.slots['camera'][0]
            combo.setCurrentIndex(combo.findData('camera'))
        assert dialog.sources['camera'].rows['colorMode'][0].currentData() == 'constant'
        assert hint.isHidden()
        if os.environ.get('QT_QPA_PLATFORM') == 'windows':
            dialog.setAttribute(Qt.WA_DontShowOnScreen)
            dialog.show()
            QApplication.processEvents()
            image = tmp_path / 'rtsp-node-migration.png'
            assert dialog.grab().save(str(image))
            print('RTSP_MIGRATION_SCREENSHOT=' + str(image))
        dialog.accept()
        assert dialog.result() == dialog.Accepted, dialog.error.text()
        return dialog.result()
    monkeypatch.setattr(DeviceNodeDialog, 'exec_', complete)
    inventory = DeviceMigrationDialog(c, required, window)
    inventory.configureSelected()
    inventory.reject()
    after = c.session.payload()
    node = next(n for n in after['workflows']['main']['nodes'] if n['nodeId'] == 'capture')
    assert node['operatorId'] == MANAGED_OPERATOR
    assert node['params'] == {} and not node['paramSchema']['properties']
    assert node['outputPorts'] == old['outputPorts']
    assert after['workflows']['main']['edges'] == before['workflows']['main']['edges']
    assert node['deviceOverrides']['camera']['colorMode'] == 'grayscale'
    assert after['dependencies']['operators'] == [dict(operatorId=MANAGED_OPERATOR, version='1.0.0')]
    selected = after['devices']['definitions'][node['deviceBindings']['camera']['deviceId']]
    if newDevice:
        assert selected['connection']['password'] == 'fixture-password'
        assert selected['connection']['readTimeoutMs'] == 300
    else:
        assert selected == before['devices']['definitions']['camera']
    assert 'fixture-password' not in '\n'.join(prompts) and 'fixture-token' not in '\n'.join(prompts)
    assert path.read_bytes() == disk
    WorkflowCompiler(result.activeOperators, deviceTypes=result.deviceTypes).compile(c.session.document())
    assert c.session.undo() and c.session.payload() == before
    assert c.session.redo() and c.session.payload() == after
    saved = tmp_path / 'managed-rtsp.emoproj'
    saveProject(saved, after)
    restored = loadProjectDocument(saved)
    assert restored.schemaVersion == '2.7'
    assert window.loadProjectDirectory(str(saved), confirmed=True)
    window.pageCoordinator.sync()
    reopened = next(n for n in window.pageCoordinator.session.document().workflows['main'].nodes if n.nodeId == 'capture')
    assert reopened.operatorId == MANAGED_OPERATOR and reopened.params == {}
    assert reopened.deviceBindings['camera'].deviceId == node['deviceBindings']['camera']['deviceId']


def testMigrationPreservesSharedLegacyDependencyAndRejectsIncompleteTransaction():
    result = scan()
    payload = project(legacy=True).model_dump(mode='json')
    old = payload['workflows']['main']['nodes'][1]
    payload['workflows']['main']['nodes'].append(dict(deepcopy(old), nodeId='other'))
    session = ProjectEditSession(payload, enablePresentation=False)
    catalog = DeviceCatalogEditor(session, result.deviceTypes, requirements(result))
    node = migrationDraft(old, asdict(result.activeOperators[LEGACY_OPERATOR].manifest))
    node['params'] = {}
    node['paramSchema'] = result.activeOperators[MANAGED_OPERATOR].manifest.paramSchema
    node['deviceBindings'] = {'camera': {'deviceId': 'camera'}}
    before = session.payload()
    with pytest.raises(ValueError, match='版本'):
        catalog.commitNode('main', node, newDevices={'camera': definition().model_dump()})
    assert session.payload() == before
    catalog.commitNode('main', node, newDevices={'camera': definition().model_dump()}, operatorVersion='1.0.0')
    assert {item['operatorId'] for item in session.workflows.dependencies['operators']} == {LEGACY_OPERATOR, MANAGED_OPERATOR}


def testVariableSourceMigrationAndNoSilentConnectionBindingLoss(ownedDesignerWindow, tmp_path, monkeypatch):
    window, result, required, _path = windowFor(ownedDesignerWindow, tmp_path, monkeypatch, existing=True)
    c = window.pageCoordinator
    with c.session.transaction():
        c.session.workflows.projectExtensions['globalVariables'] = {'color': dict(name='Color', type='string', initialValue='grayscale')}
        node = next(n for n in c.session.workflows.get('main').nodes if n['nodeId'] == 'capture')
        node['globalVariableBindings'] = [dict(parameterPath=['colorMode'], variableId='color')]
    old = next(n for n in c.session.payload()['workflows']['main']['nodes'] if n['nodeId'] == 'capture')
    draft = migrationDraft(old, asdict(result.activeOperators[LEGACY_OPERATOR].manifest))
    dialog = DeviceNodeDialog(c, draft, required[MANAGED_OPERATOR], window, migrateLegacy=True)
    ready(dialog)
    combo = dialog.slots['camera'][0]
    combo.setCurrentIndex(combo.findData('camera'))
    assert dialog.sources['camera'].rows['colorMode'][0].currentData() == 'variable'
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    dialog.accept()
    assert dialog.result() == dialog.Accepted, dialog.error.text()
    node = next(n for n in c.session.document().workflows['main'].nodes if n.nodeId == 'capture')
    assert not node.globalVariableBindings
    assert node.deviceValueBindings['camera']['colorMode'].variableId == 'color'
    assert 'colorMode' not in node.deviceOverrides.get('camera', {})
    from emo_master.apps.designer.state.device_catalog import migrateLegacySources
    draft['globalVariableBindings'] = [dict(parameterPath=['password'], variableId='color')]
    with pytest.raises(ValueError, match='password'):
        migrateLegacySources(draft, 'camera', 'camera', result.deviceTypes['device.camera.rtsp'], [])


def testRtspDeviceFormMasksPasswordAndProvidesUsefulErrors(retainedQtApplication, tmp_path):
    declaration = scan().deviceTypes['device.camera.rtsp']
    dialog = DeviceDefinitionDialog(declaration, initial=definition(connection={'password': 'fixture-mask'}).model_dump())
    try:
        assert dialog.forms['connection']._controls['password'].echoMode() == QLineEdit.Password
        assert not dialog.discoverButton.isEnabled()
        assert dialog.definition()['policies']['release'] == 'idle_timeout'
        for code in ('E_CAMERA_CONNECT', 'E_CAMERA_READ', 'E_CAMERA_FRAME_TIMEOUT'):
            assert deviceErrorMessage(DeviceError(code, 'detail')) != '设备操作未完成，请查看详情'
        if os.environ.get('QT_QPA_PLATFORM') == 'windows':
            dialog.setAttribute(Qt.WA_DontShowOnScreen)
            dialog.show()
            QApplication.processEvents()
            image = tmp_path / 'rtsp-device-form.png'
            assert dialog.grab().save(str(image))
            print('RTSP_UI_SCREENSHOT=' + str(image))
    finally:
        dialog.reject()
        dialog.deleteLater()


def testDeviceWorkspaceShowsBackgroundDisconnect(ownedDesignerWindow, tmp_path, monkeypatch):
    from emo_master.apps.designer.ui.device_workspace import DeviceWorkspace
    from emo_master.core.project.snapshots import revisionOf
    window, result, required, _path = windowFor(ownedDesignerWindow, tmp_path, monkeypatch, existing=True)
    dialog = DeviceWorkspace(window.pageCoordinator)
    try:
        dialog.deviceTypes, dialog.requirements = result.deviceTypes, required
        dialog.refresh()
        dialog.selectedId = 'camera'
        dialog.stateScope = dialog._scope()
        dialog.states = {'camera': dict(state='IDLE', queued=0, generation='generation', lifecycleConnected=True,
            definitionRevision=revisionOf(definition().model_dump()),
            driverState=dict(connectionState='FAULTED', lastError='RTSP read timed out'))}
        dialog._showStates()
        assert '连接故障' in dialog.table.item(0, 3).text()
        assert 'RTSP read timed out' in dialog.table.item(0, 6).text()
        assert '连接故障' in dialog.onlineState.toPlainText()
    finally:
        dialog.reject()
        dialog.shutdown()


def testFaceWhileMigrationPreservesPagesAndSubflowLinks(tmp_path):
    from tests.runtime.test_rtsp_face_project import fixtureProject
    _path, document, _registry, _image = fixtureProject(tmp_path)
    result = scan()
    session = ProjectEditSession(document.model_dump(mode='json'), enablePresentation=False)
    before = session.payload()
    old = next(node for node in before['workflows']['camera_once']['nodes'] if node['nodeId'] == 'source')
    node = migrationDraft(old, asdict(result.activeOperators[LEGACY_OPERATOR].manifest))
    node.update(params={}, paramSchema=result.activeOperators[MANAGED_OPERATOR].manifest.paramSchema,
                deviceBindings={'camera': {'deviceId': 'camera'}})
    editor = DeviceCatalogEditor(session, result.deviceTypes, requirements(result))
    editor.commitNode('camera_once', node, newDevices={'camera': definition().model_dump()}, operatorVersion='1.0.0')
    after = session.payload()
    assert after['presentation'] == before['presentation']
    assert after['workflows']['main'] == before['workflows']['main']
    assert after['workflows']['camera_tick'] == before['workflows']['camera_tick']
    assert after['workflows']['camera_once']['edges'] == before['workflows']['camera_once']['edges']
    WorkflowCompiler(result.activeOperators, deviceTypes=result.deviceTypes).compile(session.document())
