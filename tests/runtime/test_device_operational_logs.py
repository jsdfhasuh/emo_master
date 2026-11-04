"""Native device failures reach existing global events and JSONL without hardware."""
import json
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.devices.diagnostics import DeviceDiagnostics
from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.test_jsonl_logging import _records


SCOPE = dict(runtimeInstanceId='runtime', projectId='project', projectLoadGeneration='load')


@pytest.mark.parametrize('explicitLevel', [False, True])
def testNativeFailureJsonlProjectionRetainsLevelCodeAndIdentity(explicitLevel):
    written = []
    diagnostics = DeviceDiagnostics()
    diagnostics.writer = SimpleNamespace(enqueue=written.append)
    fields = dict(code='E_DEVICE_CONNECT', workflowId='main', nodeId='camera',
                  workflowRunId='run', nodeRunId='node-run', iterationPath=[2], jobId='job')
    if explicitLevel:
        fields['level'] = 'ERROR'
    diagnostics.append(SCOPE, 'camera', 'device.connect.failed', 'cannot connect', **fields)
    event, = written
    assert (event.level, event.code, event.projectId, event.jobId) == ('ERROR', 'E_DEVICE_CONNECT', 'project', 'job')
    assert (event.workflowId, event.nodeId, event.workflowRunId, event.nodeRunId) == ('main', 'camera', 'run', 'node-run')
    assert json.loads(event.iterationPathJson) == [2]
    assert diagnostics.query(SCOPE, 'camera')['records'][0]['level'] == 'ERROR'
    assert (event.originApp, event.component, event.runtimeInstanceId) == ('runtime', 'device', 'runtime')


def testGlobalFailureSummariesAndRecoveryHaveSinglePersistentCopy(tmp_path):
    now = [0.0]
    diagnostics = DeviceDiagnostics(clock=lambda: now[0])
    store = EventStore()
    writer = RuntimeJsonlLogWriter(tmp_path)
    store.addSink(writer.enqueue)
    store.addSink(diagnostics.projectEvent)
    diagnostics.writer = writer
    diagnostics.eventPublisher = store.append
    for index in range(3):
        now[0] = float(index)
        diagnostics.append(SCOPE, 'camera', 'device.connect.failed', 'cannot connect', code='E_DEVICE_CONNECT')
    assert len(store.read('__runtime__')) == 1
    assert store.read('job') == []
    diagnostics.append(SCOPE, 'camera', 'device.connect.completed', 'connection ready')
    events = store.read('__runtime__')
    assert [event.eventType for event in events] == [
        'device.connect.failed', 'device.connect.failed', 'device.connect.recovered']
    assert [event.level for event in events] == ['ERROR', 'ERROR', 'INFO']
    assert [event.sequence for event in events] == [1, 2, 3]
    assert json.loads(events[1].payloadJson)['occurrencesSinceLastLog'] == 2
    assert json.loads(events[-1].payloadJson)['previousCode'] == 'E_DEVICE_CONNECT'
    assert len(diagnostics.query(SCOPE, 'camera')['records']) == 4
    assert writer.close() is None
    rows = _records(tmp_path)
    assert len(rows) == 3
    assert all(row['payload']['deviceId'] == 'camera' for row in rows)
    assert [row['sequence'] for row in rows] == [1, 2, 3]


def testIntervalSummaryCancellationAndShutdownFlush():
    now = [0.0]
    diagnostics = DeviceDiagnostics(clock=lambda: now[0])
    store = EventStore()
    diagnostics.eventPublisher = store.append
    for index in range(2):
        diagnostics.append(SCOPE, 'camera', 'device.action.failed', 'read timed out', code='E_CAMERA_READ')
    now[0] = 31.0
    diagnostics.append(SCOPE, 'camera', 'device.action.failed', 'read timed out', code='E_CAMERA_READ')
    assert len(store.read('__runtime__')) == 2
    diagnostics.append(SCOPE, 'camera', 'device.action.failed', 'normal cancellation', code='E_DEVICE_CANCELLED')
    assert len(store.read('__runtime__')) == 2
    diagnostics.append(SCOPE, 'camera', 'device.action.failed', 'read timed out', code='E_CAMERA_READ')
    diagnostics.flushOperational()
    diagnostics.flushOperational()
    events = store.read('__runtime__')
    assert len(events) == 3
    assert json.loads(events[-1].payloadJson)['occurrences'] == 4
    assert json.loads(events[-1].payloadJson)['occurrencesSinceLastLog'] == 1
    assert not any(event.eventType.endswith('.recovered') for event in events)


def testDifferentDeviceActionsDoNotRecoverEachOthersFailures():
    diagnostics = DeviceDiagnostics()
    store = EventStore()
    diagnostics.eventPublisher = store.append
    diagnostics.append(SCOPE, 'plc', 'device.action.failed', 'write outcome unknown',
                       action='write', code='E_OUTCOME_UNKNOWN', requestId='original-write')
    diagnostics.append(SCOPE, 'plc', 'device.action.completed', 'read', action='read')
    assert len(store.read('__runtime__')) == 1
    diagnostics.append(SCOPE, 'plc', 'device.action.completed', 'write',
                       action='write', requestId='new-write')
    recovered = store.read('__runtime__')[-1]
    payload = json.loads(recovered.payloadJson)
    assert recovered.message == '设备后续操作成功：write'
    assert payload['requestId'] == 'new-write' and payload['previousRequestId'] == 'original-write'


def testFailedGlobalPublisherFallsBackToDeviceFileWithoutAffectingAppend():
    written = []
    diagnostics = DeviceDiagnostics()
    diagnostics.writer = SimpleNamespace(enqueue=written.append)
    def unavailable(*_args, **_kwargs):
        raise OSError('event store busy')
    diagnostics.eventPublisher = unavailable
    row = diagnostics.append(SCOPE, 'camera', 'device.connect.failed', 'cannot connect', code='E_DEVICE_CONNECT')
    assert row['cursor'] == 1 and len(written) == 1
    assert written[0].level == 'ERROR'
    assert diagnostics.logErrors and 'event store busy' in diagnostics.logErrors[-1]


def testRuntimeGlobalStreamSqliteAndFileShareDeviceFailureAndFlush(tmp_path):
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs', logDirectory=tmp_path / 'logs')
    diagnostics = runtime.deviceManager.diagnostics
    try:
        for _ in range(2):
            diagnostics.append(SCOPE, 'camera', 'device.connect.failed', 'synthetic connection failure', code='E_DEVICE_CONNECT')
        events = list(runtime.StreamJobEvents(pb.StreamJobEventsRequest(job_id='__runtime__'), None))
        assert len(events) == 1 and events[0].level == 'ERROR' and events[0].code == 'E_DEVICE_CONNECT'
        assert events[0].project_id == 'project'
        persisted = runtime.sqliteStore.listJobEventsAfter('__runtime__')
        assert len(persisted) == 1 and persisted[0].code == 'E_DEVICE_CONNECT'
    finally:
        runtime.close()
    rows = _records(tmp_path / 'logs')
    errors = [row for row in rows if row['eventType'] == 'device.connect.failed']
    assert len(errors) == 2
    assert errors[-1]['payload']['occurrences'] == 2
    assert errors[-1]['payload']['occurrencesSinceLastLog'] == 1


@pytest.mark.parametrize('failure', [None, 'known', 'plain'])
def testActionLogRetainsOperationContextAndOnlyExistingOutcome(monkeypatch, failure):
    from emo_master.apps.runtime.devices.executor import DeviceError
    from tests.runtime.test_managed_slmp import managerFor, plcProject
    manager = managerFor(plcProject(12345))
    driver = manager.drivers['plc']
    monkeypatch.setattr(driver, 'connect', lambda *_args: None)
    def perform(*_args, **_kwargs):
        if failure == 'known':
            raise DeviceError('E_TEST', 'unknown result', outcome='UNKNOWN')
        if failure == 'plain':
            raise RuntimeError('plain failure')
        return {'words': [42]}
    monkeypatch.setattr(driver, 'execute', perform)
    try:
        kwargs = dict(cycleId='cycle-a', requestId='actual-request',
                      source=dict(workflowId='main', nodeId='read', slot='plc'),
                      arguments=dict(device='D', startAddress=800, count=1))
        if failure:
            with pytest.raises((RuntimeError, DeviceError)):
                manager.execute('a', 'plc', 'read', {}, **kwargs)
        else:
            assert manager.execute('a', 'plc', 'read', {}, **kwargs) == {'words': [42]}
        rows = manager.diagnostics.query(manager.scope, 'plc')['records']
        row = next(row for row in rows if row['eventType'].startswith('device.action.'))
        assert {key: row[key] for key in ('jobId', 'workflowId', 'nodeId', 'planRevision', 'slot')} == dict(
            jobId='a', workflowId='main', nodeId='read', planRevision=0, slot='plc')
        assert row['requestId'] == 'actual-request' and row['action'] == 'read'
        if failure == 'known':
            assert row['outcome'] == 'UNKNOWN'
        else:
            assert 'outcome' not in row
        assert not row.get('workflowRunId') and not row.get('nodeRunId')
    finally:
        manager.close()


def testConnectionFailureThenManualSuccessPublishesOneRecovery(monkeypatch):
    from emo_master.apps.runtime.devices.executor import DeviceError
    from tests.runtime.test_managed_slmp import managerFor, plcProject
    manager = managerFor(plcProject(12345))
    store = EventStore()
    manager.diagnostics.eventPublisher = store.append
    driver = manager.drivers['plc']
    def fail(*_args):
        raise DeviceError('E_TEST_CONNECT', 'temporary refusal')
    monkeypatch.setattr(driver, 'connect', fail)
    try:
        pending = manager._connectEvent('plc', 'a')
        with pytest.raises(DeviceError):
            manager.executors['plc'].wait(pending)
        assert [event.eventType for event in store.read('__runtime__')] == ['device.connect.failed']
        monkeypatch.setattr(driver, 'connect', lambda *_args: None)
        manager.lifecycles['plc'].connect(lambda: False, float('inf'))
        manager.lifecycles['plc'].connect(lambda: False, float('inf'))
        assert [event.eventType for event in store.read('__runtime__')] == [
            'device.connect.failed', 'device.connect.recovered']
        rows = manager.diagnostics.query(manager.scope, 'plc')['records']
        assert [row['eventType'] for row in rows] == ['device.connect.failed', 'device.connect.completed']
        assert all(row['action'] == '' for row in rows)
    finally:
        manager.close()


def testTcpSynchronousAndBackgroundFailuresHaveExplicitPublishResponsibility(monkeypatch):
    from emo_master.apps.runtime.communication.manager import CommunicationManager
    from emo_master.core.project.communication import CommunicationSettings
    from emo_master.core.contracts.tcp_service import TcpServiceError
    events = []
    manager = CommunicationManager('runtime')
    manager.eventSink = lambda eventType, message, **fields: events.append((eventType, fields))
    config = CommunicationSettings.model_validate(dict(enabled=True, tcpServer=dict(bindAddress='127.0.0.1', port=12345,
        channels=[dict(channelId='loopback', name='Loopback', allowedIps=['127.0.0.1'])])))
    # The declaration requires a real port; use an ephemeral loopback reservation.
    import socket
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        config.tcpServer.port = reservation.getsockname()[1]
    manager.configure('project', 'key', config)
    monkeypatch.setattr(manager, '_spawn', lambda *_args: (_ for _ in ()).throw(RuntimeError('cannot create thread')))
    with pytest.raises(TcpServiceError):
        manager.start()
    assert events == []
    assert manager.snapshot()['records'][-1]['level'] == 'ERROR'
    manager.stop()
    monkeypatch.setattr(manager, '_spawn', lambda *_args: None)
    manager.start()
    assert events[-1] == ('device.connect.completed', dict(events[-1][1], action=''))
    manager._failService(RuntimeError('listener failed'), 'E_TCP_LISTENER_FAILED')
    assert events[-1][0] == 'device.connect.failed'
    assert events[-1][1]['level'] == 'ERROR' and events[-1][1]['action'] == ''
    manager.close()
