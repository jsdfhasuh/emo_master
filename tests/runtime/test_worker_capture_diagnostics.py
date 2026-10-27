"""Optional capture metadata cannot replace the original worker terminal event."""
from collections import deque
from contextlib import nullcontext
import queue
import threading

import pytest

from emo_master.apps.runtime.jobs import worker_main as module
from emo_master.apps.runtime.execution.errors import WorkflowExecutionError
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested
from tests.runtime.test_worker_heartbeat_finalization import _spec, _stubComputation


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'aborted'])
@pytest.mark.parametrize('hasDetails', [False, True])
def testCaptureSummaryPreservesTerminalAndOriginalFailure(monkeypatch, tmp_path, outcome, hasDetails):
    primary = RuntimeError('original computation failure')

    def run():
        if outcome == 'failed':
            raise primary
        if outcome == 'aborted':
            raise CancellationRequested('original cancellation')

    _stubComputation(monkeypatch, run)
    runner = module.WorkflowRunner
    runner.captureErrors = 1
    details = [dict(method='output', workflowRunId='invocation', errorType='OSError')]
    if hasDetails:
        runner.captureDiagnostics = deque(details, maxlen=16)
    events = queue.Queue()
    expected = pytest.raises(RuntimeError) if outcome == 'failed' else nullcontext()
    with expected as caught:
        module.runJobProcess(_spec(tmp_path, None), threading.Event(), events)
    if outcome == 'failed':
        assert caught.value is primary
    rows = list(events.queue)
    terminal = rows[-1]
    assert terminal['eventType'] == f'job.{outcome}'
    assert terminal['payload'] == dict(captureErrors=1, captureDiagnostics=details if hasDetails else [])
    if outcome == 'failed':
        assert terminal['message'] == str(primary) and terminal['code'] == 'E_EXEC_FAILED'
    if outcome == 'aborted':
        assert terminal['message'] == 'original cancellation' and terminal['code'] == 'E_CANCELLED'
    assert not any(thread.name == 'runtime-heartbeat-job-stop' for thread in threading.enumerate())


@pytest.mark.parametrize('error,code', [
    (ValueError('invalid operator value'), 'E_EXEC_FAILED'),
    (WorkflowExecutionError('E_DEVICE_TIMEOUT', 'device timeout'), 'E_DEVICE_TIMEOUT'),
    (SystemExit('process exit'), 'E_WORKER_CRASHED'),
])
def testWorkerDistinguishesExecutionFailureFromProcessExit(monkeypatch, tmp_path, error, code):
    def run():
        raise error

    _stubComputation(monkeypatch, run)
    events = queue.Queue()
    with pytest.raises(type(error)) as caught:
        module.runJobProcess(_spec(tmp_path, None), threading.Event(), events)
    assert caught.value is error
    terminal = list(events.queue)[-1]
    assert terminal['eventType'] == 'job.failed'
    assert terminal['code'] == code and terminal['message'] == str(error)
