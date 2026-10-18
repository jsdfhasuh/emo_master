from copy import deepcopy
from pathlib import Path

import pytest
from PySide2.QtWidgets import QMessageBox, QPushButton

from emo_master.apps.designer.state.device_catalog import DeviceCatalogEditor
from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.plugin.devices import DeviceType
from emo_master.core.project.device_identity import matchingDevices
from emo_master.core.project.devices import DeviceDefinition
from tests.core.project.test_devices import declaration, definition  # noqa: F401
from tests.designer.test_device_workspace import windowFor, waitUntil


def builtin(name):
    root = Path(__file__).resolve().parents[2] / 'src/emo_master/plugins/builtins'
    paths = {'simulated': 'simulated_camera/device_types/camera.json',
             'plc': 'plc_slmp_read/device_types/slmp.json', 'camera': 'huaray_camera/device_types/imv.json'}
    return DeviceType.model_validate_json((root / paths[name]).read_text(encoding='utf-8'))


@pytest.mark.parametrize('name,connection,other', [
    ('simulated', dict(serial='TEST'), dict(serial='DIFFERENT')),
    ('plc', dict(host='192.0.2.1', port=10001), dict(host='192.0.2.2', port=10001)),
    ('camera', dict(cameraKey='KEY'), dict(cameraKey='OTHER'))])
def testDeclaredIdentityIgnoresDefaultsAndNeverMerges(name, connection, other):
    metadata = builtin(name)
    value = metadata.validateDefinition(DeviceDefinition(name='first', deviceTypeId=metadata.deviceTypeId,
        driverVersion=metadata.version, connection=connection)).model_dump()
    changed = deepcopy(value)
    changed.update(name='second', defaults={}, policies={})
    if name == 'plc':
        changed['connection'].update(host='::ffff:192.0.2.1', frameEncoding='ascii')
    if name == 'camera':
        changed['connection']['maxFrameBytes'] = 1024
    before = deepcopy(value)
    assert matchingDevices(changed, {'existing': value}, metadata) == ['existing']
    assert matchingDevices(changed, {'existing': value}, metadata, excluding='existing') == []
    changed['connection'].update(other)
    assert not matchingDevices(changed, {'existing': value}, metadata)
    assert value == before


def testUnmarkedOrIncompleteIdentityNeverGuessesAliases(declaration, definition):  # noqa: F811
    value = definition.model_dump()
    assert not matchingDevices(value, {'existing': value}, declaration)
    declaration.connectionSchema['properties']['source']['xDeviceIdentity'] = True
    incomplete = dict(value, connection={})
    assert not matchingDevices(incomplete, {'existing': incomplete}, declaration)


def testDuplicateCatalogEditIsAtomicAndOffersExistingId():
    metadata = builtin('simulated')
    session = ProjectEditSession(WorkflowStore().toPayload(), enablePresentation=False)
    editor = DeviceCatalogEditor(session, {metadata.deviceTypeId: metadata})
    value = DeviceDefinition(name='original', deviceTypeId=metadata.deviceTypeId, driverVersion=metadata.version,
                             connection=dict(serial='SAME')).model_dump()
    editor.put(value, deviceId='existing')
    before, undo = session.payload(), len(session._undo)
    with pytest.raises(ValueError, match='existing'):
        editor.put(dict(value, name='different policy'), deviceId='duplicate')
    assert session.payload() == before and len(session._undo) == undo
    node = dict(nodeId='capture', operatorId='vision.io.simulated_camera',
                deviceBindings=dict(camera=dict(deviceId='duplicate')))
    with pytest.raises(ValueError, match='existing'):
        editor.commitNode('main', node, newDevice=value, deviceId='duplicate')
    assert session.payload() == before and len(session._undo) == undo


@pytest.mark.parametrize('confirm', [False, True])
def testNodeWizardExplicitlyReusesWithoutOverwritingDefaults(ownedDesignerWindow, tmp_path,
        declaration, definition, monkeypatch, confirm):  # noqa: F811
    from emo_master.apps.designer.ui import device_wizard as module
    declaration.connectionSchema['properties']['source']['xDeviceIdentity'] = True
    window, requirements = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    node = dict(nodeId='new', operatorId='vision.io.huaray_camera', params={}, paramSchema={})
    dialog = module.DeviceNodeDialog(c, node, requirements[node['operatorId']], window)
    try:
        waitUntil(lambda: bool(dialog.types))
        before = c.session.payload()
        candidate = definition.model_dump()
        candidate['defaults']['exposureUs'] = 12000
        class DefinitionDialog:
            Accepted = 1
            value = candidate
            def __init__(self, *args, **kwargs):
                pass
            def exec_(self):
                return self.Accepted
        monkeypatch.setattr(module, 'DeviceDefinitionDialog', DefinitionDialog)
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes if confirm else QMessageBox.No)
        next(button for button in dialog.findChildren(QPushButton) if button.text() == '新建设备').click()
        assert not dialog.newDefinitions
        assert c.session.payload() == before
        if confirm:
            assert dialog.slots['camera'][0].currentData() == 'camera'
        else:
            assert dialog.slots['camera'][0].currentData() is None
    finally:
        dialog.reject()
