"""Terminal follow includes final presentation diagnostics, without owning cleanup."""
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService


@pytest.fixture
def runtime(tmp_path):
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs',
                             logDirectory=tmp_path / 'logs')
    retirementWait = runtime.jobSupervisor.waitForRetirement
    try:
        yield runtime
    finally:
        # This fixture installs only a synthetic observer owner, never a device or worker.
        runtime._presentationOwner = None
        runtime.jobSupervisor.waitForRetirement = retirementWait
        runtime.close()


@pytest.mark.parametrize('resumeAfterTerminal', [False, True])
def testFollowIncludesPresentationFailureClosedAfterWorkerTerminal(runtime, monkeypatch, resumeAfterTerminal):
    terminal = runtime.eventStore.append('job-display', 'job.completed', 'done')
    order = []

    def retired(**kwargs):
        assert kwargs['jobId'] == terminal.jobId
        order.append('worker-retired')

    def drain(jobId, *, timeoutSeconds, isActive):
        assert order == ['worker-retired']
        assert jobId == terminal.jobId and timeoutSeconds == 2.0 and isActive()
        runtime.eventStore.append(jobId, 'presentation.source.unavailable', '最后一帧预览导出失败',
            level='ERROR', code='EXPORT_FAILED', nodeId='preview', workflowId='camera',
            payload={'sourceId': 'live', 'resultKey': 'final-result'})
        order.append('display-drained')
        return True

    def processed(jobId, sequence, timeoutSeconds):
        assert order == ['worker-retired', 'display-drained']
        assert jobId == terminal.jobId and sequence == terminal.sequence + 1
        assert timeoutSeconds == 3.0
        order.append('display-written')
        return True

    monkeypatch.setattr(runtime.jobSupervisor, 'waitForRetirement', retired)
    monkeypatch.setattr(runtime.operationalLogWriter, 'waitUntilProcessed', processed)
    runtime._presentationOwner = SimpleNamespace(waitForLogDrain=drain)
    request = pb.StreamJobEventsRequest(job_id=terminal.jobId, follow=True,
                                       after_sequence=terminal.sequence if resumeAfterTerminal else 0)
    events = list(runtime.StreamJobEvents(request, None))
    expected = [] if resumeAfterTerminal else ['job.completed']
    assert [event.event_type for event in events] == expected + ['presentation.source.unavailable']
    assert events[-1].sequence == terminal.sequence + 1
    assert events[-1].level == 'ERROR' and events[-1].node_id == 'preview'
    assert 'final-result' in events[-1].payload_json
    assert order == ['worker-retired', 'display-drained', 'display-written']


@pytest.mark.parametrize('cancelAt', ['retirement', 'presentation'])
def testFollowCancellationDoesNotContinueDrainingOrReading(runtime, monkeypatch, cancelAt):
    runtime.eventStore.append('job-display', 'job.completed', 'done')
    active = [True]
    drains, logWaits = [], []

    def retired(**kwargs):
        if cancelAt == 'retirement':
            active[0] = False

    def drain(jobId, *, timeoutSeconds, isActive):
        assert isActive()
        drains.append(jobId)
        active[0] = False
        assert not isActive(), 'drain must observe cancellation from the existing stream context'
        runtime.eventStore.append(jobId, 'presentation.source.unavailable', 'late failure', level='ERROR')
        return False

    monkeypatch.setattr(runtime.jobSupervisor, 'waitForRetirement', retired)
    monkeypatch.setattr(runtime.operationalLogWriter, 'waitUntilProcessed', lambda *a, **kw: logWaits.append(a))
    runtime._presentationOwner = SimpleNamespace(waitForLogDrain=drain)
    events = list(runtime.StreamJobEvents(pb.StreamJobEventsRequest(job_id='job-display', follow=True),
                                         SimpleNamespace(is_active=lambda: active[0])))
    assert [event.event_type for event in events] == ['job.completed']
    assert drains == ([] if cancelAt == 'retirement' else ['job-display'])
    assert not logWaits


def testHistoricalReadNeverWaitsForPresentationDrain(runtime):
    runtime.eventStore.append('job-display', 'job.completed', 'done')

    def unexpectedDrain(*args, **kwargs):
        raise AssertionError('history read must not wait on active presentation owners')

    runtime._presentationOwner = SimpleNamespace(waitForLogDrain=unexpectedDrain)
    events = list(runtime.StreamJobEvents(pb.StreamJobEventsRequest(job_id='job-display', follow=False), None))
    assert [event.event_type for event in events] == ['job.completed']


def testTerminalWaitIncludesReadPresentationButNeverNonOperationalSequences(runtime, monkeypatch):
    runtime.eventStore.append('job-display', 'job.completed', 'done')
    display = runtime.eventStore.append('job-display', 'presentation.source.recovered', 'preview recovered')
    runtime.eventStore.append('job-display', 'runtime.logfile.failed', 'file failure', notifySinks=False)
    waited = []

    def processed(jobId, sequence, timeoutSeconds):
        waited.append((jobId, sequence))
        # The final replay still includes a writer diagnostic discovered during this wait.
        runtime.eventStore.append(jobId, 'runtime.logfile.failed', 'late file failure', notifySinks=False)
        return True

    monkeypatch.setattr(runtime.operationalLogWriter, 'waitUntilProcessed', processed)
    events = list(runtime.StreamJobEvents(pb.StreamJobEventsRequest(job_id='job-display', follow=True), None))
    assert waited == [('job-display', display.sequence)]
    assert [event.event_type for event in events] == [
        'job.completed', 'presentation.source.recovered', 'runtime.logfile.failed', 'runtime.logfile.failed']
