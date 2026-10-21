"""Normal-path regressions for the TCP device integration closure."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import time

import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.configuration import requiredProjectChanges, selectedCandidate
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_tcp_device_integration import definition, graph, node, edge


BUILTINS = Path(__file__).resolve().parents[2] / 'src/emo_master/plugins/builtins'


@pytest.fixture
def thirdParty(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    root = tmp_path / 'closure_plugin'
    root.mkdir()
    (root / '__init__.py').write_text('''
from dataclasses import replace
from emo_master.plugins.builtins._communication_operators import TcpClientOperator
class ThirdPartyTcp(TcpClientOperator):
    meta = replace(TcpClientOperator.meta, operatorId='closure.tcp.client')
''', encoding='utf-8')
    manifest = json.loads((BUILTINS / 'tcp_client/manifest.json').read_text(encoding='utf-8'))
    manifest.update(operatorId='closure.tcp.client', entry='closure_plugin:ThirdPartyTcp')
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return root


@pytest.fixture
def runtime(tmp_path, thirdParty):
    service = RuntimeService(dbPath=tmp_path/'runtime/runtime.db', workspaceRoot=tmp_path/'runtime/jobs',
        pluginRootPaths=(str(BUILTINS), str(thirdParty)))
    client = RuntimeClient(runtimeService=service)
    yield service, client
    client.close()
    service.close()


def load(service, payload, tmp_path):
    project = tmp_path/'closure.emoproj'
    project.write_text(json.dumps(payload), encoding='utf-8')
    result = service.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None)
    assert result.ok, result.message


def testF02NewServerReferenceIsAddedBeforeSelectorValidation(runtime):
    service, _ = runtime
    online = WorkflowStore().toPayload()
    local = deepcopy(online)
    local['devices']['definitions']['new-server'] = definition(service.pluginScanResult.deviceTypes['device.tcp.server'],
        channels=[dict(channelId='a', name='A', allowedIps=['127.0.0.1'])])
    local['workflows']['main']['nodes'].append(node('acquire', 'communication.tcp.session', 'new-server', selectors=dict(channelId='a')))
    required = requiredProjectChanges(online, local, ['/workflows/main/nodes/acquire'])
    assert set(required) == {'/devices/definitions/new-server'}


@pytest.mark.parametrize('newDevice', [True, False])
def testF02SelectorDependencyUsesDeviceDeclaration(runtime, newDevice):
    service, _ = runtime
    types = service.pluginScanResult.deviceTypes
    online = WorkflowStore().toPayload()
    # Use a third-party selector name and collection path to rule out channelId special casing.
    declaration = types['device.tcp.server'].model_copy(deep=True)
    declaration.bindingSelectors = {'station': dict(itemsPath='connection.stations', valueField='id', labelField='name')}
    device = definition(types['device.tcp.server'], channels=[])
    device['connection'] = dict(stations=[dict(id='a', name='A')])
    if not newDevice:
        online['devices']['definitions']['server'] = device
    local = deepcopy(online)
    local['devices']['definitions']['server'] = deepcopy(device)
    local['devices']['definitions']['server']['connection']['stations'].append(dict(id='b', name='B'))
    local['workflows']['main']['nodes'].append(node('bind', 'closure.selector', 'server', selectors=dict(station='b')))
    local['project']['name'] = 'UNSELECTED'
    required = requiredProjectChanges(online, local, ['/workflows/main/nodes/bind'],
        deviceTypes={declaration.deviceTypeId: declaration}, operatorRegistry={'closure.selector': dict(deviceRequirements={
            'tcp': dict(deviceTypeId=declaration.deviceTypeId, requiredCapabilities=[],
                selectorSchema=dict(type='object', properties={'station': dict(type='string')}, required=['station'], additionalProperties=False))})})
    path = '/devices/definitions/server' + ('' if newDevice else '/connection/stations')
    assert set(required) == {path}
    candidate = selectedCandidate(online, local, ['/workflows/main/nodes/bind', *required])
    assert candidate['project'] == online['project']


@pytest.mark.parametrize('nested', [False, True])
def testF03ThirdPartyTcpGetsBridgeInSpawnedJob(runtime, tmp_path, nested):
    service, client = runtime
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(2)
        payload = WorkflowStore().toPayload()
        payload['devices']['definitions'] = dict(tcp=definition(service.pluginScanResult.deviceTypes['device.tcp.client_once'],
            port=listener.getsockname()[1]))
        payload['workflows']['main'] = graph([node('send', 'closure.tcp.client', 'tcp', params=dict(text='CLOSURE', operation='send'))],
            [edge('send', 'sentBytes', 'output', 'sentBytes')], outputs=dict(sentBytes='integer'))
        if nested:
            payload['workflows']['child'] = payload['workflows']['main']
            payload['workflows']['main'] = graph([dict(nodeId='child', kind='subflow', targetWorkflowId='child',
                inputPorts={}, outputPorts=dict(sentBytes='integer'))], [edge('child', 'sentBytes', 'output', 'sentBytes')],
                outputs=dict(sentBytes='integer'))
            payload['workflowOrder'].append('child')
        load(service, payload, tmp_path)
        started = client.startJob(service.loadedProjectId, 'main')
        assert started.ok, started
        assert waitForTerminal(service, started.job_id).status == 'COMPLETED'
        with listener.accept()[0] as peer:
            assert peer.recv(100) == b'CLOSURE'


def testF02NewServerAndBoundNodeSelectedPublicationRuns(runtime, tmp_path):
    from tests.runtime.test_tcp_device_integration import publish, run, freePort
    service, client = runtime
    load(service, WorkflowStore().toPayload(), tmp_path)
    before = client.getOnlineProjectSnapshot()['payload']
    local = deepcopy(before)
    value = definition(service.pluginScanResult.deviceTypes['device.tcp.server'], port=freePort(),
        channels=[dict(channelId='a', name='A', allowedIps=['127.0.0.1'])])
    value['policies']['connect'] = 'on_project_open'
    local['devices']['definitions']['new-server'] = value
    local['workflows']['main'] = graph([node('acquire', 'communication.tcp.session', 'new-server', selectors=dict(channelId='a'))])
    local['project']['name'] = 'unselected name'
    paths = ['/workflows/main/nodes/acquire']
    required = requiredProjectChanges(before, local, paths, deviceTypes=service.pluginScanResult.deviceTypes,
                                     operatorRegistry=service.pluginScanResult.activeOperators)
    assert set(required) == {'/devices/definitions/new-server'}
    receipt = publish(dict(service=service, client=client), local, paths+list(required), 'new-server')
    assert receipt['connections']['new-server'] == 'LISTENING'
    assert client.getOnlineProjectSnapshot()['payload']['project'] == before['project']
    with socket.create_connection(('127.0.0.1', value['connection']['port']), timeout=5):
        run(service, client, 'main')


@pytest.mark.parametrize('kind', ['client_once', 'receive_once'])
def testF04StopSingleTransactionAbortsAndReleases(runtime, tmp_path, kind):
    service, client = runtime
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        if kind == 'client_once':
            listener.listen(1)
        else:
            listener.close()
        payload = WorkflowStore().toPayload()
        device = definition(service.pluginScanResult.deviceTypes['device.tcp.'+kind], port=port)
        payload['devices']['definitions'] = dict(tcp=device)
        operator = 'communication.tcp.' + ('client' if kind == 'client_once' else 'receive_once')
        payload['workflows']['main'] = graph([node('wait', operator, 'tcp', params=dict(operation='exchange', text='WAIT') if kind == 'client_once' else {})])
        load(service, payload, tmp_path)
        # Run twice: Stop must release the transaction so a new Job can enter it.
        for _ in range(2):
            started = client.startJob(service.loadedProjectId, 'main')
            assert started.ok, started
            peer = None
            if kind == 'client_once':
                listener.settimeout(10)
                peer = listener.accept()[0]
                assert peer.recv(100) == b'WAIT'
            else:
                end = time.monotonic()+10
                while not service.deviceManager.drivers['tcp'].active and time.monotonic() < end:
                    time.sleep(.01)
                assert service.deviceManager.drivers['tcp'].active
            try:
                client.stopJob(started.job_id)
                result = waitForTerminal(service, started.job_id)
                assert result.status == 'ABORTED', result
                service.deviceManager.waitOwner(started.job_id)
                assert not service.deviceManager.drivers['tcp'].snapshot()['ownsResources']
            finally:
                if peer:
                    peer.close()


def testF05ReceiveTwiceAndListenerRemainsExclusive(runtime, tmp_path):
    service, client = runtime
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    payload = WorkflowStore().toPayload()
    device = definition(service.pluginScanResult.deviceTypes['device.tcp.receive_once'], port=port)
    device['defaults'].update(ackText='ACK', messageTextEncoding='utf-8')
    payload['devices']['definitions'] = dict(tcp=device)
    payload['workflows']['main'] = graph([node('wait', 'communication.tcp.receive_once', 'tcp')])
    load(service, payload, tmp_path)
    for _ in range(2):
        started = client.startJob(service.loadedProjectId, 'main')
        assert started.ok, started
        end = time.monotonic()+10
        while True:
            try:
                peer = socket.create_connection(('127.0.0.1', port), timeout=.1)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise
                time.sleep(.01)
        with peer, socket.socket() as other:
            with pytest.raises(OSError):
                other.bind(('127.0.0.1', port))
                other.listen(1)
            peer.sendall(b'RECEIVE\n')
            assert peer.recv(100).startswith(b'ACK')
            assert waitForTerminal(service, started.job_id).status == 'COMPLETED'
