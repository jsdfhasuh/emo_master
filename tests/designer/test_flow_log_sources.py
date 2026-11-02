"""Flow operations keep their log origin independently of the active workspace."""
from tests.designer.test_runtime_log_usability import Client, Settings, addNode


def testGraphValidationRemainsFlowLogWhilePageWorkspaceIsVisible(ownedDesignerWindow):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    window.pageCoordinator.showPages()
    window._clearRuntimeLogCache()

    window.validateGraph()

    entry, = window.logEntries
    assert entry.message == '流程图校验通过'
    assert entry.source == 'flow_designer' and entry.designArea() == 'flow'


def testNodeParameterSuccessAndFailureUseFlowOrigin(ownedDesignerWindow):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1, nodeId='number')
    window._clearRuntimeLogCache()

    assert window.applyNodeParams('number', {'value': 2})
    assert not window.applyNodeParams('missing', {})

    updated = next(entry for entry in window.logEntries if entry.message == '参数已更新：number')
    failed = next(entry for entry in window.logEntries if entry.message == '参数应用失败：节点不存在')
    assert (updated.source, updated.level) == ('flow_designer', 'INFO')
    assert (failed.source, failed.level) == ('flow_designer', 'ERROR')
    assert updated.designArea() == failed.designArea() == 'flow'
    assert window.flowModel.nodes['number'].params == {'value': 2}
