"""Leased physical ownership, immutable identities and failure cleanup."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from emo_master.apps.runtime.events.models import RuntimeEvent
from emo_master.apps.runtime.preview.store import PreviewAssetStore, PreviewSnapshotWriter
from emo_master.apps.runtime.preview.run_inspection import RunInspectionStore
from emo_master.apps.runtime.preview import run_inspection as module


def document():
    return SimpleNamespace(project=SimpleNamespace(revision=7), workflows={'main': SimpleNamespace(nodes=[
        SimpleNamespace(nodeId='load', outputPorts={'image': 'image', 'optional': 'image'})])})


def publish(store, session, root, job='a', value=1, final='node.completed'):
    store.attach(session, 'project', job, document())
    context = SimpleNamespace(workflowId='main', workflowRunId=job + '-flow', nodeRunId=job + '-node', iterationPath=(0,))
    writer = PreviewSnapshotWriter(root / job, jobId=job, projectRevision=7)
    writer.capture(document().workflows['main'].nodes[0], {'image': np.full((30, 40, 3), value, np.uint8)}, context)
    store.observe(RuntimeEvent(job, 'node.started', '', workflowId='main', nodeId='load',
        workflowRunId=context.workflowRunId, nodeRunId=context.nodeRunId, sequence=1))
    store.observe(RuntimeEvent(job, final, '', workflowId='main', nodeId='load',
        workflowRunId=context.workflowRunId, nodeRunId=context.nodeRunId, sequence=2))
    store.terminal(job, root / job, 'COMPLETED' if final == 'node.completed' else 'FAILED')
    return writer


@pytest.fixture
def owner(tmp_path):
    assets = PreviewAssetStore(tmp_path / 'assets')
    store = RunInspectionStore(assets, startMaintenance=False)
    try:
        yield store, store.open('project')
    finally:
        store.close()
        assets.close()


def testTwoJobsAndReaderHoldEvictedPixelsUntilActualIteratorExit(owner, tmp_path):
    store, session = owner
    publish(store, session, tmp_path, 'a', 1)
    first = store.list(session, 'project', 'a', 'main', 'load')[0][0]
    reader = store.stream(session, 'project', first.assetId)
    assert next(reader)[0]
    publish(store, session, tmp_path, 'b', 2)
    publish(store, session, tmp_path, 'c', 3)
    assert store.list(session, 'project', 'a', 'main', 'load')[1] == 'EXPIRED'
    assert first.path.is_file() and store.stats()['retiredJobs'] == 1
    assert store.stats()['readers'] == 1
    # Closing the session also cannot return the active reader's disk ownership.
    store.closeSession(session)
    assert first.path.exists() and store.stats()['encodedBytes'] > 0
    reader.close()
    assert not first.path.exists() and store.stats()['encodedBytes'] == 0
    assert store.stats()['sessions'] == 0


@pytest.mark.parametrize('status', ['node.failed', 'node.skipped'])
def testLastLoopFailureOrSkipNeverReusesEarlierSuccess(owner, tmp_path, status):
    store, session = owner
    store.attach(session, 'project', 'a', document())
    ctx = SimpleNamespace(workflowId='main', workflowRunId='body-0', nodeRunId='node-0', iterationPath=(0,))
    writer = PreviewSnapshotWriter(tmp_path / 'a', jobId='a', projectRevision=7)
    writer.capture(document().workflows['main'].nodes[0], {'image': np.zeros((3, 4, 3), np.uint8)}, ctx)
    for sequence, kind, flow, node in [(1, 'node.started', 'body-0', 'node-0'), (2, 'node.completed', 'body-0', 'node-0'),
                                       (3, 'node.started', 'body-1', 'node-1'), (4, status, 'body-1', 'node-1'),
                                       (5, 'node.completed', 'body-0', 'node-0')]:
        store.observe(RuntimeEvent('a', kind, '', workflowId='main', nodeId='load', workflowRunId=flow,
                                   nodeRunId=node, sequence=sequence))
    store.terminal('a', tmp_path / 'a', 'FAILED')
    sources, state, reason = store.list(session, 'project', 'a', 'main', 'load')
    assert not sources and state == 'NO_CURRENT_SNAPSHOT' and 'NOT_COMPLETED' in reason
    assert store.stats()['encodedBytes'] == 0


def testLeaseCapacityExpiryAndFailedStartDoNotRotate(owner, tmp_path):
    store, session = owner
    store.clock = lambda: 1
    store.sessions[session].expiresAt = 31
    second = store.open('other')
    with pytest.raises(ValueError, match='SESSION_BUDGET'):
        store.open('third')
    with pytest.raises(ValueError, match='EXPIRED'):
        store.require(session, 'other')
    publish(store, session, tmp_path, 'a')
    store.attach(session, 'project', 'rejected', document(), accepted=False)
    store.abandon(session, 'project', 'rejected')
    assert list(store.sessions[session].jobs) == ['a']
    store.clock = lambda: 30
    store.renew(session, 'project')
    store.clock = lambda: 32
    store.expire()
    assert second not in store.sessions and session in store.sessions
    store.clock = lambda: 61
    store.expire()
    assert store.stats()['sessions'] == 0 and store.stats()['encodedBytes'] == 0


def testQuotaAndFailedUnlinkRemainChargedAndBlockNewCopies(owner, tmp_path, monkeypatch):
    store, session = owner
    publish(store, session, tmp_path, 'a')
    publish(store, session, tmp_path, 'b')
    original = module.shutil.rmtree
    def fail(path):
        if path.name == 'a':
            raise OSError('injected locked file')
        original(path)
    monkeypatch.setattr(module.shutil, 'rmtree', fail)
    publish(store, session, tmp_path, 'c')
    assert store.stats()['retiredJobs'] == 1 and store.usage() > 0
    monkeypatch.setattr(module, 'JOB_BYTES', 1)
    publish(store, session, tmp_path, 'd')
    assert not store.list(session, 'project', 'd', 'main', 'load')[0]
    assert 'ASSET_BUDGET' in store.list(session, 'project', 'd', 'main', 'load')[2]
    monkeypatch.setattr(module.shutil, 'rmtree', original)
    store.expire()
    store.closeSession(session)
    assert store.stats()['encodedBytes'] == 0


def testForeignIdentityAndUncommittedTemporaryIndexAreNotImported(owner, tmp_path):
    store, session = owner
    store.attach(session, 'project', 'a', document())
    root = tmp_path / 'a' / 'preview_staging'
    root.mkdir(parents=True)
    (root / '.index.json.uncommitted.tmp').write_text(json.dumps({'entries': [{'nodeId': 'load'}]}))
    store.terminal('a', tmp_path / 'a', 'ABORTED')
    assert store.stats()['encodedBytes'] == 0
    writer = publish(store, session, tmp_path, 'b')
    index = writer.root / 'index.json'
    payload = json.loads(index.read_text())
    payload['entries'][0]['originJobId'] = 'foreign'
    index.write_text(json.dumps(payload))
    job = store.sessions[session].jobs['b']
    for source in job.sources:
        source.path.unlink()
    job.sources.clear()
    job.ready = False
    store.terminal('b', tmp_path / 'b', 'COMPLETED')
    assert not store.list(session, 'project', 'b', 'main', 'load')[0]
    assert 'IDENTITY' in store.list(session, 'project', 'b', 'main', 'load')[2]
