from copy import deepcopy
import socket
from time import monotonic

import grpc
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.clients.runtime.devices import DeviceManagementClient
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.runtime.test_managed_tcp import freePort, waitUntil
from tests.runtime.test_tcp_device_integration import definition


@pytest.fixture
def overviewRuntime(tmp_path):
    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    client = RuntimeClient(service)
    payload = WorkflowStore().toPayload()
    types = service.pluginScanResult.deviceTypes
    server = definition(types['device.tcp.server'], bindAddress='127.0.0.1', port=freePort(),
                        channels=[dict(channelId='station', name='Station', allowedIps=['127.0.0.1'])])
    server['policies']['connect'] = 'manual'
    camera = definition(types['device.simulated.camera'], serial='STATUS-SIMULATOR')
    camera['policies']['connect'] = 'manual'
    payload['devices']['definitions'] = dict(server=server, camera=camera)
    document = ProjectDocument.model_validate(payload)
    assert document.schemaVersion == '2.7'
    WorkflowCompiler().compile(document, 'main')
    path = tmp_path / 'status.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    assert loadProjectDocument(path) == document
    try:
        yield service, client, path
    finally:
        client.close()
        service.close()


def testOverviewReadOnlyAndUptimeAvailableBeforeProjectLoad(overviewRuntime):
    service, client, path = overviewRuntime
    service._startedMonotonic = monotonic() - 3600
    empty = client.getDeviceOverview('not-loaded')
    assert not empty['available'] and empty['code'] == 'E_CONFIG_NOT_LOADED'
    assert 3600000 <= empty['uptimeMs'] < 3602000
    assert service.loadedDocument is None and not service.deviceManager.drivers
    assert client.loadProject(str(path)).ok
    config = deepcopy(service.projectConfiguration.current)
    state = client.getDeviceOverview(service.loadedProjectId)
    assert state['available'] and len(state['states']) == 2
    assert state['states']['server']['connectionAddress']['bindAddress'] == '127.0.0.1'
    assert state['states']['camera']['connectionLifetime'] == 'persistent'
    assert not state['states']['camera']['lifecycleConnected']
    assert state['states']['server']['driverState']['state'] == 'STOPPED'
    assert service.projectConfiguration.current == config and not service.jobRepository.all()
    other = client.getDeviceOverview('another-project')
    assert not other['available'] and not other['states'] and other['code'] == 'E_CONFIG_PROJECT'
    assert other['uptimeMs'] >= state['uptimeMs']


def testLoopbackPeerDetailsAndDisconnectUseSameDeviceStates(overviewRuntime):
    service, client, path = overviewRuntime
    assert client.loadProject(str(path)).ok
    runtimeId, states = client.getDeviceStates(service.loadedProjectId)
    client.controlDevice(service.loadedProjectId, runtimeId, 'server', states['server'], 'connect', confirmed=True)
    manager = service.deviceManager.drivers['server'].backend
    with socket.create_connection(('127.0.0.1', manager.effective.tcpServer.port), timeout=2) as peer:
        waitUntil(lambda: bool(manager._peers))
        snapshot = client.getDeviceOverview(service.loadedProjectId)
        channel, = snapshot['states']['server']['driverState']['channels']
        assert channel['name'] == 'Station' and channel['connected']
        assert channel['peerAddress'] == list(peer.getsockname()) and channel['connectedAtMs'] > 0
    waitUntil(lambda: not manager._peers)
    snapshot = client.getDeviceOverview(service.loadedProjectId)
    channel, = snapshot['states']['server']['driverState']['channels']
    assert not channel['connected'] and channel['peerAddress'] is None
    assert snapshot['states']['server']['driverState']['state'] == 'LISTENING'


def testOverviewOverGrpcAndOldRuntimeWithoutUptime(overviewRuntime):
    service, _, path = overviewRuntime
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    with grpc.insecure_channel(f'127.0.0.1:{port}') as channel:
        client = RuntimeClient(runtime_pb2_grpc.RuntimeServiceStub(channel))
        try:
            assert client.getDeviceOverview('unloaded')['uptimeMs'] is not None
            assert client.loadProject(str(path)).ok
            snapshot = client.getDeviceOverview(service.loadedProjectId)
            assert snapshot['available'] and 'camera' in snapshot['states']
        finally:
            client.close()
            server.stop(0).wait()
    class OlderRuntime:
        def GetDeviceStates(self, request, context):
            return pb.GetDeviceStatesReply(ok=True, runtime_instance_id='older', project_id=request.project_id,
                                           states_json='{}')
    old = DeviceManagementClient(OlderRuntime()).getDeviceOverview('p')
    assert old['available'] and old['uptimeMs'] is None
