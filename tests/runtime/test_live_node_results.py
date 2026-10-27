"""Real loop + spawn + gRPC. Assertions happen while the Job is RUNNING."""
from dataclasses import asdict
import json
import time

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.state.run_result_history import RunResultHistory
from emo_master.apps.designer.services.display_calls import DisplayCallContext
from tests.runtime.inspection_fixtures import loopProject
from tests.runtime.test_run_inspection_rpc import running
from tests.runtime.runtime_test_utils import waitForTerminal


def waitForGate(runtime, job):
    deadline = time.monotonic() + 10
    while not any(event.eventType == 'node.started' and event.nodeId == 'gate'
                  for event in runtime.eventStore.readMerged(job)):
        assert time.monotonic() < deadline, runtime.eventStore.readMerged(job)[-8:]
        time.sleep(.01)
    assert runtime.jobRepository.get(job).status == 'RUNNING'


def invocationSource(client, project, session, record, *, node='image', port='image'):
    deadline = time.monotonic() + 5
    while True:
        listing = client.listNodePreviewSourcesWithMetadata(project, 'body', node,
            jobId=record['jobId'], inspectionSessionId=session,
            workflowRunId=record['workflowRunId'], nodeRunId=record['nodeRunId'])
        if listing.captureState != 'PREPARING':
            break
        assert time.monotonic() < deadline, listing
        time.sleep(.01)
    assert listing.captureState == 'CURRENT_AVAILABLE', listing
    return next(source for source in listing.sources if source.port == port)


def pixels(client, project, session, source):
    content, mime = client.readInspectionAsset(project, session, source.sourceId, DisplayCallContext())
    assert mime == 'image/png'
    return cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_UNCHANGED)


@pytest.mark.parametrize('shape', [(12, 16), (1440, 2560), (2160, 3840)])
def testRunningLoopKeepsTwoMatchingInvocationsAndOriginalSavedImages(tmp_path, shape):
    def configure(document, root):
        image = next(node for node in document.workflows['body'].nodes if node.nodeId == 'image')
        image.params.update(height=shape[0], width=shape[1])
    with running(tmp_path, configure=configure, factory=loopProject) as (runtime, client, project, session):
        reply = client.startJob(project, inputs={'items': [5, 2, 3]}, inspectionSessionId=session)
        assert reply.ok, reply.message
        try:
            waitForGate(runtime, reply.job_id)
            history = RunResultHistory()
            definitions = [dict(node.model_dump(), workflowId=workflowId) for workflowId, workflow in
                           runtime.loadedDocument.workflows.items() for node in workflow.nodes]
            history.accept(reply.job_id, definitions, reply.project_revision)
            for event in runtime.eventStore.readMerged(reply.job_id):
                raw = asdict(event)
                raw['payload'] = json.loads(raw.pop('payloadJson'))
                history.applyEvent(raw)
            sources = []
            for older, expected in ((False, 3), (True, 2)):
                history.view('body', 'image')
                history.selectPrevious(older)
                view = history.view('body', 'image')
                record = view['record']
                count = next(item for item in record['io']['outputs']['items'] if item['port'] == 'count')
                assert count['value']['value'] == expected
                assert record['io']['inputs']['items'][0]['value']['value'] == expected
                source = invocationSource(client, project, session, record)
                displayed = pixels(client, project, session, source)
                assert source.nodeRunId == record['nodeRunId'] and source.workflowRunId == record['workflowRunId']
                assert (source.originalHeight, source.originalWidth) == shape
                assert displayed.shape[:2] == (source.height, source.width)
                assert source.width <= 1920 and source.height <= 1080
                assert source.width / source.height == pytest.approx(shape[1] / shape[0], rel=.002)
                assert np.all(displayed == 20 + expected)
                sources.append(source)
                history.view('body', 'save')
                history.selectPrevious(older)
                savedRecord = history.view('body', 'save')['record']
                saved = invocationSource(client, project, session, savedRecord, node='save', port='__saved_result__')
                assert np.all(pixels(client, project, session, saved) == 20 + expected)
            assert len({source.captureId for source in sources}) == 2
            assert runtime.jobRepository.get(reply.job_id).status == 'RUNNING'
            original = cv2.imdecode(np.fromfile(tmp_path / '本地 图片/result.png', np.uint8), cv2.IMREAD_COLOR)
            assert original.shape[:2] == shape and np.all(original == 23)
            first = next(event for event in runtime.eventStore.readMerged(reply.job_id)
                         if event.nodeId == 'image' and event.eventType == 'node.completed')
            expired = client.listNodePreviewSourcesWithMetadata(project, 'body', 'image', jobId=reply.job_id,
                inspectionSessionId=session, workflowRunId=first.workflowRunId, nodeRunId=first.nodeRunId)
            assert not expired.sources
            owned = runtime.runInspectionStore.sessions[session].executions[('body', 'image')]
            assert len(owned) == 2 and all(execution.status == 'node.completed' for execution in owned)
        finally:
            assert client.stopJob(reply.job_id).ok
            assert waitForTerminal(runtime, reply.job_id).status == 'ABORTED'


def testFailedInvocationDoesNotHidePreviousSuccessfulInvocation(tmp_path):
    def configure(document, root):
        next(node for node in document.workflows['body'].nodes if node.nodeId == 'image').params['failIndex'] = 3
    with running(tmp_path, configure=configure, factory=loopProject) as (runtime, client, project, session):
        reply = client.startJob(project, inputs={'items': [5, 2, 3]}, inspectionSessionId=session)
        assert reply.ok and waitForTerminal(runtime, reply.job_id).status == 'FAILED'
        calls = [event for event in runtime.eventStore.readMerged(reply.job_id)
                 if event.nodeId == 'image' and event.eventType in ('node.completed', 'node.failed')]
        assert len(calls) == 3 and calls[-1].eventType == 'node.failed'
        previous = invocationSource(client, project, session, asdict(calls[-2]))
        assert np.all(pixels(client, project, session, previous) == 22)
        failed = client.listNodePreviewSourcesWithMetadata(project, 'body', 'image', jobId=reply.job_id,
            inspectionSessionId=session, workflowRunId=calls[-1].workflowRunId, nodeRunId=calls[-1].nodeRunId)
        assert not failed.sources and 'NOT_COMPLETED' in failed.message
