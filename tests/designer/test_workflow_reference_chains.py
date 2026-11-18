"""Complete caller paths from the live draft, without running any workflows."""
from copy import deepcopy
from math import ceil
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QPoint, QSettings, Qt
from PySide2.QtTest import QTest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.designer.test_workflow_entry_markers import readableNativeFont  # noqa: F401
from tests.designer.test_workflow_history_navigation import settle


@pytest.fixture
def window(ownedDesignerWindow, tmp_path):
    store = WorkflowStore()
    store.renameWorkflow('main', 'Production entry')
    for key, name in [('station', 'Station 2 | Continuous operation'),
                      ('coordinates', 'Station 2 | Screw coordinates'),
                      ('holes', 'Station 2 | Hole measurement'), ('offline', 'Offline inspection')]:
        store.addWorkflow(name, key)
    store.get('main').nodes.append(dict(nodeId='dispatch-station', kind='dispatch', displayName='Start station',
        params=dict(targets=[dict(targetSlotId='station-slot', workflowId='station', inputs={}, queueCapacity=1)])))
    store.get('station').nodes.append(dict(nodeId='repeat-coordinates', kind='loop', displayName='Inspect screws',
        loop=dict(mode='repeat', contractVersion=2, repeatCount=2, maxIterations=2, bodyWorkflowId='coordinates')))
    store.get('coordinates').nodes.append(dict(nodeId='measure', kind='subflow', displayName='Measure hole',
                                             targetWorkflowId='holes'))
    payload = store.toPayload('Call chain navigation')
    assert payload['schemaVersion'] == '2.10' and payload['presentation']['schemaVersion'] == '1.3'
    document = ProjectDocument.model_validate(payload)
    WorkflowCompiler({}).compile(document)
    path = tmp_path / 'call-chains.emoproj'
    saveProject(path, payload)
    assert loadProjectDocument(path) == document

    def forbidden(*args, **kwargs):
        pytest.fail('call chain navigation must not start Runtime or devices')
    w = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: [], startJob=forbidden,
        loadProject=lambda _: SimpleNamespace(ok=True, message='local stub')),
        settingsStore=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
    assert w.loadProjectDirectory(str(path), confirmed=True)
    w.show()
    settle()
    w.resize(1250, 820)
    w.activateWorkflow('holes')
    settle()
    return w


def pathIds(nav, target='holes'):
    return [([ref['sourceNodeId'] for ref in chain], cycle) for chain, cycle in nav.referenceChains(target)]


def node(window, workflow, nodeId):
    return next(n for n in window.workflowStore.get(workflow).nodes if n['nodeId'] == nodeId)


def chainItems(popup, index=0):
    items = []
    item = popup.tree.topLevelItem(index)
    while item is not None:
        items.append(item)
        assert item.childCount() <= 1
        item = item.child(0)
    return items


def testCompleteChainIncludesRootEveryCallAndTargetAndNavigatesBack(window):
    nav = window.workflowNavigation
    assert pathIds(nav) == [(['dispatch-station', 'repeat-coordinates', 'measure'], False)]
    nav.showReferences()
    settle()
    popup = nav.popup
    assert popup.tree.topLevelItemCount() == 1
    items = chainItems(popup)
    assert [item.text(0) for item in items] == [
        'Production entry\n起点 · 调用节点：Start station',
        'Station 2 | Continuous operation\n调用节点：Inspect screws',
        'Station 2 | Screw coordinates\n调用节点：Measure hole',
        'Station 2 | Hole measurement\n当前工作流',
    ]
    assert all(item.isExpanded() for item in items[:-1])
    assert [item.data(0, Qt.UserRole)['workflowId'] for item in items] == ['main', 'station', 'coordinates', 'holes']
    for item, nodeId in zip(items, ['dispatch-station', 'repeat-coordinates', 'measure']):
        assert nodeId not in item.text(0)
        assert f'节点 ID：{nodeId}' in item.toolTip(0)
        assert ' → ' not in item.toolTip(0)
    assert '调用方式：启动工作流' in items[0].toolTip(0)
    assert '调用方式：重复执行' in items[1].toolTip(0)
    assert '调用方式：子工作流调用' in items[2].toolTip(0)
    assert '1 \u6761\u94fe\u8def' in popup.title.text()
    for query, step in [('Production entry', 0), ('dispatch-station', 0), ('Inspect screws', 1),
                        ('repeat-coordinates', 1), ('Screw coordinates', 2), ('measure', 2), ('holes', 3)]:
        popup.search.setText(query)
        assert not items[0].isHidden() and popup.tree.currentItem() is items[step]
        assert all(item.isExpanded() for item in items[:-1])
    item = items[-2]
    popup.tree.scrollToItem(item)
    settle()
    QTest.mouseClick(popup.tree.viewport(), Qt.LeftButton, pos=popup.tree.visualItemRect(item).center())
    assert not popup.isVisible()
    assert window.activeWorkflowId == 'coordinates' and window.flowScene.getSelectedNodeId() == 'measure'
    assert nav.move(-1) and window.activeWorkflowId == 'holes'


def testSharedAncestorsKeepDistinctCallSitesAndUnconnectedRoot(window):
    nav = window.workflowNavigation
    window.workflowStore.get('main').nodes.append(dict(nodeId='again', kind='subflow', displayName='Start station',
                                                      targetWorkflowId='station'))
    window.workflowStore.get('offline').nodes.append(dict(nodeId='offline-call', kind='subflow',
        displayName='Inspect offline', targetWorkflowId='coordinates'))
    window.renameWorkflow('offline', 'Production entry')
    assert pathIds(nav) == [
        (['dispatch-station', 'repeat-coordinates', 'measure'], False),
        (['again', 'repeat-coordinates', 'measure'], False),
        (['offline-call', 'measure'], False),
    ]
    nav.showReferences()
    assert popupCount(window) == 3
    rows = [nav.popup.tree.topLevelItem(i).text(0) for i in range(3)]
    assert 'Production entry \u00b7 main' in rows[0]
    assert 'Production entry \u00b7 offline' in rows[2]
    nav.popup.search.setText('offline-call')
    assert [nav.popup.tree.topLevelItem(i).isHidden() for i in range(3)] == [True, True, False]
    QTest.keyClick(nav.popup.search, Qt.Key_Return)
    assert window.activeWorkflowId == 'offline' and window.flowScene.getSelectedNodeId() == 'offline-call'


def popupCount(window):
    return window.workflowNavigation.popup.tree.topLevelItemCount()


def testQueryCapturesLiveGraphWithoutApplyingDraftOrChangingProject(window):
    w, nav = window, window.workflowNavigation
    w.activateWorkflow('main')
    live = w.flowModel.nodes['dispatch-station']
    live.params['targets'][0]['workflowId'] = 'coordinates'
    live.displayName = 'Live unsaved caller'
    session = w.pageCoordinator.session
    before = (deepcopy(w.flowModel.toProjectGraph()), deepcopy(w.workflowStore.workflows),
              deepcopy(session.payload()), deepcopy(session._undo), deepcopy(session._redo), session.dirty,
              Path(w.loadedProjectPath).read_bytes())
    jobs = {key: (id(run), run.jobId) for key, run in w.runtimeController.runs.items()}
    nav.showReferences('holes')
    assert pathIds(nav) == [(['dispatch-station', 'measure'], False), (['repeat-coordinates', 'measure'], False)]
    assert 'Live unsaved caller' in nav.popup.tree.topLevelItem(0).text(0)
    QTest.keyClick(nav.popup.search, Qt.Key_Escape)
    after = (w.flowModel.toProjectGraph(), w.workflowStore.workflows, session.payload(),
             session._undo, session._redo, session.dirty, Path(w.loadedProjectPath).read_bytes())
    assert after == before
    assert {key: (id(run), run.jobId) for key, run in w.runtimeController.runs.items()} == jobs


@pytest.mark.parametrize('changed', ['main', 'station', 'coordinates'])
def testChangedLinkAnywhereRejectsStalePath(window, changed):
    nav = window.workflowNavigation
    nav.showReferences()
    if changed == 'main':
        node(window, 'main', 'dispatch-station')['params']['targets'][0]['workflowId'] = 'offline'
    elif changed == 'station':
        node(window, 'station', 'repeat-coordinates')['loop']['bodyWorkflowId'] = 'offline'
    else:
        node(window, 'coordinates', 'measure')['targetWorkflowId'] = 'offline'
    before = list(nav.history.entries), nav.history.index
    QTest.keyClick(nav.popup.search, Qt.Key_Return)
    assert window.activeWorkflowId == 'holes'
    assert '\u8c03\u7528\u6765\u6e90\u5df2\u53d8\u5316' in window.statusBar().currentMessage()
    assert (nav.history.entries, nav.history.index) == before


@pytest.mark.parametrize('source', ['coordinates', 'holes'])
def testCyclicDraftStopsAtRepeatedWorkflowWithoutHidingNormalPath(window, source):
    nav = window.workflowNavigation
    window.activateWorkflow('offline')
    window.workflowStore.get(source).nodes.append(dict(nodeId='cycle', kind='subflow', targetWorkflowId='coordinates'))
    paths = pathIds(nav)
    assert paths[0] == (['dispatch-station', 'repeat-coordinates', 'measure'], False)
    assert paths[1] == (['cycle', 'measure'], True)
    nav.showReferences('holes')
    assert popupCount(window) == 2
    assert '\u5faa\u73af\u5f15\u7528' in nav.popup.tree.topLevelItem(1).text(0)


@pytest.mark.parametrize('mode', ['workflow', 'globalVariable'])
def testWhileConditionUsesExistingExecutableReferenceContract(window, mode):
    loop = node(window, 'station', 'repeat-coordinates')['loop']
    loop.update(mode='while', conditionMode=mode, conditionWorkflowId='holes')
    chains = window.workflowNavigation.referenceChains('holes')
    relations = [[ref['relation'] for ref in chain] for chain, _cycle in chains]
    expected = [['dispatch', 'while-body', 'subflow']]
    if mode == 'workflow':
        expected.insert(0, ['dispatch', 'while-condition'])
    assert relations == expected


def testNoCallersAndEmptySearchStayInPlace(window):
    nav = window.workflowNavigation
    assert nav.referenceChains('main') == []
    nav.showReferences('main')
    assert popupCount(window) == 0 and nav.popup.empty.isVisible()
    nav.showReferences()
    nav.popup.search.setText('no-such-call-chain')
    assert nav.popup.empty.isVisible() and nav.popup.tree.currentItem() is None
    QTest.keyClick(nav.popup.search, Qt.Key_Return)
    assert nav.popup.isVisible() and window.activeWorkflowId == 'holes'


@pytest.mark.parametrize('width', [380, 560])
def testFullChainWrapsWithoutElisionOrHorizontalScrolling(window, tmp_path, width):
    popup = window.workflowNavigation.popup
    window.workflowNavigation.showReferences()
    popup.resize(width, 430)
    settle()
    previous = None
    for item in chainItems(popup):
        rect = popup.tree.visualItemRect(item)
        index = popup.tree.indexFromItem(item)
        option = popup.tree.viewOptions()
        delegate = popup.tree.itemDelegate()
        delegate.initStyleOption(option, index)
        document = delegate._document(option, rect.width(), index)
        assert document.toPlainText() == item.text(0)
        assert rect.height() >= ceil(document.size().height()) + 6
        if previous is not None:
            assert previous.bottom() < rect.top()
            assert previous.left() < rect.left()
        previous = rect
    popup.tree.scrollToItem(chainItems(popup)[-1])
    settle()
    assert popup.tree.viewport().rect().contains(popup.tree.visualItemRect(chainItems(popup)[-1]))
    assert popup.tree.horizontalScrollBar().maximum() == 0
    assert popup.tree.textElideMode() == Qt.ElideNone
    out = Path(os.environ.get('EMO_NAV_CHAIN_QA_DIR', str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    assert popup.grab().save(str(out / f'call-chain-{width}.png'))


def testOversizedChainCanScrollToItsFinalWorkflow(window):
    for key in ['main', 'station', 'coordinates']:
        window.renameWorkflow(key, window.workflowStore.get(key).name + ' long workflow name' * 30)
    window.workflowNavigation.showReferences()
    popup = window.workflowNavigation.popup
    popup.resize(380, 280)
    settle()
    item = popup.tree.topLevelItem(0)
    viewport = popup.tree.viewport().rect()
    assert popup.tree.visualItemRect(item).height() > viewport.height()
    scrollbar = popup.tree.verticalScrollBar()
    assert scrollbar.maximum() > 0
    scrollbar.setValue(popup.tree.visualItemRect(item).bottom() - viewport.bottom())
    settle()
    assert 0 <= popup.tree.visualItemRect(item).bottom() <= viewport.bottom()
    scrollbar.setValue(scrollbar.maximum())
    settle()
    assert viewport.contains(popup.tree.visualItemRect(chainItems(popup)[-1]))


@pytest.mark.parametrize('step', [0, 1, 2, 3])
def testEachHierarchyStepLocatesItsOwnWorkflowAndCaller(window, step):
    nav = window.workflowNavigation
    nav.showReferences()
    settle()
    popup = nav.popup
    item = chainItems(popup)[step]
    destination = item.data(0, Qt.UserRole)
    popup.tree.scrollToItem(item)
    settle()
    QTest.mouseClick(popup.tree.viewport(), Qt.LeftButton, pos=popup.tree.visualItemRect(item).center())
    assert not popup.isVisible()
    assert window.activeWorkflowId == destination['workflowId']
    assert window.flowScene.getSelectedNodeId() == destination['nodeId']
    if step < 3:
        assert nav.move(-1) and window.activeWorkflowId == 'holes'


def testCollapseAndSearchKeepWholeChainAndDoNotActivateHiddenDescendants(window):
    nav = window.workflowNavigation
    nav.showReferences()
    settle()
    popup = nav.popup
    items = chainItems(popup)
    rect = popup.tree.visualItemRect(items[0])
    branch = QPoint(rect.left() - popup.tree.indentation() // 2, rect.center().y())
    QTest.mouseClick(popup.tree.viewport(), Qt.LeftButton, pos=branch)
    assert popup.isVisible() and not items[0].isExpanded()
    popup._choose(items[1])
    assert popup.isVisible() and window.activeWorkflowId == 'holes'
    popup.search.setText('repeat-coordinates')
    assert all(item.isExpanded() for item in items[:-1])
    assert popup.tree.currentItem() is items[1]
    popup.search.setText('missing-chain')
    popup._choose(items[1])
    assert popup.isVisible() and window.activeWorkflowId == 'holes'
    popup.search.clear()
    assert not items[0].isHidden()


def testReferenceHierarchyDoesNotLeakIntoFlatDefinitionChoices(window):
    nav = window.workflowNavigation
    nav.showReferences()
    assert nav.popup.tree.indentation() > 0
    nav.popup.hide()
    window.activateWorkflow('station')
    window.flowModel.nodes['repeat-coordinates'].loop.update(
        mode='while', conditionMode='workflow', conditionWorkflowId='offline')
    window.flowScene.setNodeSelected('repeat-coordinates')
    nav.goToDefinition()
    assert nav.popup.tree.topLevelItemCount() == 2
    assert nav.popup.tree.indentation() == 0 and not nav.popup.tree.rootIsDecorated()
    assert all(nav.popup.tree.topLevelItem(i).childCount() == 0 for i in range(2))
    nav.popup.search.setText('offline')
    QTest.keyClick(nav.popup.search, Qt.Key_Return)
    assert window.activeWorkflowId == 'offline'


@pytest.mark.parametrize('width', [380, 560])
def testChineseSixStepHierarchyShowsBusinessNamesWithoutTechnicalPath(window, tmp_path, width):
    w = window
    for key, name in [('main', '工位2 | 运行入口'), ('station', '工位2 | 循环体'),
                      ('coordinates', '工位2·1匹 | 定位矫正'), ('holes', '工位2·1匹 | B路测量')]:
        w.renameWorkflow(key, name)
    w.workflowStore.addWorkflow('工位2 | 单帧计算与两拍分支', 'frame')
    w.workflowStore.addWorkflow('工位2 | 第一拍定位机型分支', 'first')
    node(w, 'main', 'dispatch-station')['displayName'] = '启动工位2'
    repeat = node(w, 'station', 'repeat-coordinates')
    repeat['displayName'] = '等待并处理一帧'
    repeat['loop']['bodyWorkflowId'] = 'frame'
    for source, nodeId, name, target in [('frame', 'first-shot', '[26] 第一拍', 'first'),
                                       ('first', 'branch-1hp', '1匹：机型2或3', 'coordinates')]:
        w.workflowStore.get(source).nodes.append(dict(nodeId=nodeId, kind='subflow',
                                                     displayName=name, targetWorkflowId=target))
    node(w, 'coordinates', 'measure')['displayName'] = 'B路识别测量'
    w._refreshWorkflowTabs()
    w.workflowNavigation.showReferences()
    popup = w.workflowNavigation.popup
    popup.resize(width, 500)
    settle()
    items = chainItems(popup)
    assert len(items) == 6
    visible = '\n'.join(item.text(0) for item in items)
    assert '当前工作流' in visible and 'B路识别测量' in visible and '[26] 第一拍' in visible
    assert all(technical not in visible for technical in ['dispatch-station', 'repeat-coordinates',
                                                         'first-shot', 'branch-1hp', 'Subflow', 'Dispatch'])
    assert popup.tree.horizontalScrollBar().maximum() == 0
    previous = None
    for item in items:
        rect = popup.tree.visualItemRect(item)
        if previous is not None:
            assert previous.bottom() < rect.top()
        previous = rect
    popup.tree.scrollToItem(items[-1])
    settle()
    assert popup.tree.viewport().rect().contains(popup.tree.visualItemRect(items[-1]))
    popup.tree.verticalScrollBar().setValue(0)
    settle()
    out = Path(os.environ.get('EMO_NAV_CHAIN_QA_DIR', str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    assert popup.grab().save(str(out / f'reference-hierarchy-{width}.png'))
