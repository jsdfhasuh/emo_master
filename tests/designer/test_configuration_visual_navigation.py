from copy import deepcopy
from types import SimpleNamespace

from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QPushButton, QTreeWidget, QStyle, QStyleOptionViewItem

from emo_master.apps.designer.ui.online_graph import OnlineGraphDialog
from emo_master.apps.designer.ui.read_only_graph import changedNodes
from tests.designer.test_device_workspace import waitUntil
from tests.designer.test_project_configuration_ui import setupDialog
from tests.runtime.test_device_runtime import simulatedProject


def graphData(payload, revision):
    return dict(runtimeInstanceId='test-instance', snapshot=dict(payload=deepcopy(payload),
        documentRevision=revision, executionRevision=revision), ports={}, events=[], jobs=[],
        history=[dict(documentRevision=1, executionRevision=1), dict(documentRevision=2, executionRevision=2)])


def testClickLocationDoesNotToggleSelectionAndCanNavigate(ownedDesignerWindow, tmp_path):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
        online = dialog.snapshot['payload']['workflows']['main']['nodes']
        next(n for n in online if n['nodeId'] == 'capture')['params'] = {'testThreshold': 12}
        dialog._showSnapshot(dialog.snapshot)
        dialog.show()
        waitUntil(lambda: dialog.isVisible())
        row = next(r for r in range(dialog.table.rowCount())
                   if dialog.table.item(r, 0).data(Qt.UserRole).endswith('/params/testThreshold'))
        item = dialog.table.item(row, 0)
        dialog.table.scrollToItem(item)
        index = dialog.table.model().index(row, 0)
        option = QStyleOptionViewItem()
        dialog.locationDelegate.initStyleOption(option, index)
        option.rect = dialog.table.visualItemRect(item)
        style = dialog.table.style()
        checkbox = style.subElementRect(QStyle.SE_ItemViewItemCheckIndicator, option, dialog.table)
        QTest.mouseClick(dialog.table.viewport(), Qt.LeftButton, pos=checkbox.center())
        assert item.checkState() == Qt.Checked
        assert not hasattr(dialog, 'lastChangeDialog')
        text = style.subElementRect(QStyle.SE_ItemViewItemText, option, dialog.table)
        QTest.mouseClick(dialog.table.viewport(), Qt.LeftButton, pos=text.center())
        view = dialog.lastChangeDialog
        assert item.checkState() == Qt.Checked
        assert view.workflow.currentData() == 'main' and view.node.currentData() == 'capture'
        assert 'testThreshold' in view.details.toPlainText() and '12' in view.details.toPlainText()
        assert '连线与位置对照' in view.details.toPlainText()
        assert not calls and not view.view.isInteractive()
        button = next(b for b in view.findChildren(QPushButton) if b.text() == '在工程中定位')
        button.click()
        assert dialog.closed and window.activeWorkflowId == 'main'
        assert window.flowModel.selectedNodeId == 'capture'
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
    finally:
        if hasattr(dialog, 'lastChangeDialog'):
            dialog.lastChangeDialog.reject()
        dialog.reject()


def testDeletedNodeAndVariableHaveReadableBeforeAfterViews(ownedDesignerWindow, tmp_path):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        oldFlow = dialog.snapshot['payload']['workflows']['main']
        removed = deepcopy(oldFlow['nodes'][0])
        removed.update(nodeId='removed', displayName='已删除的采集节点')
        oldFlow['nodes'].append(removed)
        dialog._showSnapshot(dialog.snapshot)
        row = next(r for r in range(dialog.table.rowCount()) if dialog.table.item(r, 0).data(Qt.UserRole).endswith('/nodes/removed'))
        dialog.openChangeLocation(row)
        view = dialog.lastChangeDialog
        assert view.node.currentData() == 'removed'
        assert '仅在对照版本中存在' in view.details.toPlainText()
        assert view.beforeScene.getNodeCenter('removed') is not None
        assert view.scene.getNodeCenter('removed') is None
        assert any(not b.isEnabled() and '本地已删除' in b.text() for b in view.findChildren(QPushButton))
        view.reject()
        dialog.local['globalVariables'] = {'index': dict(name='车辆序号', initialValue=0, type='integer')}
        dialog._showSnapshot(dialog.snapshot)
        row = next(r for r in range(dialog.table.rowCount()) if dialog.table.item(r, 0).data(Qt.UserRole) == '/globalVariables/index')
        dialog.openChangeLocation(row)
        tree = dialog.lastChangeDialog.findChild(QTreeWidget)
        assert tree.headerItem().text(1) == '运行端原内容'
        children = [tree.topLevelItem(0).child(i) for i in range(tree.topLevelItem(0).childCount())]
        assert any(n.text(0) == '名称' and n.text(2) == '车辆序号' for n in children)
        assert not calls
    finally:
        if hasattr(dialog, 'lastChangeDialog'):
            dialog.lastChangeDialog.reject()
        dialog.reject()


def testHistoryComparisonHighlightsChangesAndGraphClicksAreReadOnly(retainedQtApplication, tmp_path):
    before = simulatedProject()
    after = deepcopy(before)
    node = next(n for n in after['workflows']['main']['nodes'] if n['nodeId'] == 'capture')
    node['params'] = {'threshold': 7}
    node['paramSchema'] = dict(properties=dict(threshold=dict(title='检测阈值')))
    removed = deepcopy(before['workflows']['main']['nodes'][0])
    removed.update(nodeId='old', displayName='旧采集节点')
    before['workflows']['main']['nodes'].append(removed)
    added = deepcopy(node)
    added.update(nodeId='new', displayName='新增检测节点')
    after['workflows']['main']['nodes'].append(added)
    snapshots = {1: graphData(before, 1), 2: graphData(after, 2)}
    calls = []
    def read(project, revision, job):
        calls.append((revision, job))
        return deepcopy(snapshots[revision or 2])
    dialog = OnlineGraphDialog(SimpleNamespace(getOnlineGraphSnapshot=read), before['project']['projectId'])
    try:
        dialog.show()
        waitUntil(lambda: dialog.pending is None)
        assert calls == [(0, ''), (1, '')]
        assert dialog.beforePanel.isVisible()
        assert '新增 1 个' in dialog.diffSummary.text() and '删除 1 个' in dialog.diffSummary.text()
        assert dialog.view.transform().m11() >= .7
        dialog.view.focusNode('new')
        point = dialog.view.mapFromScene(*dialog.scene.getNodeCenter('new'))
        QTest.mouseClick(dialog.view.viewport(), Qt.LeftButton, pos=point)
        assert dialog.node.currentData() == 'new'
        assert '所选版本新增' in dialog.details.toPlainText()
        assert '检测阈值' in dialog.details.toPlainText() and '7' in dialog.details.toPlainText()
        dialog.selectNode('old')
        assert '仅在对照版本' in dialog.details.toPlainText()
        dialog.technicalDetails.click()
        assert '"comparison"' in dialog.details.toPlainText()
        dialog.technicalDetails.click()
        originalTransform = dialog.view.transform()
        dialog.render()
        assert dialog.view.transform() == originalTransform
        assert snapshots[1]['snapshot']['payload'] == before
        assert snapshots[2]['snapshot']['payload'] == after
        assert dialog.grab().save(str(tmp_path / 'history-comparison.png'))
        dialog.compare.setCurrentIndex(dialog.compare.findData(None))
        waitUntil(lambda: dialog.pending is None)
        assert not dialog.beforePanel.isVisible()
        assert '没有可用的对照版本' in dialog.diffSummary.text()
    finally:
        dialog.reject()


def testPositionAndConnectionOnlyChangesAreMarked():
    before = simulatedProject()['workflows']['main']
    after = deepcopy(before)
    key = before['nodes'][0]['nodeId']
    after['layout'] = dict(nodePositions={key: dict(x=100, y=200)})
    assert key in changedNodes(after, before)
    after = deepcopy(before)
    after['edges'] = [dict(fromNode=key, fromPort='image', toNode='other', toPort='image')]
    assert key in changedNodes(after, before)
