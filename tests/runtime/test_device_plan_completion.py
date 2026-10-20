"""Normal setup and camera diagnostic paths using simulated hardware only."""
import time
import json
import pytest

from emo_master.apps.runtime.devices.imv_capture import ImvCapture
from emo_master.apps.runtime.devices.imv_host import ManagedImv
from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_managed_imv import ROOT, definition
from tests.plugins.test_camera_configuration import ConfigurationApi
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.imv_test_support import SimulatedImvRuntimeService


def testActualReadDoesNotAcquireAndTrialRestoresNativeValues():
    api = ConfigurationApi()
    d = definition()
    camera = ImvCapture(d.connection, lambda: api)
    try:
        actual = camera.readParameters(d.defaults, lambda: False, time.monotonic() + 5)
        assert actual['actual']['ExposureTime'] > 0
        assert not api.commands and camera.params is None
        camera.capture(d.defaults, lambda: False, time.monotonic() + 5, 1024)
        before = camera.readParameters(d.defaults, lambda: False, time.monotonic() + 5)['actual']
        image, result = camera.trialCapture(d.defaults, dict(exposureMode='manual', exposureUs=12345., gainMode='manual', gainRaw=2.),
                                          lambda: False, time.monotonic() + 5, 1024)
        assert image.shape == (3, 4, 3)
        assert result['actualExposureUs'] == 12345.
        after = camera.readParameters(d.defaults, lambda: False, time.monotonic() + 5)['actual']
        for key in ('ExposureAuto', 'ExposureTime', 'GainAuto', 'GainRaw'):
            assert before[key] == after[key]
        assert camera.params == d.defaults
    finally:
        camera.close()


def testSpawnReadAndTrialUseSameHostAndReleaseMemory():
    d = definition()
    driver = ManagedImv(d, apiFactory=ConfigurationApi)
    try:
        read = driver.execute('read_parameters', d.defaults, lambda: False, time.monotonic() + 15)
        pid = driver.process.pid
        assert read['actual'] and driver.memory is None
        image, result = driver.execute('trial_capture', d.defaults, lambda: False, time.monotonic() + 15,
            changes=dict(exposureMode='manual', exposureUs=12000.))
        assert result['restored'] and result['actualExposureUs'] == 12000.
        assert image.size and driver.process.pid == pid and driver.memory is None
    finally:
        driver.close()
    assert driver.process is None


def testCancelledTrialRestoresActualValuesAndCachedConfiguration():
    from emo_master.apps.runtime.devices.executor import DeviceError
    api = ConfigurationApi()
    d = definition()
    camera = ImvCapture(d.connection, lambda: api)
    try:
        camera.capture(d.defaults, lambda: False, time.monotonic() + 5, 1024)
        before = camera.readParameters(d.defaults, lambda: False, time.monotonic() + 5)['actual']
        with pytest.raises(DeviceError):
            camera.trialCapture(d.defaults, dict(exposureMode='manual', exposureUs=12345.),
                                lambda: api.doubles['ExposureTime'] == 12345., time.monotonic() + 5, 1024)
        after = camera.readParameters(d.defaults, lambda: False, time.monotonic() + 5)['actual']
        for key in ('ExposureAuto', 'ExposureTime', 'GainAuto', 'GainRaw'):
            assert before[key] == after[key]
        assert camera.params == d.defaults and not camera.quarantined
    finally:
        camera.close()


def testFirstTrialRestoresDeviceActualValuesNotProjectDefaults():
    api = ConfigurationApi()
    api.enums.update(ExposureAuto='Off', GainAuto='Off', TriggerMode='Off')
    api.doubles.update(ExposureTime=5678., GainRaw=1.)
    d = definition()
    camera = ImvCapture(d.connection, lambda: api)
    try:
        before = camera.readParameters(d.defaults, lambda: False, time.monotonic() + 5)['actual']
        assert camera.params is None and not camera.session.grabbing
        image, result = camera.trialCapture(d.defaults, dict(exposureMode='manual', exposureUs=12345.),
                                            lambda: False, time.monotonic() + 5, 1024)
        assert image.size and result['actualExposureUs'] == 12345.
        after = camera.readParameters(d.defaults, lambda: False, time.monotonic() + 5)['actual']
        assert after == before
        assert camera.params is None and not camera.session.grabbing
    finally:
        camera.close()


def testConnectionTestDoesNotPublishAndReusesExistingOwner():
    types, rejected = PluginRegistry('0.6.1').scanDeviceTypes([ROOT])
    assert not rejected
    manager = DeviceManager(types)
    payload = simulatedProject()
    d = payload['devices']['definitions']['camera']
    try:
        result = manager.testConnection(d)
        assert result['connected'] and not result['reused']
        assert not manager.definitions and not manager.executors and not manager.maintenanceOwners
        manager.load(ProjectDocument.model_validate(payload))
        executor = manager.executors['camera']
        manager.lifecycles['camera'].connect(lambda: False, time.monotonic() + 5)
        driver = manager.drivers['camera']
        result = manager.testConnection(d)
        assert result['reused'] and result['deviceId'] == 'camera'
        assert manager.executors['camera'] is executor and manager.drivers['camera'] is driver
        assert manager.lifecycles['camera'].connected
        assert not manager.permissions and not manager.maintenanceOwners
    finally:
        manager.close()


def testRemoteSetupAndCameraReadTrialUseProductionRpc(tmp_path):
    import grpc
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc
    from emo_master.apps.runtime.main import createRuntimeServer
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from tests.runtime.test_managed_imv import cameraProject
    service = SimulatedImvRuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    client = RuntimeClient(runtime_pb2_grpc.RuntimeServiceStub(channel))
    try:
        info = client.deviceNetworkCommand('', '', 'info')
        assert info['deviceSetupVersion'] == 1
        simulated = simulatedProject()['devices']['definitions']['camera']
        result = client.deviceNetworkCommand(info['projectId'], info['runtimeInstanceId'], 'test_connection',
            payload=dict(definition=simulated, confirmed=True))
        assert result['connected'] and not service.deviceManager.definitions
        assert service.projectConfiguration.current is None
        path = tmp_path / 'camera.emoproj'
        path.write_text(json.dumps(cameraProject()), encoding='utf-8')
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        runtimeId, states = client.getDeviceStates(service.loadedProjectId)
        args = (service.loadedProjectId, runtimeId, 'camera', states['camera'])
        opened = client.cameraDiagnostic(*args, 'open')
        session = opened['sessionId']
        assert opened['metadata']['cameraTuningVersion'] == 1
        before = client.getOnlineProjectSnapshot()
        read = client.cameraDiagnostic(*args, 'read_parameters', sessionId=session, sequence=1, confirmed=True)
        assert read['metadata']['actual']['ExposureTime'] > 0 and not read['preview']
        result = client.cameraDiagnostic(*args, 'trial_capture', sessionId=session, sequence=2, confirmed=True,
            changes=dict(exposureMode='manual', exposureUs=15000.))
        assert result['preview'] and result['metadata']['acquisition']['restored']
        assert result['metadata']['acquisition']['actualExposureUs'] == 15000.
        client.cameraDiagnostic(*args, 'close', sessionId=session)
        after = client.getOnlineProjectSnapshot()
        assert before == after
        assert not service.deviceManager.maintenanceOwners
    finally:
        client.close()
        channel.close()
        server.stop(0).wait(5)
        service.close()
