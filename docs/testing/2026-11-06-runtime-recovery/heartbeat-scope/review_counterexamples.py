"""Independent read-only review counterexamples; no application source is changed."""
import json
import queue
from threading import Event

import emo_master.apps.runtime.events.event_store as store_module
import emo_master.apps.runtime.jobs.supervisor as supervisor_module
from emo_master.apps.runtime.jobs.event_bridge import EventBridge
from tests.runtime.test_heartbeat_liveness import _withCell
from tests.runtime.test_job_supervisor_contracts import _supervisor


def test_silent_producer_after_read_pause_expires_on_next_observation(monkeypatch):
    supervisor, repository, store, process, cell, clock = _withCell(monkeypatch)
    clock[0] = 900
    cell.publish()
    clock[0] = 1000
    original_read = cell.read

    def delayed_read():
        sample = original_read()
        clock[0] = 6350
        # Unlike the author's healthy-producer case, nothing publishes during the pause.
        return sample

    monkeypatch.setattr(cell, 'read', delayed_read)
    supervisor.checkHeartbeat('job-stop')
    assert repository.get('job-stop').status == 'RUNNING'
    assert supervisor._heartbeatMonotonic['job-stop'] == 900
    assert not process.terminated

    monkeypatch.setattr(cell, 'read', original_read)
    monkeypatch.setattr(store_module.time, 'time', lambda: 1_800_000_000.123)
    supervisor.checkHeartbeat('job-stop')
    assert repository.get('job-stop').errorCode == 'E_HEARTBEAT_TIMEOUT'
    assert process.terminated
    failed = next(event for event in store.read('job-stop') if event.eventType == 'job.failed')
    heartbeat = json.loads(failed.payloadJson)['heartbeat']
    assert heartbeat['lastGoodMs'] == heartbeat['sampleMs'] == 900
    assert heartbeat['observationMs'] == heartbeat['currentMs'] == 6350
    assert heartbeat['clock'] == 'monotonic' and heartbeat['timeoutMs'] == 5000
    assert failed.timestampMs == 1_800_000_000_123
    print('silent-after-read: one deferred observation, then E_HEARTBEAT_TIMEOUT; epoch event timestamp preserved')


def test_dead_process_is_observed_even_with_fresh_shared_heartbeat(monkeypatch):
    supervisor, repository, store, process, cell, clock = _withCell(monkeypatch)
    clock[0] = 1000
    cell.publish()
    process.alive, process.exitcode = False, 23
    events, cancel = queue.Queue(), Event()
    supervisor._handles['job-stop'] = (process, cancel, events)
    bridge = EventBridge(supervisor, 'job-stop', process, events, cancel)
    supervisor._bridges['job-stop'] = bridge

    def heartbeat_must_not_run(_job_id):
        raise AssertionError('a fresh heartbeat must not postpone the process exit path')

    monkeypatch.setattr(supervisor, 'checkHeartbeat', heartbeat_must_not_run)
    bridge.run()
    assert repository.get('job-stop').errorCode == 'E_WORKER_CRASHED'
    assert repository.get('job-stop').status == 'FAILED'
    assert process.closed
    assert not process.terminated
    assert not supervisor.ownsJobResources('job-stop')
    print('dead-process: EventBridge selected processExited before heartbeat and completed retirement')


def test_legacy_timeout_payload_uses_wall_clock_and_no_monotonic_sample(monkeypatch):
    supervisor, repository, store, process = _supervisor()
    epoch = 1_700_000_000_000
    supervisor._heartbeat['job-stop'] = epoch
    supervisor._heartbeatTimeouts['job-stop'] = 5000
    supervisor._heartbeatIntervals['job-stop'] = 500
    monkeypatch.setattr(supervisor_module, 'nowMs', lambda: epoch + 5001)

    def forbidden_monotonic():
        raise AssertionError('legacy heartbeat observations must stay in the wall clock domain')

    monkeypatch.setattr(supervisor_module, 'monotonicMs', forbidden_monotonic)
    supervisor.checkHeartbeat('job-stop')
    assert repository.get('job-stop').errorCode == 'E_HEARTBEAT_TIMEOUT'
    assert process.terminated
    failed = next(event for event in store.read('job-stop') if event.eventType == 'job.failed')
    assert json.loads(failed.payloadJson)['heartbeat'] == {
        'clock': 'wall', 'observationMs': epoch + 5001, 'currentMs': epoch + 5001,
        'sampleMs': None, 'lastGoodMs': epoch, 'timeoutMs': 5000, 'intervalMs': 500, 'seen': False,
    }
    print('legacy: wall timestamp comparisons and payload clock label agree; sample is null')
