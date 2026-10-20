from emo_master.apps.designer.presenters.node_run_presenter import inspectionText
from emo_master.apps.designer.state.node_run_inspection import MAX_INSPECTED_NODES, MAX_NODE_BYTES, NodeRunInspection
from emo_master.apps.designer.ui.runtime_panel import RuntimePanelState
from emo_master.core.contracts.run_inspection import encodedBytes, finishInspection, startInspection


def event(kind='node.completed', sequence=2, *, job='job', workflow='main', node='count', execution='invocation', value=0):
    return {'eventType': kind, 'sequence': sequence, 'jobId': job, 'workflowId': workflow,
            'nodeId': node, 'nodeRunId': execution, 'workflowRunId': 'workflow-run',
            'iterationPath': [2], 'timestampMs': 1800000000123,
            'payload': {'ioSummary': finishInspection(startInspection({'left': 0}), {'count': value}),
                        'metrics': {'latencyMs': 1.5}, 'code': 'E_SAMPLE', 'message': 'actual failure'}}


def testDuplicateOutOfOrderAndOldCompletionCannotRestorePreviousOutput():
    state = NodeRunInspection()
    assert state.applyEvent(event(value=7))
    assert not state.applyEvent(event(value=8))
    assert not state.applyEvent(event(sequence=1, value=9))
    assert state.get('main', 'count', 'job')['io']['outputs']['items'][0]['value']['value'] == 7
    assert state.applyEvent(event('node.started', 3, execution='new'))
    assert state.get('main', 'count', 'job')['io']['outputs'] is None
    assert not state.applyEvent(event(sequence=4, value=10))
    assert state.applyEvent(event('node.failed', 5, execution='new'))
    record = state.get('main', 'count', 'job')
    assert record['io']['outputs'] is None
    assert 'actual failure' in inspectionText(record, 'job')
    # A skipped invocation has no started event in the real Runner.
    assert state.applyEvent(event('node.skipped', 6, execution='skipped'))
    assert state.get('main', 'count', 'job')['status'] == 'SKIPPED'
    assert not state.applyEvent(event(sequence=7, execution='new', value=20))


def testCrossWorkflowAndJobAndRepeatedInvocationsStaySeparate():
    state = RuntimePanelState()
    state.setActiveJob('job')
    state.applyEvent(event(value=False))
    state.applyEvent(event(workflow='body', value=42))
    assert 'count = false' in inspectionText(state.nodeInspection.get('main', 'count', 'job'), 'job')
    assert state.nodeInspection.get('body', 'count', 'job')['io']['outputs']['items'][0]['value']['value'] == 42
    state.applyEvent(event('node.started', 3, execution='second'))
    state.applyEvent(event(sequence=4, execution='second', value=0))
    assert 'count = 0' in inspectionText(state.nodeInspection.get('main', 'count', 'job'), 'job')
    state.setActiveJob('next')
    state.applyEvent(event(sequence=99, value=55))
    assert state.nodeInspection.get('main', 'count', 'next') is None
    assert state.nodeInspection.retainedBytes == 0
    state.setActiveJob(None)
    state.applyEvent(event(job='next'))
    assert state.nodeInspection.retainedBytes == 0


def testRetentionLimitsIncludeEscapedIdentityTextAndMaliciousPayloads():
    state = NodeRunInspection()
    for i in range(MAX_INSPECTED_NODES + 50):
        raw = event(node=str(i), value='\x00' * 10000)
        raw.update(workflowId='\x00' * 128, jobId='\x00' * 128,
                   workflowRunId='\x00' * 128, nodeRunId='\x00' * 128,
                   iterationPath=[1000000] * 1000, sequence=2**10000)
        raw['payload'].update(message='\x00' * 10000, diagnostics={'text': '\x00' * 10000},
                              metrics={str(n): '\x00' * 10000 for n in range(100)})
        assert state.applyEvent(raw)
        assert encodedBytes(state.get(raw['workflowId'], str(i), raw['jobId'])) <= MAX_NODE_BYTES
    assert state.retainedBytes <= MAX_INSPECTED_NODES * MAX_NODE_BYTES
    assert state.get('\x00' * 128, '0', '\x00' * 128) is None
    for field in ('workflowId', 'nodeId', 'jobId', 'workflowRunId', 'nodeRunId'):
        raw = event()
        raw[field] = []
        assert not state.applyEvent(raw)
    raw = event()
    raw.update(iterationPath='invalid', timestampMs=2**10000)
    assert state.applyEvent(raw)
    assert 'count = 0' in inspectionText(state.get('main', 'count', 'job'), 'job')
    state.clear()
    assert state.retainedBytes == 0


def testOlderRuntimeIsHonestAboutMissingInputsAndUnavailableOutput():
    state = NodeRunInspection()
    raw = event()
    raw['payload'] = {'outputs': {'count': 0, 'result': False}}
    assert state.applyEvent(raw)
    text = inspectionText(state.get('main', 'count', 'job'), 'job')
    assert 'count = 0' in text and 'result = false' in text
    assert '旧事件未提供输入摘要' in text
    raw = event('node.failed', sequence=3)
    raw['payload'] = {'outputs': {'count': 999}, 'message': 'failed'}
    state.applyEvent(raw)
    assert '999' not in inspectionText(state.get('main', 'count', 'job'), 'job')
