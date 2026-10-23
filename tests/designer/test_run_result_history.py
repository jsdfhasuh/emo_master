from copy import deepcopy

import pytest

from emo_master.apps.designer.presenters.node_run_presenter import inspectionText
from emo_master.apps.designer.state.run_result_history import RunResultHistory, RUN_METADATA_LIMIT
from tests.designer.test_node_run_inspection import event


def definitions(name='Original'):
    return [{'workflowId': 'main', 'nodeId': 'node', 'displayName': name,
             'operatorId': 'count', 'outputPorts': {'count': 'integer'}, 'inputPorts': {}}]


def complete(history, job, value):
    raw = event('node.completed', 2, node='node', execution=job, value=value)
    raw['jobId'] = job
    history.applyEvent(raw)


def testAcceptedRunsRotateOnlyOnceAndFreezeDefinitions():
    history = RunResultHistory()
    original = definitions()
    history.accept('a', original, 7)
    original[0]['displayName'] = 'Edited'
    complete(history, 'a', 0)
    history.accept('a', original, 8)
    assert len(history.runs) == 1
    assert history.selected.definitions[('main', 'node')]['displayName'] == 'Original'
    history.accept('b', original, 8)
    complete(history, 'b', False)
    history.selectPrevious(True)
    assert history.selected.jobId == 'a'
    assert history.view('main', 'node')['record']['io']['outputs']['items'][0]['value']['value'] == 0
    assert history.current.inspection.get('main', 'node', 'b')['io']['outputs']['items'][0]['value']['value'] is False
    assert history.selected.revision == 7


def testAcceptanceDoesNotRotateAndThirdInvocationEvictsOnlyThatNode():
    history = RunResultHistory()
    for job in ('a', 'b'):
        history.accept(job, definitions())
        complete(history, job, 2)
    history.selectPrevious(True)
    history.accept('c', definitions())
    assert list(history.runs) == ['a', 'b', 'c']
    assert history.view('main', 'node')['job'].jobId == 'a'
    complete(history, 'c', 3)
    assert list(history.runs) == ['b', 'c']
    assert history.view('main', 'node')['job'].jobId == 'b'
    history.clear()
    assert history.retainedBytes == 0 and history.selected is None


def testThreeInvocationsInOneJobKeepTwoRecordsAndStartFinishRotateOnce():
    history = RunResultHistory()
    history.accept('job', definitions(), 7)
    for index, value in enumerate((5, 2, 3)):
        for kind, sequence in (('node.started', index * 2 + 1), ('node.completed', index * 2 + 2)):
            raw = event(kind, sequence, node='node', execution=str(index), value=value)
            history.applyEvent(raw)
        assert len(history.executions[('main', 'node')]) == min(index + 1, 2)
    assert history.view('main', 'node')['record']['io']['outputs']['items'][0]['value']['value'] == 3
    history.selectPrevious(True)
    previous = history.view('main', 'node')
    assert previous['record']['io']['outputs']['items'][0]['value']['value'] == 2
    assert previous['record']['nodeRunId'] == '1' and previous['history']
    assert history.current.inspection.get('main', 'node', 'job')['nodeRunId'] == '2'


def testSkippedNodeDoesNotDisplaceActualInvocationsAndCancelClosesActiveRecord():
    history = RunResultHistory()
    history.accept('job', definitions())
    history.applyEvent(event('node.started', 1, node='node', execution='first'))
    history.applyEvent(event('node.completed', 2, node='node', execution='first', value=5))
    history.applyEvent(event('node.skipped', 3, node='node', execution='skip'))
    view = history.view('main', 'node')
    assert not view['hasPrevious'] and view['record']['nodeRunId'] == 'first'
    assert view['observation']['status'] == 'SKIPPED'
    history.applyEvent(event('node.started', 4, node='node', execution='second'))
    history.applyEvent({'eventType': 'job.aborted', 'jobId': 'job'})
    assert history.view('main', 'node')['record']['status'] == 'CANCELLED'
    history.selectPrevious(True)
    assert history.view('main', 'node')['record']['nodeRunId'] == 'first'


def testRuntimeAndInspectionContextChangesReleaseHistoryButSameContextDoesNot():
    history = RunResultHistory()
    history.bindContext(('runtime-a', 'session-a'))
    history.accept('a', definitions())
    complete(history, 'a', 5)
    history.bindContext(('runtime-a', 'session-a'))
    assert history.view('main', 'node')['record'] is not None
    history.bindContext(('runtime-b', 'session-b'))
    assert history.current is None and history.retainedBytes == 0


def testSameNodeIdInDifferentWorkflowsKeepsIndependentInvocations():
    history = RunResultHistory()
    history.accept('job', definitions() + [{**definitions()[0], 'workflowId': 'body'}])
    for workflow, value in [('main', 5), ('body', 2)]:
        history.applyEvent(event(node='node', workflow=workflow, value=value))
    assert history.view('main', 'node')['record']['io']['outputs']['items'][0]['value']['value'] == 5
    assert history.view('body', 'node')['record']['io']['outputs']['items'][0]['value']['value'] == 2
    assert not history.view('main', 'node')['hasPrevious']


def testCooperativeCancellationIsShownAsCancelledWithoutChangingCanvasObservation():
    history = RunResultHistory()
    history.accept('job', definitions())
    history.applyEvent(event('node.started', 1, node='node'))
    failed = event('node.failed', 2, node='node')
    failed['code'] = 'E_CANCELLED'
    history.applyEvent(failed)
    assert history.view('main', 'node')['record']['status'] == 'CANCELLED'
    assert history.current.inspection.get('main', 'node', 'job')['status'] == 'FAILED'


@pytest.mark.parametrize('terminal,label,reason', [('ABORTED', '已取消', '取消原因'), ('FAILED', '已中断', '中断原因')])
def testTerminalExecutionHasReadableStatusAndNoStaleOutputs(terminal, label, reason):
    history = RunResultHistory()
    history.accept('job', definitions())
    history.applyEvent(event('node.started', 1, node='node'))
    history.setStatus('job', terminal)
    text = inspectionText(history.view('main', 'node')['record'], 'job')
    assert label in text and reason in text
    assert '无结果（本次执行未完成）' in text and '等待完成' not in text


def testNewestFailedInvocationNeverRecoversPreviousSuccessfulValues():
    history = RunResultHistory()
    history.accept('a', definitions())
    complete(history, 'a', 123)
    for kind, seq, execution in [('node.started', 3, 'new'), ('node.failed', 4, 'new'),
                                  ('node.completed', 5, 'a')]:
        raw = event(kind, seq, node='node', execution=execution, value=999)
        raw['jobId'] = 'a'
        history.applyEvent(raw)
    record = history.view('main', 'node')['record']
    assert record['status'] == 'FAILED' and record['io']['outputs'] is None
    terminal = {'eventType': 'job.failed', 'jobId': 'a'}
    history.applyEvent(terminal)
    before = deepcopy(record)
    complete(history, 'a', 555)
    assert history.view('main', 'node')['record'] == before


def testSummaryAndFrozenPortMetadataShareTheSameBudget():
    history = RunResultHistory()
    nodes = [{**definitions()[0], 'nodeId': str(i), 'displayName': '长' * 10000,
              'outputPorts': {str(p): 'image' for p in range(300)}} for i in range(200)]
    history.accept('a', nodes)
    for i in range(160):
        raw = event('node.completed', i + 1, node=str(i), value=2)
        raw['jobId'] = 'a'
        history.applyEvent(raw)
    assert len(history.current.definitions) <= 64
    assert history.current.retainedBytes <= RUN_METADATA_LIMIT
    assert history.view('other-workflow', '0')['record'] is None
    assert 'E_INSPECTION_METADATA_BUDGET' in history.current.notice


def testNodeCountEvictionIsExplicitInsteadOfLookingLikeAnUnexecutedNode():
    history = RunResultHistory()
    nodes = [{**definitions()[0], 'nodeId': str(i)} for i in range(65)]
    history.accept('a', nodes)
    assert len(history.current.definitions) == 64
    assert '节点上限64' in history.current.notice
    for i in range(65):
        raw = event('node.completed', i + 1, node=str(i), value=i)
        raw['jobId'] = 'a'
        history.applyEvent(raw)
    assert history.view('main', '0')['record'] is None
    assert history.view('main', '64')['record']['status'] == 'COMPLETED'
    assert 'E_INSPECTION_METADATA_BUDGET' in history.current.notice
    assert '最早更新的记录已释放' in history.current.notice
    assert history.current.retainedBytes <= RUN_METADATA_LIMIT
