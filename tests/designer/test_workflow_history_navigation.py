"""VS Code-style browsing is neither a Runtime call stack nor project undo."""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QEventLoop, QRectF, QSettings, Qt, QTimer
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QLineEdit

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.state.flow_graph_model import FlowGraphModel
from emo_master.apps.designer.ui.log_dialog import StructuredLogEntry
from tests.designer.test_workflow_entry_markers import readableNativeFont  # noqa: F401
from tests.runtime.test_workflow_debug_control import nestedProject
from tests.designer.navigation_fixture import saveNavigationProject


def settle():
    loop = QEventLoop()
    QTimer.singleShot(60, loop.quit)
    loop.exec_()


@pytest.fixture
def window(ownedDesignerWindow, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('navigation must never start a Runtime Job')
    w = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: [], startJob=forbidden,
        loadProject=lambda _: SimpleNamespace(ok=True, message='local stub')),
        settingsStore=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
    path = tmp_path / 'navigation.emoproj'
    payload = nestedProject()
    payload['workflowOrder'].append('other')
    payload['workflows']['other'] = dict(name='另一工位', inputs={}, outputs={}, nodes=[], edges=[])
    saveProject(path, payload)
    assert w.loadProjectDirectory(str(path), confirmed=True)
    w.show()
    settle()
    w.resize(1250, 820)
    settle()
    return w


def selected(w, node):
    w.flowScene.setNodeSelected(node)
    w.onNodeSelectionChanged()
    settle()


def view(w):
    center = w.flowView.mapToScene(w.flowView.viewport().rect().center())
    return w.flowView.getZoomFactor(), center.x(), center.y()


def testDefinitionThenTabBackForwardRestoresExactCallerAndViewWithoutMutatingProject(window):
    w = window
    nav = w.workflowNavigation
    session = w.pageCoordinator.session
    before = deepcopy(session.payload()), deepcopy(session._undo), deepcopy(session._redo)
    raw = Path(w.loadedProjectPath).read_bytes()
    jobs = {key: (id(run), run.jobId) for key, run in w.runtimeController.runs.items()}
    selected(w, 'second')
    w.flowView.setZoomFactor(.64)
    w.flowView.centerOn(*w.flowScene.getNodeCenter('second'))
    origin = view(w)
    w.designerActions.invoke('definition')
    assert w.activeWorkflowId == 'child'
    assert nav.history.entries[-2].nodeId == 'second'
    w.flowView.setZoomFactor(.83)
    childView = view(w)
    w.workflowTabs.selectWorkflow('other')
    assert [entry.workflowId for entry in nav.history.entries] == ['main', 'child', 'other']
    assert nav.move(-1) and w.activeWorkflowId == 'child'
    assert view(w)[0] == pytest.approx(childView[0])
    assert view(w) == pytest.approx(childView, abs=3)
    assert nav.move(-1) and w.activeWorkflowId == 'main'
    assert w.flowScene.getSelectedNodeId() == 'second'
    assert view(w)[0] == pytest.approx(origin[0])
    assert view(w) == pytest.approx(origin, abs=3)
    assert nav.move(1) and w.activeWorkflowId == 'child'
    w.pageCoordinator.sync()
    assert (session.payload(), session._undo, session._redo) == before
    assert not session.dirty and Path(w.loadedProjectPath).read_bytes() == raw
    assert {key: (id(run), run.jobId) for key, run in w.runtimeController.runs.items()} == jobs


def testSharedChildReturnsToActualCallNodeAndNewJumpDropsForward(window):
    w, nav = window, window.workflowNavigation
    for caller in ['first', 'second']:
        selected(w, caller)
        assert nav.openReference('child', 'main', caller)
        assert nav.move(-1)
        assert w.flowScene.getSelectedNodeId() == caller
    assert nav.canMove(1)
    w.activateWorkflow('other')
    assert not nav.canMove(1)


def testSameWorkflowNodeJumpsAreSingleStepsButSelectionPanZoomAreNot(window):
    w, nav = window, window.workflowNavigation
    selected(w, 'first')
    for zoom in [.3, .5, .8]:
        w.flowView.setZoomFactor(zoom)
        w.flowView.centerOn(100, 200)
    assert len(nav.history.entries) == 1
    w.navigateToNodeFromSidebar('second')
    assert len(nav.history.entries) == 2
    assert nav.move(-1) and w.flowScene.getSelectedNodeId() == 'first'
    assert nav.move(1) and w.flowScene.getSelectedNodeId() == 'second'


def testDeletedNodeFallsBackToWorkflowAndDeletedWorkflowIsSkipped(window):
    w, nav = window, window.workflowNavigation
    selected(w, 'first')
    w.activateWorkflow('other')
    w.activateWorkflow('child')
    w.deleteWorkflow('other')
    main = w.workflowStore.get('main')
    main.nodes = [node for node in main.nodes if node.get('nodeId') != 'first']
    assert nav.move(-1) and w.activeWorkflowId == 'main'
    assert w.flowScene.getSelectedNodeId() is None
    assert '原定位节点已删除' in w.statusBar().currentMessage()


def testFailedJumpDoesNotAlterHistoryAndReopenSameProjectClearsIt(window):
    w, nav = window, window.workflowNavigation
    w.activateWorkflow('child')
    assert nav.move(-1)
    before = list(nav.history.entries), nav.history.index
    assert not nav.navigate('missing')
    assert not nav.navigate('child', 'missing')
    assert (nav.history.entries, nav.history.index) == before and nav.canMove(1)
    oldSession = nav.session
    assert w.loadProjectDirectory(w.loadedProjectPath, confirmed=True)
    assert nav.session != oldSession
    assert len(nav.history.entries) == 1 and not nav.canMove(-1) and not nav.canMove(1)


def testReferenceResultsKeepSeparateNodesAndRejectStaleRows(window):
    w, nav = window, window.workflowNavigation
    w.activateWorkflow('child')
    nav.showReferences()
    settle()
    popup = nav.popup
    assert popup.isVisible() and popup.tree.topLevelItemCount() == 2
    popup.search.setText('second')
    QTest.keyClick(popup.search, Qt.Key_Return)
    assert w.activeWorkflowId == 'main' and w.flowScene.getSelectedNodeId() == 'second'
    assert nav.move(-1) and w.activeWorkflowId == 'child'
    nav.showReferences()
    popup.search.setText('first')
    # A retained row must not activate a removed/retargeted source.
    main = w.workflowStore.get('main')
    next(n for n in main.nodes if n.get('nodeId') == 'first')['targetWorkflowId'] = 'other'
    QTest.keyClick(popup.search, Qt.Key_Return)
    assert w.activeWorkflowId == 'child' and '调用来源已变化' in w.statusBar().currentMessage()
    nav.showReferences('other')
    popup.search.setText('no-such-reference')
    assert popup.empty.isVisible() and popup.tree.currentItem() is None
    QTest.keyClick(popup.search, Qt.Key_Return)
    assert popup.isVisible()
    QTest.keyClick(popup.search, Qt.Key_Escape)
    assert not popup.isVisible()


def testCanvasNavigationUsesAppliedTargetAndPreservesUnappliedEditorDraft(window):
    w, nav = window, window.workflowNavigation
    w.openNodeParamDialog('second')
    editor = w.nodeParamDialog
    # Retain an unapplied target change; jumping must not apply it.
    control = editor._schemaForm._controls['targetWorkflowId']
    control.setCurrentIndex(control.findData('other'))
    # Parameter selection is not navigation or Apply. The canvas still points
    # at the configured workflow until the user explicitly applies the draft.
    assert w.activeWorkflowId == 'main'
    w.flowScene.workflowOpenHandler('child', 'second')
    settle()
    assert w.activeWorkflowId == 'child'
    assert nav.move(-1) and w.activeWorkflowId == 'main'
    assert w.flowScene.getSelectedNodeId() == 'second'
    assert w.flowModel.nodes['second'].targetWorkflowId == 'child'
    assert editor.collectParams()['targetWorkflowId'] == 'other'
    w.openNodeParamDialog('second')
    assert w.nodeParamDialog is editor
    assert editor.collectParams()['targetWorkflowId'] == 'other'
    editor.forceClose()


def testDeferredCanvasJumpRecordsCallerAndRejectsOldProjectSession(window):
    w, nav = window, window.workflowNavigation
    w.flowScene.workflowOpenHandler('child', 'second')
    assert w.activeWorkflowId == 'main'
    settle()
    assert w.activeWorkflowId == 'child'
    assert nav.move(-1) and w.flowScene.getSelectedNodeId() == 'second'
    w.flowScene.workflowOpenHandler('child', 'first')
    # Simulate replacement before the queued graphics event is dispatched.
    w._projectInstanceToken = 'replacement-session'
    nav.reset()
    settle()
    assert w.activeWorkflowId == 'main' and len(nav.history.entries) == 1


@pytest.mark.parametrize('gesture', ['double_click', 'ctrl_click'])
def testActualCanvasReferenceClickAndBackButtonReturnWithoutMovingNode(window, gesture):
    from emo_master.apps.designer.ui.flow_scene import _WorkflowReferenceText
    w, nav = window, window.workflowNavigation
    # The minimal fixture deliberately has no layout; separate the call from
    # boundary nodes before testing a real mouse hit rather than an API call.
    w.flowScene._nodeItems['second'].setPos(900, 500)
    settle()
    positions = w.flowScene.getNodePositions()
    item = next(child for child in w.flowScene._nodeItems['second'].childItems()
                if isinstance(child, _WorkflowReferenceText))
    w.flowView.setZoomFactor(1)
    w.flowView.centerOn(item)
    point = w.flowView.mapFromScene(item.mapToScene(item.boundingRect().center()))
    assert w.flowView.itemAt(point) is item
    if gesture == 'ctrl_click':
        QTest.mouseClick(w.flowView.viewport(), Qt.LeftButton, Qt.ControlModifier, pos=point)
    else:
        QTest.mouseDClick(w.flowView.viewport(), Qt.LeftButton, pos=point)
        QTest.mouseRelease(w.flowView.viewport(), Qt.LeftButton, pos=point)
    settle()
    assert w.activeWorkflowId == 'child'
    assert w.workflowNavigationControls.back.isEnabled()
    QTest.mouseClick(w.workflowNavigationControls.back, Qt.LeftButton)
    settle()
    assert w.activeWorkflowId == 'main' and w.flowScene.getSelectedNodeId() == 'second'
    assert w.flowScene.getNodePositions() == positions
    assert len(nav.history.entries) == 2


@pytest.mark.parametrize('gesture', ['plain', 'shift', 'ctrl_shift', 'drag', 'node_body'])
def testCanvasReferenceShortcutDoesNotNavigateForOtherGestures(window, gesture):
    from PySide2.QtCore import QPoint, QPointF
    from emo_master.apps.designer.ui.flow_scene import _WorkflowReferenceText
    w = window
    node = w.flowScene._nodeItems['second']
    node.setPos(900, 500)
    settle()
    item = next(child for child in node.childItems() if isinstance(child, _WorkflowReferenceText))
    w.flowView.setZoomFactor(1)
    w.flowView.centerOn(item)
    point = w.flowView.mapFromScene(item.mapToScene(item.boundingRect().center()))
    assert w.flowView.itemAt(point) is item
    if gesture == 'drag':
        QTest.mousePress(w.flowView.viewport(), Qt.LeftButton, Qt.ControlModifier, pos=point)
        QTest.mouseRelease(w.flowView.viewport(), Qt.LeftButton, Qt.ControlModifier,
                           pos=point + QPoint(100, 0))
    elif gesture == 'node_body':
        point = w.flowView.mapFromScene(node.mapToScene(QPointF(10, 10)))
        QTest.mouseClick(w.flowView.viewport(), Qt.LeftButton, Qt.ControlModifier, pos=point)
        assert node.isSelected()
    else:
        modifiers = {'plain': Qt.NoModifier, 'shift': Qt.ShiftModifier,
                     'ctrl_shift': Qt.ControlModifier | Qt.ShiftModifier}
        QTest.mouseClick(w.flowView.viewport(), Qt.LeftButton, modifiers[gesture], pos=point)
    settle()
    assert w.activeWorkflowId == 'main'
    assert len(w.workflowNavigation.history.entries) == 1


def testLogAndRelationshipJumpsAreAtomicAndReturnToOriginalLocation(window):
    w, nav = window, window.workflowNavigation
    selected(w, 'second')
    entry = StructuredLogEntry(1234, 'ERROR', 'test', workflowId='child', nodeId='number',
        projectId=w._currentProjectId(), projectInstanceToken=w._runtimeLogProjectToken)
    assert '已定位' in w.navigateToRuntimeLogEntry(entry)
    assert len(nav.history.entries) == 2
    assert nav.history.current.key[1:] == ('child', 'number')
    assert nav.move(-1) and w.flowScene.getSelectedNodeId() == 'second'
    tree = w.workflowDependencyTree
    call = tree.topLevelItem(0).child(0)
    # The source node click is a single same-workflow location, not two steps.
    tree.itemClicked.emit(call, 0)
    assert w.flowScene.getSelectedNodeId() == 'first'
    assert len(nav.history.entries) == 2
    assert nav.move(-1) and w.flowScene.getSelectedNodeId() == 'second'


def testSubflowContextMenuUsesCanonicalDefinitionAction(window):
    w = window
    menu = w.nodeContextMenu.buildMenu('second')
    definition = next(action for action in menu.actions() if action.data() == 'definition')
    assert definition.isEnabled() and 'F12' in definition.text() and definition.shortcut().isEmpty()
    definition.trigger()
    assert w.activeWorkflowId == 'child'
    assert w.workflowNavigation.move(-1) and w.flowScene.getSelectedNodeId() == 'second'


def testMultipleLoopTargetsRequireAChoiceAndStaleDefinitionIsRejected(window):
    w, nav = window, window.workflowNavigation
    path = Path('examples/image_batch_while_portable/image-batch-while.emoproj')
    w.workflowController.loadPayload(json.loads(path.read_text(encoding='utf-8')), preserveEdges=True)
    w.activeWorkflowId = 'main'
    w._refreshWorkflowTabs()
    nav.reset()
    selected(w, 'while')
    nav.goToDefinition()
    settle()
    assert nav.popup.isVisible() and nav.popup.tree.topLevelItemCount() == 2
    nav.popup.search.setText('body')
    QTest.keyClick(nav.popup.search, Qt.Key_Return)
    assert w.activeWorkflowId == 'body'
    assert nav.move(-1) and w.flowScene.getSelectedNodeId() == 'while'
    nav.goToDefinition()
    nav.popup.search.setText('body')
    w.flowModel.nodes['while'].loop['bodyWorkflowId'] = 'condition'
    QTest.keyClick(nav.popup.search, Qt.Key_Return)
    assert w.activeWorkflowId == 'main' and '引用已变化' in w.statusBar().currentMessage()


def testKeyboardBackForwardDefinitionAndInputGuard(window, designerApplication):
    w, nav = window, window.workflowNavigation
    selected(w, 'first')
    w.activateWindow()
    w.flowView.setFocus()
    settle()
    QTest.keyClick(w.flowView, Qt.Key_F12)
    assert w.activeWorkflowId == 'child'
    QTest.keyClick(w.flowView, Qt.Key_Left, Qt.AltModifier)
    assert w.activeWorkflowId == 'main'
    QTest.keyClick(w.flowView, Qt.Key_Right, Qt.AltModifier)
    assert w.activeWorkflowId == 'child'
    field = QLineEdit(w)
    field.show()
    field.setFocus()
    settle()
    QTest.keyClick(field, Qt.Key_Left, Qt.AltModifier)
    assert w.activeWorkflowId == 'child'
    field.hide()
    w.flowView.setFocus()
    w.isJobRunning = True
    assert not w.designerActions.blockedReason('navigate_back')
    w.isJobRunning = False
    assert nav.canMove(-1)


def testNavigationActionsAreDisabledInPageDesignAndDoNotAffectRuntimeMarkers(window, monkeypatch):
    w, nav = window, window.workflowNavigation
    from tests.designer.test_workflow_entry_markers import event
    manager = w.runtimeController
    run = manager._create('child')
    run.state.updateJob('RUNNING')
    run.state.applyEvent(event('workflow.started', 'child', 'child-run', 1))
    manager._refresh(run)
    selected(w, 'second')
    nav.goToDefinition()
    w.workflowTabs.selectWorkflow('other')
    assert nav.move(-1) and nav.move(-1)
    assert w.workflowTabs._labels['child'].status == 'RUNNING'
    before = list(nav.history.entries), nav.history.index
    monkeypatch.setattr(w.designerActions, 'pageEditor', lambda: object())
    for key in ['navigate_back', 'navigate_forward', 'definition', 'references']:
        assert w.designerActions.blockedReason(key) == '仅流程设计可用'
    assert (nav.history.entries, nav.history.index) == before
    run.state.updateJob('IDLE')


def testPortableProjectNestedNavigationDoesNotChangeSavedDraft(window, tmp_path):
    w, nav = window, window.workflowNavigation
    path = saveNavigationProject(tmp_path)
    original = path.read_bytes()
    assert w.loadProjectDirectory(str(path), confirmed=True)
    before = deepcopy(w.pageCoordinator.session.payload())
    w.activateWorkflow('station1-first')
    callers = [(key, node.targetWorkflowId) for key, node in w.flowModel.nodes.items() if node.kind == 'subflow']
    assert callers
    caller, child = callers[0]
    selected(w, caller)
    nav.goToDefinition()
    assert w.activeWorkflowId == child
    assert nav.move(-1) and w.activeWorkflowId == 'station1-first'
    assert w.flowScene.getSelectedNodeId() == caller
    settle()
    evidence = Path('build/workflow_history_20261012') / ('scale' + os.environ.get('QT_SCALE_FACTOR', '1'))
    evidence.mkdir(parents=True, exist_ok=True)
    assert w.grab().save(str(evidence / 'migration-navigation.png'))
    assert nav.move(1) and w.activeWorkflowId == child
    w.pageCoordinator.sync()
    assert w.pageCoordinator.session.payload() == before
    assert not w.pageCoordinator.session.dirty and path.read_bytes() == original


def testControlNodeNullRoundtripDoesNotHideActualOperatorIdChanges():
    payload = nestedProject()
    payload['workflows']['main']['nodes'][1]['operatorId'] = None
    store = WorkflowStore()
    store.loadPayload(payload)
    model = FlowGraphModel()
    model.loadProjectGraph(store.graphFor('main'))
    assert model.nodes['first'].operatorId == ''
    store.captureGraph('main', model.toProjectGraph())
    assert next(n for n in store.get('main').nodes if n['nodeId'] == 'first')['operatorId'] is None
    model.loadProjectGraph(store.graphFor('child'))
    model.nodes['number'].operatorId = 'vision.value.text'
    store.captureGraph('child', model.toProjectGraph())
    assert next(n for n in store.get('child').nodes if n['nodeId'] == 'number')['operatorId'] == 'vision.value.text'


def testExplicitNodeJumpIsNotOverwrittenByDeferredFit(window):
    w = window
    w.navigateToWorkflow('child', 'number')
    assert w.flowView._pendingFit is None
    expected = w.flowScene.getNodeCenter('number')
    settle()
    center = view(w)[1:]
    assert center == pytest.approx(expected, abs=3)


def testShortWindowKeepsBackForwardWithoutStealingToolboxSpace(window):
    w = window
    w.activateWorkflow('child')
    w.resize(640, 319)
    settle()
    controls = w.workflowNavigationControls
    assert not controls.isVisible()
    assert controls.back.isVisible() and controls.forward.isVisible()
    assert controls.back.parentWidget() is w.workflowTabs
    QTest.mouseClick(controls.back, Qt.LeftButton)
    assert w.activeWorkflowId == 'main'
    w.resize(1200, 800)
    settle()
    assert controls.isVisible()


def testReturnRevealsWholeCallerWhenItsCenterWasVisibleButNodeWasClipped(window):
    w, nav = window, window.workflowNavigation
    selected(w, 'second')
    w.flowView.setZoomFactor(.7)
    x, y = w.flowScene.getNodeCenter('second')
    w.flowView.centerOn(x + w.flowView.viewport().width() / 1.4 - 4, y)
    bounds = QRectF(*w.flowScene.getNodeBounds('second'))
    visible = w.flowView.mapToScene(w.flowView.viewport().rect()).boundingRect()
    assert visible.contains(bounds.center()) and not visible.contains(bounds)
    nav.openReference('child', 'main', 'second')
    assert nav.move(-1)
    settle()
    assert w.flowView.getZoomFactor() == pytest.approx(.7)
    bounds = QRectF(*w.flowScene.getNodeBounds('second'))
    assert w.flowView.mapToScene(w.flowView.viewport().rect()).boundingRect().contains(bounds)


def testNavigationControlsAndReferencePopupRenderAtNarrowWidth(window):
    w, nav = window, window.workflowNavigation
    longName = '第一工位很长的坐标识别与矫正工作流 Long workflow name ' * 3
    w.renameWorkflow('main', longName)
    w.activateWorkflow('child')
    nav.showReferences()
    settle()
    popup = nav.popup
    popup.resize(380, 420)
    settle()
    item = popup.tree.topLevelItem(0)
    assert popup.tree.visualItemRect(item).height() > 40
    assert longName in item.text(0) and 'first' in item.text(0)
    evidence = Path('build/workflow_history_20261012') / ('scale' + os.environ.get('QT_SCALE_FACTOR', '1'))
    evidence.mkdir(parents=True, exist_ok=True)
    assert popup.grab().save(str(evidence / 'callers.png'))
    popup.hide()
    from emo_master.apps.designer.ui.workflow_navigation_controls import WorkflowNavigationControls
    controls = WorkflowNavigationControls()
    controls.bindActions(w.designerActions.actions)
    controls.location.setText('当前位置：' + longName)
    controls.resize(380, controls.sizeHint().height())
    controls.show()
    settle()
    assert controls.width() == 380
    assert controls.back.isVisible() and controls.forward.isVisible()
    assert not controls.back.geometry().intersects(controls.location.geometry())
    assert not controls.location.geometry().intersects(controls.references.geometry())
    # Status/tooltip changes must not revert fixed-width arrows to clipped text.
    w.designerActions.refresh()
    settle()
    for bar in (controls, w.workflowNavigationControls):
        assert not bar.back.icon().isNull() and not bar.forward.icon().isNull()
    assert controls.grab().save(str(evidence / 'navigation.png'))
    assert w.grab().save(str(evidence / 'designer.png'))
