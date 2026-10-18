from copy import deepcopy
import ctypes
import json

import grpc
import pytest

from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.devices.network import executeNetwork, networkAddress
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.plugins.builtins._huaray_device_info import DeviceInfo, GigEDeviceInfo, UsbDeviceInfo
from emo_master.plugins.builtins._huaray_imv import ImvApi, IMV_DeviceList, HuarayCameraError
from tests.runtime.test_managed_imv import cameraProject


ROW = dict(cameraKey='FAKE-KEY', cameraType=0, modelName='Synthetic', serialNumber='test-only',
    macAddress='00:11:22:33:44:55', ipAddress='192.0.2.10', subnetMask='255.255.255.0', defaultGateWay='0.0.0.0',
    interfaceIp='192.0.2.1')


def testReviewedHeaderLayoutAndSecondArrayElement():
    assert ctypes.sizeof(GigEDeviceInfo) == 3092
    assert ctypes.sizeof(UsbDeviceInfo) == 3096
    assert ctypes.sizeof(DeviceInfo) == 10312
    assert DeviceInfo.DeviceSpecificInfo.offset == 3096
    assert DeviceInfo.nInterfaceType.offset == 6192
    assert DeviceInfo.InterfaceInfo.offset == 7752
    entries = (DeviceInfo * 2)()
    for index, key in enumerate((b'first', b'second')):
        entries[index].cameraKey = key
        entries[index].DeviceSpecificInfo.gigeDeviceInfo.ipAddress = b'192.0.2.10'
    api = ImvApi.__new__(ImvApi)
    api._getVersion = lambda: b'2.4.2.3'
    def enumerate_(pointer, _kind):
        listing = ctypes.cast(pointer, ctypes.POINTER(IMV_DeviceList)).contents
        listing.nDevNum = 2
        listing.pDevInfo = entries
        return 0
    api._enumDevices = enumerate_
    assert [row['cameraKey'] for row in api.enumerate_device_info()] == ['first', 'second']
    api._getVersion = lambda: b'unsupported'
    with pytest.raises(HuarayCameraError, match='reviewed'):
        api.enumerate_device_info()


@pytest.mark.parametrize('count', [1, 101])
def testMalformedNativeListRejectedBeforeDereferencing(count):
    api = ImvApi.__new__(ImvApi)
    api._getVersion = lambda: b'2.4.2.3'
    def enumerate_(pointer, _kind):
        ctypes.cast(pointer, ctypes.POINTER(IMV_DeviceList)).contents.nDevNum = count
        return 0
    api._enumDevices = enumerate_
    with pytest.raises(HuarayCameraError, match='invalid SDK device list'):
        api.enumerate_device_info()


class Api:
    def __init__(self, confirm=True):
        self.row = deepcopy(ROW)
        self.writes = self.destroyed = 0
        self.confirm = confirm

    def version(self):
        return 'synthetic'

    def enumerate_device_info(self):
        return [deepcopy(self.row)]

    def create_handle(self, mode, key):
        assert mode == 1 and key == 'FAKE-KEY'
        return 0, 'handle'

    def force_ip_address(self, handle, ip, mask, gateway):
        self.writes += 1
        if self.confirm:
            self.row.update(ipAddress=ip, subnetMask=mask, defaultGateWay=gateway)
        return 0

    def destroy_handle(self, handle):
        self.destroyed += 1


def command():
    return dict(cameraKey='FAKE-KEY', before=deepcopy(ROW), ipAddress='192.0.2.20',
                subnetMask='255.255.255.0', defaultGateWay='0.0.0.0')


def testSpawnedNetworkHostUsesOnlyInjectedApi():
    from emo_master.apps.runtime.devices.network import runNetworkHost
    result = runNetworkHost('discover', {}, lambda: False, apiFactory=Api)
    assert result['devices'] == [ROW]
    result = runNetworkHost('force_ip', command(), lambda: False, apiFactory=Api)
    assert result['after']['ipAddress'] == '192.0.2.20'


def testForceIpChecksMacAndReadbackWithoutOpeningOrCapturing():
    api = Api()
    changed = command()
    changed['before']['macAddress'] = 'wrong'
    with pytest.raises(DeviceError, match='identity changed'):
        executeNetwork(api, 'force_ip', changed)
    assert api.writes == 0
    assert executeNetwork(api, 'force_ip', command())['after']['ipAddress'] == '192.0.2.20'
    assert api.writes == api.destroyed == 1
    uncertain = Api(confirm=False)
    with pytest.raises(DeviceError) as result:
        executeNetwork(uncertain, 'force_ip', command())
    assert result.value.outcome == 'UNKNOWN' and uncertain.writes == uncertain.destroyed == 1


def testOtherInterfaceCollisionRejectedAndReleaseFailureIsUnknown():
    api = Api()
    api.enumerate_device_info = lambda: [deepcopy(ROW), dict(ROW, cameraKey='other', interfaceIp='192.0.2.20')]
    with pytest.raises(DeviceError, match='already used'):
        executeNetwork(api, 'force_ip', command())
    assert api.writes == 0
    api = Api()
    api.destroy_handle = lambda _handle: -1
    with pytest.raises(DeviceError) as result:
        executeNetwork(api, 'force_ip', command())
    assert result.value.outcome == 'UNKNOWN' and api.writes == 1


@pytest.mark.parametrize('fields', [dict(ipAddress='192.0.2.0'), dict(ipAddress='224.0.0.1'),
    dict(subnetMask='255.0.255.0'), dict(defaultGateWay='198.51.100.1')])
def testInvalidNetworkAddressRejected(fields):
    with pytest.raises(ValueError):
        networkAddress(dict(command(), **fields))


@pytest.mark.parametrize('unknown', [False, True])
def testNetworkMutationRequiresPausedIdentityAndDurableAtMostOnceReceipt(tmp_path, unknown):
    path = tmp_path / 'project.json'
    path.write_text(json.dumps(cameraProject()), encoding='utf-8')
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    calls = []
    def runner(action, payload, cancelled):
        calls.append(action)
        if action == 'discover':
            return dict(devices=[deepcopy(ROW)], sdkVersion='synthetic')
        with runtime.sqliteStore.connection() as connection:
            receipt = json.loads(connection.execute('SELECT result FROM deviceNetworkReceipts').fetchone()[0])
        assert receipt['state'] == 'UNKNOWN'
        if unknown:
            raise DeviceError('E_NETWORK_OUTCOME_UNKNOWN', 'synthetic lost receipt', outcome='UNKNOWN')
        return executeNetwork(Api(), action, payload)
    try:
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        before = deepcopy(runtime.loadedPayload)
        runtime.deviceNetwork.runner = runner
        grpc.channel_ready_future(channel).result(5)
        client = RuntimeClient(runtime_pb2_grpc.RuntimeServiceStub(channel))
        project, instance = runtime.loadedProjectId, runtime.runtimeInstanceId
        discovered = client.deviceNetworkCommand(project, instance, 'discover')
        state = json.loads(runtime.GetDeviceStates(pb.GetDeviceStatesRequest(project_id=project), None).states_json)['camera']
        payload = dict(discoveryId=discovered['discoveryId'], cameraKey='FAKE-KEY', ipAddress='192.0.2.20',
            subnetMask='255.255.255.0', defaultGateWay='0.0.0.0', confirmed=True,
            expectedGeneration=state['generation'], expectedDefinitionRevision=state['definitionRevision'])
        def send(value=payload):
            return client.deviceNetworkCommand(project, instance, 'force_ip', deviceId='camera', requestId='change', payload=value)
        with pytest.raises(RuntimeClientError, match='disconnect'):
            send()
        assert calls == ['discover']
        executor, operation = runtime.deviceManager.beginControl('camera', state['generation'], 'disconnect')
        executor.wait(operation)
        result = send()
        assert result['state'] == ('UNKNOWN' if unknown else 'COMPLETED')
        assert send() == result and calls == ['discover', 'force_ip']
        with pytest.raises(RuntimeClientError, match='different content'):
            send(dict(payload, ipAddress='192.0.2.30'))
        assert runtime.loadedPayload == before
        assert runtime.deviceManager.lifecycles['camera'].paused
        assert not runtime.deviceManager.maintenanceOwners
        assert not runtime.deviceManager.drivers['camera'].process
    finally:
        channel.close()
        server.stop(0).wait(5)
        runtime.close()
    restored = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    try:
        client = RuntimeClient(restored)
        assert client.deviceNetworkCommand(restored.loadedProjectId, restored.runtimeInstanceId, 'status', requestId='change') == result
    finally:
        restored.close()


@pytest.fixture
def networkRuntime(tmp_path):
    path = tmp_path / 'project.json'
    path.write_text(json.dumps(cameraProject()), encoding='utf-8')
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    try:
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        runtime.deviceNetwork.runner = lambda *_: dict(devices=[deepcopy(ROW)], sdkVersion='synthetic')
        client = RuntimeClient(runtime)
        snapshot = client.deviceNetworkCommand(runtime.loadedProjectId, runtime.runtimeInstanceId, 'discover')
        state = json.loads(runtime.GetDeviceStates(pb.GetDeviceStatesRequest(project_id=runtime.loadedProjectId), None).states_json)['camera']
        executor, operation = runtime.deviceManager.beginControl('camera', state['generation'], 'disconnect')
        executor.wait(operation)
        payload = dict(discoveryId=snapshot['discoveryId'], cameraKey='FAKE-KEY', ipAddress='192.0.2.20',
            subnetMask='255.255.255.0', defaultGateWay='0.0.0.0', confirmed=True,
            expectedGeneration=state['generation'], expectedDefinitionRevision=state['definitionRevision'])
        yield runtime, payload
    finally:
        runtime.close()


@pytest.mark.parametrize('fence', ['expired', 'project', 'generation', 'definition', 'confirmation', 'primary'])
def testAdmissionFencesNeverCallNativeMutation(networkRuntime, fence):
    runtime, payload = networkRuntime
    calls = []
    runtime.deviceNetwork.runner = lambda *args: calls.append(args)
    discovery = runtime.deviceNetwork.discovery
    if fence == 'expired':
        runtime.deviceNetwork.discovery = (discovery[0] - 31, discovery[1])
    elif fence == 'project':
        discovery[1]['projectId'] = 'other'
    elif fence == 'generation':
        payload['expectedGeneration'] = 'stale'
    elif fence == 'definition':
        payload['expectedDefinitionRevision'] = 'stale'
    elif fence == 'confirmation':
        payload['confirmed'] = False
    else:
        runtime._runtimeDataLockIsPrimary = False
    try:
        with pytest.raises(RuntimeClientError):
            RuntimeClient(runtime).deviceNetworkCommand(runtime.loadedProjectId, runtime.runtimeInstanceId,
                'force_ip', deviceId='camera', requestId='fenced', payload=payload)
        assert not calls and not runtime.deviceManager.maintenanceOwners
        assert runtime.deviceNetwork._receipt(runtime.loadedProjectId, 'fenced', missing=True) is None
    finally:
        runtime._runtimeDataLockIsPrimary = True


def testStrandedHostRetainsMaintenanceUntilActualExit(networkRuntime):
    runtime, payload = networkRuntime
    class Process:
        alive = True
        closed = False
        def is_alive(self):
            return self.alive
        def terminate(self):
            pass
        def join(self, timeout):
            pass
        def close(self):
            assert not self.alive
            self.closed = True
    process = Process()
    def stranded(*_):
        error = DeviceError('E_DEVICE_RELEASE_PENDING', 'synthetic stranded Host', outcome='UNKNOWN')
        error.process = process
        raise error
    runtime.deviceNetwork.runner = stranded
    result = RuntimeClient(runtime).deviceNetworkCommand(runtime.loadedProjectId, runtime.runtimeInstanceId,
        'force_ip', deviceId='camera', requestId='stranded', payload=payload)
    assert result['state'] == 'UNKNOWN'
    assert runtime.deviceManager.maintenanceOwners and runtime.deviceManager.lifecycles['camera'].controlPending
    with pytest.raises(DeviceError, match='still owns hardware'):
        runtime.deviceNetwork.close()
    assert runtime.deviceManager.maintenanceOwners
    process.alive = False
    runtime.deviceNetwork.close()
    assert process.closed and not runtime.deviceManager.maintenanceOwners
    assert not runtime.deviceManager.lifecycles['camera'].controlPending
