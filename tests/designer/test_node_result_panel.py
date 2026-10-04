from types import SimpleNamespace

from tests.designer.test_run_inspector_ui import windowWithNode, deliver


def testUnifiedPanelUsesRealValuesAndPreviousRunWithoutChangingDraft():
    window, node = windowWithNode()
    history = window.nodeResultCoordinator.history
    panel = window.nodeResultCoordinator.panel
    before = window.flowModel.toProjectGraph()
    window._setCurrentJobId('job')
    deliver(window, node, value=0)
    assert 'count = 0' in panel.values.toPlainText()
    assert panel.tabs.currentWidget() is panel.values
    assert not panel.tabs.isTabVisible(0)
    window._setCurrentJobId(None)  # Startup attempt, before a new acceptance.
    assert history.current.jobId == 'job' and history.previous is None
    window._setCurrentJobId('b')
    raw = {'eventType': 'node.completed', 'jobId': 'b', 'workflowId': 'main', 'nodeId': node,
           'nodeRunId': 'b-node', 'sequence': 1, 'payload': {'outputs': {'count': False}}}
    window.runtimeController._onRuntimeEvent(SimpleNamespace(**raw))
    assert 'count = false' in panel.values.toPlainText()
    panel.previous.click()
    assert 'count = 0' in panel.values.toPlainText()
    assert '只读（历史）' in panel.origin.text()
    assert window.currentJobId == 'b'
    assert window.flowModel.toProjectGraph() == before


def testDeclaredButMissingImageKeepsImageTabAndMissingHistoryDoesNotUseDraft():
    window, node = windowWithNode()
    window.flowModel.nodes[node].outputPorts['image'] = 'image'
    window._setCurrentJobId('a')
    panel = window.nodeResultCoordinator.panel
    assert panel.tabs.isTabVisible(0)
    assert panel.tabs.currentIndex() == 0
    assert panel.ports.currentData() == 'image'
    assert not panel.enlarge.isEnabled()
    window.addNodeFromOperatorPayload({'operatorId': 'new', 'displayName': '后来新增',
        'inputPorts': {}, 'outputPorts': {'value': 'number'}, 'paramSchema': {}})
    assert '上一轮没有此节点' in panel.values.toPlainText()
