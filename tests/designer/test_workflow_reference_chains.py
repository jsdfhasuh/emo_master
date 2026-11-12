"""Complete caller paths from the live draft, without running any workflows."""
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QSettings, Qt
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
    assert payload['schemaVersion'] == '2.9' and payload['presentation']['schemaVersion'] == '1.2'
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


def testCompleteChainIncludesRootEveryCallAndTargetAndNavigatesBack(window):
    nav = window.workflowNavigation
    assert pathIds(nav) == [(['dispatch-station', 'repeat-coordinates', 'measure'], False)]
    nav.showReferences()
    settle()
    popup = nav.popup
    assert popup.tree.topLevelItemCount() == 1
    item = popup.tree.topLevelItem(0)
    assert item.text(0).split('\n') == [
        'Production entry / Start station \u2192 Station 2 | Continuous operation / Inspect screws '
        '\u2192 Station 2 | Screw coordinates / Measure hole \u2192 Station 2 | Hole measurement',
        'main / dispatch-station \u00b7 Dispatch \u2192 station / repeat-coordinates \u00b7 Repeat \u00b7 Body '
        '\u2192 coordinates / measure \u00b7 Subflow \u2192 holes',
    ]
    assert item.toolTip(0) == item.text(0)
    assert '1 \u6761\u94fe\u8def' in popup.title.text()
    for query in ['Production entry', 'dispatch-station', 'Inspect screws', 'repeat-coordinates',
                  'Screw coordinates', 'measure', 'holes']:
        popup.search.setText(query)
        assert not item.isHidden() and popup.tree.currentItem() is item
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
    assert window.activeWorkflowId == 'coordinates' and window.flowScene.getSelectedNodeId() == 'measure'


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
    item = popup.tree.topLevelItem(0)
    rect = popup.tree.visualItemRect(item)
    assert rect.height() > 70
    assert popup.tree.viewport().rect().contains(rect)
    assert popup.tree.horizontalScrollBar().maximum() == 0
    assert popup.tree.textElideMode() == Qt.ElideNone
    out = Path(os.environ.get('EMO_NAV_CHAIN_QA_DIR', str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    assert popup.grab().save(str(out / f'call-chain-{width}.png'))


def testOversizedChainCanScrollToItsFinalWorkflow(window):
    for key in ['main', 'station', 'coordinates']:
        window.renameWorkflow(key, window.workflowStore.get(key).name + ' long workflow name' * 15)
    window.workflowNavigation.showReferences()
    popup = window.workflowNavigation.popup
    popup.resize(380, 280)
    settle()
    item = popup.tree.topLevelItem(0)
    viewport = popup.tree.viewport().rect()
    assert popup.tree.visualItemRect(item).height() > viewport.height()
    scrollbar = popup.tree.verticalScrollBar()
    assert scrollbar.maximum() > 0
    scrollbar.setValue(scrollbar.maximum())
    settle()
    assert popup.tree.visualItemRect(item).bottom() <= viewport.bottom()
