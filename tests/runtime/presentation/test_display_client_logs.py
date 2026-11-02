"""Local display failures remain bounded and carry usable result identity."""
from types import SimpleNamespace
import time

import grpc
import numpy as np
import pytest

from emo_master.clients.runtime.display_logs import DisplayLog
from emo_master.clients.runtime.display_session import DisplaySession
from tests.runtime.presentation.test_live_decisions import result


@pytest.fixture
def session(monkeypatch):
    for name in ('_receive', '_health', '_decode'):
        monkeypatch.setattr(DisplaySession, name, lambda self: None)
    value = DisplaySession('127.0.0.1:1', 'job', expectedRuntimeInstanceId='runtime', projectId='project')
    yield value
    value.close()


def testRepeatedFailuresAreSummarizedAndStableHealthRecovers():
    now = [0.0]
    logs = DisplayLog(clock=lambda: now[0])
    context = {'jobId': 'job', 'payload': {'sourceId': 'image'}}
    for index in range(100):
        now[0] = index * .1
        logs.failed('image', 'READ_TIMEOUT', 'WARN', '图片读取超时', context)
        logs.healthy('image', context)
    assert len(logs.take()) == 1
    now[0] = 30
    logs.failed('image', 'READ_TIMEOUT', 'WARN', '图片读取超时', context)
    event, = logs.take()
    assert event['payload']['occurrencesSinceLastLog'] == 100
    now[0] = 31
    logs.healthy('image', context)
    now[0] = 32.1
    logs.healthy('image', context)
    recovered, = logs.take()
    assert recovered['level'] == 'INFO' and recovered['eventType'].endswith('.recovered')
    assert recovered['payload']['previousCode'] == 'READ_TIMEOUT'


def testQueueOverflowIsReportedAndTerminalFlushDoesNotInventRecovery():
    logs = DisplayLog(clock=lambda: 0)
    for index in range(350):
        logs.failed(index, 'IMAGE_DECODE_FAILED', 'ERROR', 'corrupt image', {'jobId': 'job'})
    assert len(logs.issues) == 256 and len(logs.events) == 128
    events = logs.take()
    assert events[0]['code'] == 'DISPLAY_LOG_DROPPED'
    assert events[0]['payload']['dropped'] == 222
    logs.failed(349, 'IMAGE_DECODE_FAILED', 'ERROR', 'corrupt image', {'jobId': 'job'})
    logs.finish()
    event, = logs.take()
    assert event['eventType'].endswith('.failed')
    assert event['payload']['occurrencesSinceLastLog'] == 1
    logs.finish()
    assert logs.take() == []


def testRealDecodePathReportsContextAndCancellationIsQuiet(session, monkeypatch):
    class Cancelled(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.CANCELLED

    def fail(*_):
        raise ValueError('asset digest/size mismatch')

    monkeypatch.setattr(session, '_readImage', fail)
    bad = result(1)
    session._decodeLive(session.generation, bad, time.perf_counter_ns(), 0)
    event, = session.takeLogEvents()
    assert event['level'] == 'ERROR' and event['code'] == 'IMAGE_DECODE_FAILED'
    assert event['jobId'] == 'job' and event['projectId'] == 'project'
    assert event['payload']['identity']['resultKey'] == bad.identity.resultKey
    assert event['payload']['sourceId'] == 'image'
    session.reportImageError(result(2), 'image', Cancelled())
    unexpected, = session.takeLogEvents()
    assert unexpected['level'] == 'WARN' and unexpected['code'] == 'CANCELLED'
    monkeypatch.setattr(session, '_readImage', lambda *_: np.zeros((1, 1, 3), dtype=np.uint8))
    session._decodeLive(session.generation, result(3), time.perf_counter_ns(), 0)
    session.close()
    recovered, = session.takeLogEvents()
    assert recovered['eventType'].endswith('.recovered')
    assert recovered['payload']['identity']['resultOrdinal'] == 3
    session.reportImageError(result(3), 'image', Cancelled())
    session._connection('CANCELLED', 'closed by owner')
    assert session.takeLogEvents() == []


def testDisplayConnectionFailureAndObservedRecoveryAreRecorded(session):
    session._connection('UNAVAILABLE', 'network unavailable')
    session._connection('UNAVAILABLE', 'network unavailable')
    event, = session.takeLogEvents()
    assert event['level'] == 'WARN' and event['code'] == 'UNAVAILABLE'
    assert event['payload']['address'] == '127.0.0.1:1'
    session._connection('CONNECTED')
    session.close()
    recovered, = session.takeLogEvents()
    assert recovered['level'] == 'INFO'
    assert recovered['payload']['occurrences'] == 2


def testDispatchDrainsBothJobsAndRoutesImageDiagnostics():
    from emo_master.clients.runtime.dispatch_display import DispatchDisplaySession
    session = object.__new__(DispatchDisplaySession)
    calls = []
    session.sessions = {'job': SimpleNamespace(takeLogEvents=lambda: [{'jobId': 'job'}],
        reportImageIssue=lambda *args, **kwargs: calls.append((args, kwargs))),
        'producer': SimpleNamespace(takeLogEvents=lambda: [{'jobId': 'producer'}])}
    assert {row['jobId'] for row in session.takeLogEvents()} == {'job', 'producer'}
    current = result(1)
    session.reportImageIssue(current, 'image', 'UI_IMAGE_BUDGET', stage='qt_image')
    assert calls == [((current, 'image', 'UI_IMAGE_BUDGET'), {'stage': 'qt_image'})]
