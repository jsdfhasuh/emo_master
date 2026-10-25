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


def publish(store, session, root, job='a', value=1, final='node.completed', *, invocation=None,
            terminal=True, writer=None, transform=None, inline=False):
    if job not in store.sessions[session].jobs:
        store.attach(session, 'project', job, document())
    context = SimpleNamespace(workflowId='main', workflowRunId=job + '-flow',
                              nodeRunId=invocation or job + '-node', iterationPath=(0,))
    previous = store.sessions[session].jobs[job].last.get(('main', 'load'), {})
    sequence = previous.get('sequence', 0) + 1
    store.observe(RuntimeEvent(job, 'node.started', '', workflowId='main', nodeId='load',
        workflowRunId=context.workflowRunId, nodeRunId=context.nodeRunId, sequence=sequence))
    def receive(**event):
        if transform:
            transform(event['payload'])
        store.publish(job, event, root / job)
    writer = writer or PreviewSnapshotWriter(root / job, jobId=job, projectRevision=7,
                                             inspectionPublisher=receive, inlineInspection=inline)
    writer.capture(document().workflows['main'].nodes[0], {'image': np.full((30, 40, 3), value, np.uint8)}, context)
    store.observe(RuntimeEvent(job, final, '', workflowId='main', nodeId='load',
        workflowRunId=context.workflowRunId, nodeRunId=context.nodeRunId, sequence=sequence + 1))
    if terminal:
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


@pytest.mark.parametrize('inline', [False, True])
def testLastLoopFailureNeverReusesEarlierSuccessButPreviousRemainsReadable(owner, tmp_path, inline):
    store, session = owner
    publish(store, session, tmp_path, invocation='node-0', terminal=False, inline=inline)
    for sequence, kind, node in [(3, 'node.started', 'node-1'), (4, 'node.failed', 'node-1'),
                                  (5, 'node.completed', 'node-0')]:
        store.observe(RuntimeEvent('a', kind, '', workflowId='main', nodeId='load', workflowRunId='a-flow',
                                   nodeRunId=node, sequence=sequence))
    store.terminal('a', tmp_path / 'a', 'FAILED')
    sources, state, reason = store.list(session, 'project', 'a', 'main', 'load')
    assert not sources and state == 'NO_CURRENT_SNAPSHOT' and 'NOT_COMPLETED' in reason
    previous = store.list(session, 'project', 'a', 'main', 'load', 'a-flow', 'node-0')[0][0]
    assert b''.join(chunk for chunk, _ in store.stream(session, 'project', previous.assetId))
    assert store.stats()['encodedBytes'] > 0


def testSkipAndNewJobAcceptanceDoNotRotateNodePictures(owner, tmp_path):
    store, session = owner
    publish(store, session, tmp_path, invocation='node-0', terminal=False)
    first = store.list(session, 'project', 'a', 'main', 'load')[0][0]
    store.observe(RuntimeEvent('a', 'node.skipped', '', workflowId='main', nodeId='load',
                               workflowRunId='a-flow', nodeRunId='skipped', sequence=3))
    assert store.list(session, 'project', 'a', 'main', 'load')[0] == [first]
    assert len(store.sessions[session].executions[('main', 'load')]) == 1
    store.terminal('a', tmp_path / 'a', 'COMPLETED')
    for job in ('b', 'c'):
        store.attach(session, 'project', job, document())
        store.terminal(job, tmp_path / job, 'COMPLETED')
    assert store.list(session, 'project', 'a', 'main', 'load')[0] == [first]
    assert first.path.is_file()


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
        if path.parent.name == 'a':
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
    def wrongJob(payload):
        payload['entries'][0]['originJobId'] = 'foreign'
    publish(store, session, tmp_path, 'b', transform=wrongJob)
    assert not store.list(session, 'project', 'b', 'main', 'load')[0]
    assert 'IDENTITY' in store.list(session, 'project', 'b', 'main', 'load')[2]


def testRunningJobRetainsExactlyTwoInvocationsAndReaderSurvivesThird(owner, tmp_path):
    import cv2
    store, session = owner
    writer = publish(store, session, tmp_path, invocation='first', value=5, terminal=False)
    first = store.list(session, 'project', 'a', 'main', 'load', 'a-flow', 'first')[0][0]
    reader = store.stream(session, 'project', first.assetId)
    assert next(reader)[0]
    for invocation, value in (('second', 2), ('third', 3)):
        publish(store, session, tmp_path, invocation=invocation, value=value, terminal=False, writer=writer)
        source = store.list(session, 'project', 'a', 'main', 'load', 'a-flow', invocation)[0][0]
        data = b''.join(chunk for chunk, _ in store.stream(session, 'project', source.assetId))
        assert int(cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)[0, 0, 0]) == value
    assert not store.sessions[session].jobs['a'].ready
    assert store.list(session, 'project', 'a', 'main', 'load', 'a-flow', 'first')[0] == []
    assert first.path.exists() and store.stats()['readers'] == 1
    assert len(store.sessions[session].executions[('main', 'load')]) == 2
    reader.close()
    assert not first.path.exists()
    assert len(list((store.root).rglob('*.png'))) == 2


@pytest.mark.parametrize('inline', [False, True])
def testLivePublishingPreservesExistingMetadataBudget(owner, tmp_path, monkeypatch, inline):
    store, session = owner
    monkeypatch.setattr(module, 'METADATA_BYTES', 1)
    publish(store, session, tmp_path, terminal=False, inline=inline)
    sources, state, reason = store.list(session, 'project', 'a', 'main', 'load')
    assert not sources and state == 'NO_CURRENT_SNAPSHOT' and 'METADATA_BUDGET' in reason
    assert store.stats()['encodedBytes'] == 0


def testInlineInspectionKeepsExactLastTwoWithoutStagingOrRetainedImageFiles(owner, tmp_path):
    import cv2
    store, session = owner
    writer = None
    sizes = []
    for iteration in range(6):
        writer = publish(store, session, tmp_path, invocation=str(iteration), value=iteration,
                         terminal=False, writer=writer, inline=True)
        sources, state, _ = store.list(session, 'project', 'a', 'main', 'load')
        assert state == 'CURRENT_AVAILABLE' and len(sources) == 1
        asset = sources[0]
        assert asset.nodeRunId == str(iteration) and asset.originJobId == 'a' and asset.originProjectRevision == 7
        data = b''.join(chunk for chunk, _ in store.stream(session, 'project', asset.assetId))
        assert np.all(cv2.imdecode(np.frombuffer(data, np.uint8), -1) == iteration)
        sizes.append(len(data))
        assert store.usage() == sum(sizes[-2:])
        assert len(store.sessions[session].executions[('main', 'load')]) == min(iteration + 1, 2)
        assert not asset.path.exists() and not writer.inspectionRoot.exists()
    assert not list(store.root.rglob('*.png'))


def testInlineReaderKeepsEvictedBytesQuotaChargedUntilIteratorCloses(owner, tmp_path):
    store, session = owner
    publish(store, session, tmp_path, 'a', inline=True)
    first = store.list(session, 'project', 'a', 'main', 'load')[0][0]
    reader = store.stream(session, 'project', first.assetId)
    data, mime = next(reader)
    assert data and mime == 'image/png'
    for job in ('b', 'c'):
        publish(store, session, tmp_path, job, inline=True)
    assert store.list(session, 'project', 'a', 'main', 'load')[1] == 'EXPIRED'
    assert store.stats()['retiredJobs'] == 1 and store.usage() == 3 * len(data)
    store.closeSession(session)
    assert store.stats()['encodedBytes'] == len(data) and store.stats()['readers'] == 1
    reader.close()
    assert store.stats()['encodedBytes'] == 0 and store.stats()['sessions'] == 0


@pytest.mark.parametrize('scope', ['JOB_BYTES', 'SESSION_BYTES', '_PROJECT_LIMIT_BYTES', '_GLOBAL_LIMIT_BYTES'])
def testInlineImagesShareExistingStorageBudgets(owner, tmp_path, monkeypatch, scope):
    store, session = owner
    writer = publish(store, session, tmp_path, invocation='first', terminal=False, inline=True)
    size = store.usage()
    monkeypatch.setattr(module.legacy if scope.startswith('_') else module, scope, size)
    publish(store, session, tmp_path, invocation='second', terminal=False, writer=writer)
    sources, state, reason = store.list(session, 'project', 'a', 'main', 'load')
    assert not sources and state == 'NO_CURRENT_SNAPSHOT' and 'ASSET_BUDGET' in reason
    assert store.usage() == size


def testInlineAndFileImagesUseOneCombinedBudget(owner, tmp_path, monkeypatch):
    store, session = owner
    publish(store, session, tmp_path, 'a')
    size = store.usage()
    monkeypatch.setattr(module, 'SESSION_BYTES', size)
    publish(store, session, tmp_path, 'b', inline=True)
    assert 'ASSET_BUDGET' in store.list(session, 'project', 'b', 'main', 'load')[2]
    assert store.usage() == size
