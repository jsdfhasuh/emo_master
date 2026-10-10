"""Real promotion fsync gate, same-project winner and Runtime close ordering."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import threading
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.jobs.event_bridge import EventBridge
from emo_master.apps.runtime.jobs.models import JobRecord
from emo_master.apps.runtime.preview import store as preview


class ExitedProcess:
    pid = -1
    exitcode = 0
    closed = False

    def is_alive(self):
        return False

    def join(self, timeout=None):
        pass

    def close(self):
        self.closed = True


class Channel(queue.Queue):
    closed = False

    def close(self):
        self.closed = True


def capture(workspace, job, value):
    writer = preview.PreviewSnapshotWriter(workspace, jobId=job, projectRevision=1)
    writer.capture(SimpleNamespace(nodeId='counter', outputPorts={'count': 'integer'}),
                   {'count': value}, SimpleNamespace(workflowId='main', iterationPath=()))
    entry = json.loads((writer.root / 'index.json').read_text())['entries'][0]
    identity = preview._assetIdentity('project-key', entry)
    return writer, 'snapshot-' + identity


@pytest.mark.parametrize('close_at_gate', [False, True])
def test_real_index_fsync_preserves_fifo_asset_winner_and_close_fence(tmp_path, monkeypatch, close_at_gate):
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs', logDirectory=tmp_path / 'logs')
    supervisor = runtime.jobSupervisor
    entered, release, b_claimed, close_waiting = (threading.Event() for _ in range(4))
    local = threading.local()
    original_os, original_atomic = preview.os, preview._atomicBytes
    original_claim = supervisor._claimTerminal
    original_wait = supervisor._waitFirstAttempt
    original_promote = runtime.previewAssetStore.promote
    original_assets_close = runtime.previewAssetStore.close
    index_path = runtime.previewAssetStore.root / 'project-key' / 'index.json'
    bridges, channels, processes, asset_ids = {}, {}, {}, {}
    promoted, disposed, errors = [], [], []
    close_thread = None

    class OsProxy:
        def __getattr__(self, name):
            return getattr(original_os, name)
        def fsync(self, fd):
            if threading.current_thread() is bridges.get('a') and getattr(local, 'index', False):
                entered.set()
                assert release.wait(4)
            return original_os.fsync(fd)

    def atomic(path, data):
        previous = getattr(local, 'index', False)
        local.index = Path(path) == index_path
        try:
            return original_atomic(path, data)
        finally:
            local.index = previous

    def claim(*args, **kwargs):
        ticket = original_claim(*args, **kwargs)
        if ticket is not None and ticket.jobId == 'b':
            b_claimed.set()
        return ticket

    def wait(ticket, *args, **kwargs):
        if threading.current_thread() is close_thread:
            close_waiting.set()
        return original_wait(ticket, *args, **kwargs)

    def promote(staging, key):
        result = original_promote(staging, key)
        promoted.append(threading.current_thread().name)
        assert not runtime._closed
        return result

    def close_assets():
        assert not supervisor._ownedJobs()
        disposed.append('assets')
        original_assets_close()

    def close_runtime():
        try:
            runtime.close()
        except BaseException as error:
            errors.append(error)

    try:
        prior, prior_id = capture(tmp_path / 'prior', 'prior', 11)
        original_promote(prior.root, 'project-key')
        prior_asset = runtime.previewAssetStore.resolve(prior_id)
        for job, value in [('a', 22), ('b', 33)]:
            workspace = runtime.workspaceRoot / job
            workspace.mkdir(parents=True)
            runtime._workspacePaths[job] = workspace
            runtime._jobPreviewKeys[job] = 'project-key'
            runtime.jobRepository.create(JobRecord(job, 'project', 1, 'main', status='RUNNING'))
            _, asset_ids[job] = capture(workspace, job, value)
            channel, process = Channel(), ExitedProcess()
            channel.put({'eventType': 'job.completed'})
            cancel = threading.Event()
            bridge = EventBridge(supervisor, job, process, channel, cancel)
            channels[job], processes[job], bridges[job] = channel, process, bridge
            supervisor._handles[job] = (process, cancel, channel)
            supervisor._bridges[job] = bridge
        runtime.jobRepository.create(JobRecord('c', 'project', 1, 'main', status='RUNNING'))
        monkeypatch.setattr(preview, 'os', OsProxy())
        monkeypatch.setattr(preview, '_atomicBytes', atomic)
        monkeypatch.setattr(supervisor, '_claimTerminal', claim)
        monkeypatch.setattr(supervisor, '_waitFirstAttempt', wait)
        monkeypatch.setattr(runtime.previewAssetStore, 'promote', promote)
        monkeypatch.setattr(runtime.previewAssetStore, 'close', close_assets)
        bridges['a'].start()
        assert entered.wait(1)
        bridges['b'].start()
        assert b_claimed.wait(1)
        supervisor.consumeWorkerEvent('c', {'eventType': 'process.output', 'message': 'progress'})
        assert [e.eventType for e in runtime.eventStore.read('c')] == ['process.output']
        assert supervisor.getProcess('a') is processes['a']
        assert supervisor.ownsJobResources('a') and supervisor.ownsJobResources('b')
        assert runtime.jobRepository.get('a').status == runtime.jobRepository.get('b').status == 'RUNNING'
        assert not promoted and not any(p.closed for p in processes.values())
        assert all(runtime._workspacePaths[job].exists() for job in bridges)
        assert json.loads(index_path.read_text())['entries'][0]['originJobId'] == 'prior'
        if close_at_gate:
            close_thread = threading.Thread(target=close_runtime, name='close-at-real-index-gate')
            close_thread.start()
            assert close_waiting.wait(1)
            assert not disposed and runtime.sqliteStore._idleConnection is not None
            assert runtime._runtimeDataLock._acquired and not runtime._closed
        release.set()
        for bridge in bridges.values():
            bridge.join(2)
            assert not bridge.is_alive()
        if close_thread is not None:
            close_thread.join(2)
            assert not close_thread.is_alive() and not errors and runtime._closed
        assert promoted == ['runtime-event-bridge-a', 'runtime-event-bridge-b']
        assert all(runtime.jobRepository.get(job).status == 'COMPLETED' for job in bridges)
        data, _ = runtime.previewAssetStore.readBytes(asset_ids['b'], projectKey='project-key')
        assert json.loads(data) == 33
        assert json.loads(index_path.read_text())['entries'][0]['originJobId'] == 'b'
        assert runtime.previewAssetStore.resolve(asset_ids['a']) is None
        assert runtime.previewAssetStore.resolve(prior_id) is None and not prior_asset.path.exists()
        assert all(p.closed for p in processes.values()) and all(q.closed for q in channels.values())
        assert not supervisor._ownedJobs()
    finally:
        release.set()
        for bridge in bridges.values():
            bridge.requestStop()
            if bridge.ident is not None:
                bridge.join(2)
        if close_thread is not None:
            close_thread.join(2)
        runtime.close()
    assert disposed == ['assets']
