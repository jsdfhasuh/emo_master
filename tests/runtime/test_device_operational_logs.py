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
