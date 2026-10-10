from copy import deepcopy

import pytest

from emo_master.apps.designer.presenters.node_run_presenter import sqliteReceiptText
from emo_master.apps.designer.state.node_run_inspection import MAX_NODE_BYTES, NodeRunInspection
from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel
from emo_master.core.contracts.run_inspection import encodedBytes
from emo_master.core.contracts.sqlite_writer import receiptSummary
from tests.designer.test_node_run_inspection import event


@pytest.mark.parametrize('status,rows', [('COMMITTED', 1), ('SKIPPED', 0), ('FAILED', 0), ('UNKNOWN', None)])
def testReceiptRetentionUsesActualExecutionAndStatusWithoutExceedingBudget(status, rows):
    raw = event(node='writer')
    identity = {key: raw[key] for key in ('jobId', 'workflowId', 'nodeId', 'nodeRunId', 'workflowRunId')}
    receipt = {'writeId': 'actual-write', 'status': status, 'rowsAffected': rows, 'primaryKey': None,
               'execution': identity, 'elapsedMs': 0, 'error': {'code': 'E_SQLITE', 'message': '\x00' * 20000}}
    raw['payload']['diagnostics'] = {'sqliteReceipt': receipt}
    state = NodeRunInspection()
    assert state.applyEvent(raw)
    record = state.get('main', 'writer', 'job')
    assert record['sqliteReceipt']['rowsAffected'] == rows
    assert encodedBytes(record) <= MAX_NODE_BYTES
    assert status in sqliteReceiptText(record['sqliteReceipt'])
    assert '0.000 ms' in sqliteReceiptText(record['sqliteReceipt'])
    for field in identity:
        mismatch = deepcopy(receipt)
        mismatch['execution'][field] = 'another invocation'
        assert receiptSummary(mismatch, identity) is None
    # Next failed invocation must not reuse the earlier successful receipt.
    assert state.applyEvent(event('node.started', 3, node='writer', execution='new'))
    assert 'sqliteReceipt' not in state.get('main', 'writer', 'job')
    assert state.applyEvent(event('node.failed', 4, node='writer', execution='new'))
    assert 'sqliteReceipt' not in state.get('main', 'writer', 'job')


def testMalformedReceiptCannotCrashRemoteInspectorOrInventCommit():
    identity = {'jobId': 'job'}
    receipt = {'writeId': 'id', 'status': 'COMMITTED', 'rowsAffected': 1, 'primaryKey': 0,
               'execution': identity, 'elapsedMs': 0, 'error': None}
    for field, value in [('status', []), ('rowsAffected', True), ('rowsAffected', 0),
                         ('writeId', '\ud800'), ('writeId', ''), ('execution', {})]:
        assert receiptSummary({**receipt, field: value}, identity) is None
    normalized = receiptSummary({**receipt, 'elapsedMs': 2**10000, 'error': {'message': [], 'code': {}}}, identity)
    assert normalized['elapsedMs'] is None and normalized['primaryKey'] == 0
    assert normalized['error'] == {'message': '', 'code': ''}
    assert receiptSummary({**receipt, 'elapsedMs': float('nan')}, identity)['elapsedMs'] is None


def testSelectedCanvasClearNotifiesOnlyAfterWrappersAndDependencyHintsAreRemoved(ownedFlowScene, retainedQtApplication):
    scene = ownedFlowScene()
    for node in ('source', 'writer'):
        scene.addFlowNode(FlowNodeViewModel(nodeId=node, title=node, x=0, y=0,
            inputPorts={}, outputPorts={}, operatorId='vision.io.sqlite_writer' if node == 'writer' else ''))
    scene.setBindingDependencies('writer', ['source'])
    scene.setNodeSelected('writer')
    observations = []
    def selected():
        # Prior crash inspected wrappers Qt had already deleted during clear().
        observations.append([item.isSelected() for item in scene._nodeItems.values()])
    scene.selectionChanged.connect(selected)
    scene.clearGraph()
    retainedQtApplication.processEvents()
    assert observations == [[]]
    assert not scene._nodeItems and not scene._bindingItems and not scene._routeTimer.isActive()
