import base64
import hashlib

import pytest

from emo_master.apps.designer.state.device_package import exportDevices, importDevices
from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from tests.core.project.test_devices import declaration, definition, project  # noqa: F401


def testPortableDeviceResourcesSiteFieldsAndAtomicHistory(tmp_path, declaration, definition):  # noqa: F811
    declaration = declaration.model_copy(deep=True)
    declaration.defaultsSchema['properties']['modelPath'] = {'type': 'string'}
    definition = definition.model_copy(deep=True)
    definition.defaults['modelPath'] = 'private/source.bin'
    payload = project(definition)
    data = b'synthetic device resource'
    (tmp_path / 'model.bin').write_bytes(data)
    payload['resources'] = dict(items={'model': dict(path='model.bin', size=len(data),
        sha256=hashlib.sha256(data).hexdigest(), purpose='model')},
        parameterBindings=[dict(resourceId='model', target=dict(deviceId='camera', section='defaults', parameterPath=['modelPath']))],
        siteBindings=[dict(field='device.source', purpose='camera_serial', target=dict(deviceId='camera', section='connection', parameterPath=['source']))])
    session = ProjectEditSession(payload)
    before = session.payload()
    package = exportDevices(session, ['camera'], tmp_path)
    assert package.devices['camera'].connection['source'] == ''
    assert base64.b64decode(package.resourceData['model']) == data
    assert 'private/source.bin' not in package.model_dump_json()
    ids = importDevices(session, package, {declaration.deviceTypeId: declaration}, tmp_path)
    imported = ids['camera']
    document = session.document()
    assert not document.devices.definitions[imported].enabled
    assert document.devices.definitions['camera'] == definition
    newBinding = next(binding for binding in document.resources.parameterBindings if binding.target.deviceId == imported)
    item = document.resources.items[newBinding.resourceId]
    assert (tmp_path / item.path).read_bytes() == data
    assert document.devices.definitions[imported].defaults['modelPath'] == item.path
    assert session.undo() and session.payload() == before
    assert session.redo() and imported in session.document().devices.definitions


def testInvalidPackageDoesNotChangeDraft(tmp_path, declaration, definition):  # noqa: F811
    session = ProjectEditSession(project(definition))
    package = exportDevices(session, ['camera'], tmp_path)
    before = session.payload()
    package.devices['camera'].defaults['exposureUs'] = 'invalid-number'
    with pytest.raises(ValueError):
        importDevices(session, package, {declaration.deviceTypeId: declaration}, tmp_path)
    assert session.payload() == before and not session._undo
