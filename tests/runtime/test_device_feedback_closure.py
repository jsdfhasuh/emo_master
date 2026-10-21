"""Frozen log attribution, authoritative activity, precise errors and RPC budgets."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import grpc
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.devices.diagnostics import DeviceDiagnostics
from emo_master.apps.runtime.events.models import RuntimeEvent
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.clients.runtime.devices import DeviceManagementClient
from emo_master.core.contracts.device_driver import DeviceError
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from tests.runtime.test_tcp_device_closure import BUILTINS, runtime, thirdParty  # noqa: F401
from tests.runtime.test_tcp_device_integration import definition, graph, node


@pytest.mark.parametrize('large', [False, True])
def testF09ProjectionPreservesFrozenIdentityAndOriginalEvent(large):
    diagnostics = DeviceDiagnostics()
    written = []
    diagnostics.writer = type('Writer', (), dict(enqueue=lambda _, event: written.append(event)))()
    scope = dict(runtimeInstanceId='runtime', projectId='project', projectLoadGeneration='load-old')
    fields = dict(phase='execute', data=dict(reading=42, detail='x'*(4000 if large else 4)),
        diagnostics=dict(tcpReceipt=dict(outcome='OUTCOME_UNKNOWN', requestId='send-request', automaticRetry=False)),
        cycleId='cycle', planRevision=7, deviceBindings={'tcp': dict(deviceId='server', scope=scope,
            deviceGeneration='backend-old', selectors=dict(channelId='a'))})
    event = RuntimeEvent(jobId='job', eventType='node.log', message='actual warning', level='WARN',
        timestampMs=123456, workflowId='main', workflowRunId='workflow-run', nodeId='node', nodeRunId='node-run',
        parentWorkflowRunId='parent', iterationPathJson='[1,2]', payloadJson=json.dumps(fields))
    diagnostics.projectEvent(event)
    row = diagnostics.query(scope, 'server')['records'][0]
    assert row['timestampMs'] == event.timestampMs and row['level'] == 'WARN'
    assert row['deviceGeneration'] == 'backend-old' and row['planRevision'] == 7
    assert row['workflowRunId'] == 'workflow-run' and row['nodeRunId'] == 'node-run'
    assert row['iterationPath'] == [1, 2] and row['ownerId'] == 'job' and row['cycleId'] == 'cycle'
    assert row['phase'] == 'execute'
    assert row['diagnostics'] == fields['diagnostics']
    if not large:
        assert row['data'] == fields['data']
    assert written == []  # projection never persists a second copy of node.log


def testF09LifecycleLoggerKeepsCreationIdentityWhileExecutionUsesNewSnapshot():
    from types import SimpleNamespace
    from emo_master.apps.runtime.execution.operator_executor import OperatorExecutor
    from emo_master.apps.runtime.workflow.context import RunContext
    from emo_master.apps.runtime.workflow.cancellation import CancellationToken
    from tests.runtime.test_operator_logging import _FullLifecycleLoggingOperator
    events = []
    executor = OperatorExecutor({'test.logging': _FullLifecycleLoggingOperator},
        eventPublisher=lambda *args, **fields: events.append(fields))
    node = SimpleNamespace(operatorId='test.logging', nodeId='node', devicePlans={'tcp': dict(deviceId='server')})
    context = RunContext.root('job', 'main')
    executor.deviceIdentities = dict(server=dict(runtimeInstanceId='runtime', projectId='project',
        projectLoadGeneration='load-old', deviceGeneration='backend-old'))
    executor._operatorForNode(node, context, CancellationToken())
    executor.deviceIdentities['server'] = dict(runtimeInstanceId='runtime', projectId='project',
        projectLoadGeneration='load-new', deviceGeneration='backend-new')
    logger = executor._operatorLogManager.createLogger(context, 'test.logging', 'execute', executor.bindingSnapshot(node))
    logger.info('new execution')
    logger.close()
    executor.disposeOperators()
    logs = [event['payload'] for event in events if 'deviceBindings' in event.get('payload', {})]
    assert {event['deviceBindings']['tcp']['deviceGeneration'] for event in logs if event['phase'] == 'lifecycle'} == {'backend-old'}
    assert {event['deviceBindings']['tcp']['deviceGeneration'] for event in logs if event['phase'] == 'execute'} == {'backend-new'}


@pytest.fixture
def tickerRuntime(tmp_path):
    root = Path(__file__).resolve().parents[2]
    service = RuntimeService(dbPath=tmp_path/'data/runtime.db', workspaceRoot=tmp_path/'data/jobs',
        pluginRootPaths=(str(BUILTINS), str(root/'examples/device_service')))
    client = RuntimeClient(runtimeService=service)
    declaration = service.pluginScanResult.deviceTypes['device.example.ticker']
    declaration.policiesSchema['properties']['operationTimeoutMs'] = dict(type='integer', default=10000, minimum=1, maximum=60000)
    payload = WorkflowStore().toPayload()
    value = definition(declaration)
    value['policies']['connect'] = 'on_project_open'
    payload['devices']['definitions'] = dict(ticker=value)
    path = tmp_path/'ticker.emoproj'
    path.write_text(json.dumps(payload), encoding='utf-8')
    assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
    yield service, client
    client.close()
    service.close()


def testF10ConcurrentServiceOwnersAppearInAuthoritativeState(tickerRuntime):
    service, client = tickerRuntime
    manager = service.deviceManager
    entered, release = Event(), Event()
    def held(*_args, **_kwargs):
        entered.set()
        assert release.wait(10)
        return dict(ticks=1)
    manager.drivers['ticker'].execute = held
    pool = ThreadPoolExecutor(2)
    try:
        for owner in ('a', 'b'):
            manager.authorizeMaintenance(owner, 'ticker')
        cycles = {owner: service.projectConfiguration.beginCycle(owner, -1, lambda: False)['cycleId'] for owner in ('a', 'b')}
        first = pool.submit(manager.execute, 'a', 'ticker', 'sample', {}, cycleId=cycles['a'], requestId='request-a')
        assert entered.wait(5)
        state = client.getDeviceStates(service.loadedProjectId)[1]['ticker']
        assert state['state'] == 'BUSY' and state['activeOwner'] == 'a' and state['activeOwners'] == ['a']
        second = pool.submit(manager.execute, 'b', 'ticker', 'sample', {}, cycleId=cycles['b'], requestId='request-b')
        end = monotonic()+5
        while len(manager.services['ticker'].operations) < 2 and monotonic() < end:
            sleep(.01)
        state = client.getDeviceStates(service.loadedProjectId)[1]['ticker']
        assert state['state'] == 'BUSY' and state['activeOwner'] is None
        assert state['activeOwners'] == ['a', 'b']
        assert {item['requestId'] for item in state['serviceOperations']} == {'request-a', 'request-b'}
        assert all(item['action'] == 'sample' for item in state['serviceOperations'])
        release.set()
        assert first.result(5) == second.result(5) == dict(ticks=1)
        state = client.getDeviceStates(service.loadedProjectId)[1]['ticker']
        assert state['state'] == 'IDLE' and state['activeOwners'] == [] and state['serviceOperations'] == []
    finally:
        release.set()
        pool.shutdown()
        for owner in ('a', 'b'):
            manager.retire(owner)


def tcpPayload(service):
    payload = WorkflowStore().toPayload()
    payload['devices']['definitions'] = dict(server=definition(service.pluginScanResult.deviceTypes['device.tcp.server'],
        channels=[dict(channelId='a', name='A', allowedIps=['127.0.0.1'])]))
    payload['workflows']['main'] = graph([node('acquire', 'communication.tcp.session', 'server', selectors=dict(channelId='a'))])
    return payload


def testF11SelectorIssueHasWorkflowNodeSlotAndSelector(runtime):  # noqa: F811
    service, _ = runtime
    payload = tcpPayload(service)
    payload['workflows']['main']['nodes'][1]['deviceBindings']['tcp']['selectors']['channelId'] = 'missing'
    with pytest.raises(WorkflowCompileError) as caught:
        WorkflowCompiler(service.pluginScanResult.activeOperators, deviceTypes=service.pluginScanResult.deviceTypes).compile(payload)
    issue = next(item for item in caught.value.issues if 'missing' in item.message)
    assert issue.workflowId == 'main' and issue.nodeId == 'acquire'
    assert issue.fieldPath == 'deviceBindings.tcp.selectors.channelId'
    assert issue.code != 'E_DEVICE_RUNTIME_UNAVAILABLE'


def testF11DefinitionIssueHasDeviceAndExactFieldWithoutIO(runtime, tmp_path, monkeypatch):  # noqa: F811
    service, _ = runtime
    payload = tcpPayload(service)
    payload['devices']['definitions']['server']['connection']['port'] = 0
    monkeypatch.setattr(service.deviceManager, 'makeBackend', lambda *_: pytest.fail('candidate must not make a backend'))
    with pytest.raises(WorkflowCompileError) as caught:
        prepareRuntimeProject(ProjectDocument.model_validate(payload), tmp_path, service.pluginScanResult.activeOperators,
                              deviceTypes=service.pluginScanResult.deviceTypes)
    assert any(item.fieldPath == 'devices.definitions.server.connection.port' for item in caught.value.issues)


def testF11InitialRpcAndUiReceiveStructuredDeviceLocation(runtime, tmp_path):  # noqa: F811
    from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
    service, client = runtime
    payload = tcpPayload(service)
    payload['devices']['definitions']['server']['connection']['port'] = 0
    with pytest.raises(RuntimeError) as caught:
        client.initializeProjectConfiguration(payload, tmp_path, 'invalid-location', validate=True)
    issues = caught.value.diagnostics['issues']
    assert any(item['fieldPath'] == 'devices.definitions.server.connection.port' for item in issues)
    details = ProjectConfigurationDialog._errorDetails(caught.value)
    assert 'devices.definitions.server.connection.port' in details
    assert not service.deviceManager.drivers and not service.projectConfiguration.current


@pytest.mark.parametrize('source', ['override', 'dynamic', 'address'])
def testF11DeviceParameterSourcesKeepExactPositions(runtime, tmp_path, source):  # noqa: F811
    service, _ = runtime
    payload = tcpPayload(service)
    item = payload['workflows']['main']['nodes'][1]
    if source == 'override':
        item['deviceOverrides'] = dict(tcp=dict(timeoutMs=-1))
        path = 'deviceOverrides.tcp.timeoutMs'
    elif source == 'dynamic':
        item['deviceValueBindings'] = dict(tcp=dict(timeoutMs=dict(source='variable', variableId='missing')))
        path = 'deviceValueBindings.tcp.timeoutMs.variableId'
    else:
        payload['devices']['definitions']['server']['connection']['bindAddress'] = 'invalid.address'
        path = 'devices.definitions.server.connection.bindAddress'
    with pytest.raises(WorkflowCompileError) as caught:
        WorkflowCompiler(service.pluginScanResult.activeOperators, deviceTypes=service.pluginScanResult.deviceTypes).compile(payload)
    assert any(issue.fieldPath == path for issue in caught.value.issues)


def testF11DevicePackageShowsTypeAndRequiredVersion(runtime, tmp_path):  # noqa: F811
    from emo_master.apps.designer.state.device_package import DevicePackage, importDevices
    from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
    service, _ = runtime
    value = tcpPayload(service)['devices']['definitions']['server']
    package = DevicePackage(devices=dict(server=value))
    session = ProjectEditSession(WorkflowStore().toPayload())
    before = session.payload()
    with pytest.raises(WorkflowCompileError) as caught:
        importDevices(session, package, {}, tmp_path)
    assert value['deviceTypeId'] in str(caught.value) and value['driverVersion'] in str(caught.value)
    assert '未安装' in str(caught.value) and session.payload() == before


@pytest.mark.parametrize('dependency', ['operator_missing', 'operator_version', 'device_missing', 'device_version'])
def testF11PackageDependencyReportsRequiredAndInstalledVersions(runtime, tmp_path, dependency):  # noqa: F811
    service, _ = runtime
    registry, types = service.pluginScanResult.activeOperators, service.pluginScanResult.deviceTypes
    source = tmp_path/'source'
    source.mkdir()
    (source/'project.json').write_text(json.dumps(tcpPayload(service)), encoding='utf-8')
    package = buildRuntimePackage(source, tmp_path/'packages', registry=registry, deviceTypes=types)
    registry, types = dict(registry), dict(types)
    operator = dependency.startswith('operator')
    key = 'communication.tcp.session' if operator else 'device.tcp.server'
    required = registry[key].manifest.version if operator else types[key].version
    if dependency.endswith('missing'):
        (registry if operator else types).pop(key)
    elif operator:
        registry[key] = replace(registry[key], manifest=replace(registry[key].manifest, version='99.0.0'))
    else:
        types[key] = types[key].model_copy(update=dict(version='99.0.0'))
    with pytest.raises(WorkflowCompileError) as caught:
        installRuntimePackage(package, tmp_path/'import', registry=registry, deviceTypes=types)
    message = str(caught.value)
    assert key in message and required in message
    assert ('99.0.0' if dependency.endswith('version') else '未安装') in message
    assert caught.value.issues[0].fieldPath


@pytest.mark.parametrize('transport', ['embedded', 'grpc', 'designer'])
def testManagementSixSecondsUsesDeclaredBudgetAndCancelExitsOwner(tickerRuntime, transport):
    service, designer = tickerRuntime
    server = grpc.server(ThreadPoolExecutor(4))
    runtime_pb2_grpc.add_RuntimeServiceServicer_to_server(service, server)
    port = server.add_insecure_port('127.0.0.1:0')
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    client = (designer if transport == 'designer' else DeviceManagementClient(
        service if transport == 'embedded' else runtime_pb2_grpc.RuntimeServiceStub(channel)))
    def slow(action, params, cancelled, deadline, **_kwargs):
        end = monotonic()+6
        while monotonic() < end:
            if cancelled():
                raise DeviceError('E_DEVICE_CANCELLED', 'cancelled six-second action')
            if monotonic() >= deadline:
                raise DeviceError('E_DEVICE_OPERATION_TIMEOUT', 'action budget expired')
            sleep(.01)
        return dict(ticks=123)
    service.deviceManager.drivers['ticker'].execute = slow
    try:
        runtimeId, states = client.getDeviceStates(service.loadedProjectId)
        start = monotonic()
        result = client.executeDeviceAction(service.loadedProjectId, runtimeId, 'ticker', states['ticker'], 'sample', {})
        assert result == dict(ticks=123) and monotonic()-start >= 6
        assert not service.deviceManager.maintenanceOwners and not service.projectConfiguration.cycles
        if transport != 'designer':
            with ThreadPoolExecutor(1) as pool:
                task = pool.submit(client.executeDeviceAction, service.loadedProjectId, runtimeId, 'ticker', states['ticker'], 'sample', {})
                end = monotonic()+5
                while not service.deviceManager.maintenanceOwners and monotonic() < end:
                    sleep(.01)
                client.close()
                with pytest.raises(RuntimeError):
                    task.result(5)
            end = monotonic()+5
            while service.deviceManager.maintenanceOwners and monotonic() < end:
                sleep(.01)
            assert not service.deviceManager.maintenanceOwners and not service.projectConfiguration.cycles
    finally:
        if client is not designer:
            client.close()
        channel.close()
        server.stop(0).wait(5)
