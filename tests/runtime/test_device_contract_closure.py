"""Third-party device contracts: action namespaces, health and generic forms."""
import json
import multiprocessing
from pathlib import Path
from threading import Event
from time import monotonic

import numpy as np
import pytest

from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.devices.ipc import DeviceBridge, DeviceProxy
from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.core.plugin.devices import DeviceType
from emo_master.core.project.models import ProjectDocument
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from tests.runtime.test_tcp_device_integration import definition


def tickerType():
    root = Path(__file__).resolve().parents[2]
    value = json.loads((root/'examples/device_service/device_types/ticker.json').read_text(encoding='utf-8'))
    value['capabilities'].append('release')
    value['actionSchemas']['release'] = dict(type='object', properties={}, additionalProperties=False)
    return DeviceType.model_validate(value)


@pytest.fixture
def serviceManager():
    declaration = tickerType()
    manager = DeviceManager({declaration.deviceTypeId: declaration})
    payload = WorkflowStore().toPayload()
    payload['devices']['definitions'] = dict(ticker=definition(declaration))
    manager.load(ProjectDocument.model_validate(payload))
    manager.authorize('owner', ['ticker'])
    manager.lifecycles['ticker'].connect(lambda: False, monotonic()+5)
    yield manager
    manager.retire('owner')
    manager.close()


def testF06BusinessReleaseAndFrameworkFrameReleaseHaveDifferentRoutes(serviceManager):
    manager = serviceManager
    parent, child = multiprocessing.Pipe()
    cancellation = Event()
    bridge = DeviceBridge(manager, 'owner', parent, cancellation)
    proxy = DeviceProxy(child, cancellation)
    bridge.thread.start()
    try:
        result = proxy.call('release', deviceId='ticker', params={}, cycleId='cycle')
        assert result['ticks'] >= 0
        reservation = manager.frames.reserve('owner', 4)
        frame = manager.frames.publish(reservation, np.zeros((2, 2), dtype=np.uint8),
            cycleId='cycle', generation=manager.executors['ticker'].generation, requestId='frame')
        proxy.framework('release', frameId=frame['frameId'])
        assert not manager.frames.frames
        with pytest.raises(DeviceError, match='kind'):
            proxy._call('unknown_kind', 'sample')
    finally:
        proxy.close()
        bridge.close()


@pytest.mark.parametrize('error', [DeviceError('E_FATAL', 'unhealthy service', unhealthy=True), RuntimeError('driver failed')])
def testF07UnhealthyServiceUsesCanonicalQuarantineAndCanRecover(serviceManager, error):
    manager = serviceManager
    driver = manager.drivers['ticker']
    def fail(*_args, **_kwargs):
        raise error
    driver.execute = fail
    with pytest.raises(type(error)):
        manager.execute('owner', 'ticker', 'sample', {}, cycleId='cycle')
    executor = manager.executors['ticker']
    assert executor.quarantined and str(error) in executor.lastError
    with pytest.raises(DeviceError, match='quarantined'):
        manager.execute('owner', 'ticker', 'sample', {}, cycleId='cycle')
    assert not manager.services['ticker'].busy()
    manager.retire('owner')
    manager.recover('ticker', executor.generation)
    manager.authorize('owner', ['ticker'])
    manager.lifecycles['ticker'].connect(lambda: False, monotonic()+5)
    assert manager.execute('owner', 'ticker', 'sample', {}, cycleId='new-cycle')['ticks'] >= 0


def testF07BusinessRejectionLeavesServiceUsable(serviceManager):
    manager = serviceManager
    with pytest.raises(DeviceError, match='采样被拒绝'):
        manager.execute('owner', 'ticker', 'sample', {}, cycleId='cycle', arguments=dict(reject=True))
    assert not manager.executors['ticker'].quarantined
    assert manager.execute('owner', 'ticker', 'sample', {}, cycleId='cycle')['ticks'] >= 0


def testF08OrdinaryChannelsUseGenericCreateEditAndSave(retainedQtApplication):
    from emo_master.apps.designer.ui.device_wizard import DeviceDefinitionDialog, configurationForm
    from emo_master.apps.designer.ui.param_form import SchemaParamForm
    declaration = tickerType()
    declaration.connectionSchema = dict(type='object', properties=dict(channels=dict(type='array', default=[],
        items=dict(type='object', properties=dict(id=dict(type='string'), name=dict(type='string')),
                   required=['id', 'name'], additionalProperties=False))), additionalProperties=False)
    value = definition(declaration, channels=[dict(id='a', name='A')])
    dialog = DeviceDefinitionDialog(declaration, initial=value)
    try:
        assert type(dialog.forms['connection']) is SchemaParamForm
        dialog.accept()
        assert dialog.value['connection']['channels'] == value['connection']['channels']
        form = configurationForm(declaration.connectionSchema, dialog.value['connection'],
            deviceTypeId=declaration.deviceTypeId, section='connection')
        assert type(form) is SchemaParamForm
        assert form.getValues() == dialog.value['connection']
        form.close()
    finally:
        dialog.reject()
