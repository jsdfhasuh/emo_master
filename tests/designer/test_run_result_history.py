from copy import deepcopy

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


def testThirdAcceptanceEvictsSelectedPreviousAndClearReleasesEverything():
    history = RunResultHistory()
    for job in ('a', 'b'):
        history.accept(job, definitions())
        complete(history, job, 2)
    history.selectPrevious(True)
    history.accept('c', definitions())
    assert list(history.runs) == ['b', 'c']
    assert history.viewJobId == 'c'
    assert '保留范围' in history.notice
    history.clear()
    assert history.retainedBytes == 0 and history.selected is None


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
