"""dot's navigation, atomic editing, new-project and filename regressions."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import emo_master  # noqa: F401 - application bootstrap precedes Qt native imports
from PySide2.QtWidgets import QFileDialog, QMessageBox

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.main_window import MainWindow
from tests.runtime.test_workflow_debug_control import nestedProject
from tests.designer.test_variable_node_migration import _payload
from emo_master.plugins.builtins.variable_read.operator import ReadVariableOperator
from emo_master.plugins.builtins.variable_write.operator import WriteVariableOperator


def window(settings=None):
    return MainWindow(SimpleNamespace(listOperators=lambda: [],
        loadProject=lambda _: SimpleNamespace(ok=True, message='ok')), settingsStore=settings)


@pytest.mark.parametrize('operator', [ReadVariableOperator, WriteVariableOperator])
def testCancelExplicitMigrationChangesNothing(designerApplication, monkeypatch, tmp_path, operator):
    w = window()
    path = tmp_path / 'old.emoproj'
    assert w.saveProjectToDirectory(str(path))
    session = w.pageCoordinator.session
    before = session.payload(), deepcopy(session._undo), deepcopy(session._redo), w.flowScene.getNodePositions()
    raw = path.read_bytes()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.No)
    w.addNodeFromOperatorPayload(_payload(operator))
    assert (session.payload(), session._undo, session._redo, w.flowScene.getNodePositions()) == before
    assert not session.dirty and path.read_bytes() == raw


def testExceptionAfterModelMutationRestoresCanvasHistoryAndDirty(designerApplication, monkeypatch):
    w = window()
    session = w.pageCoordinator.session
    before = session.payload(), deepcopy(session._undo), deepcopy(session._redo), w.flowScene.getNodePositions()
    add = w.flowScene.addFlowNode
    def reject(model):
        if model.operatorId == 'test.broken':
            raise ValueError('injected render failure')
        return add(model)
    monkeypatch.setattr(w.flowScene, 'addFlowNode', reject)
    with pytest.raises(ValueError, match='render failure'):
        w.addNodeFromOperatorPayload(dict(operatorId='test.broken', inputPorts={}, outputPorts={}))
    assert (session.payload(), session._undo, session._redo, w.flowScene.getNodePositions()) == before
    assert not session.dirty
    assert set(w.flowModel.nodes) == set(w.flowScene._nodeItems)


def testFirstNavigationPreservesUntouchedPayloadAndRealMoveIsUndoable(designerApplication, tmp_path):
    payload = nestedProject()
    # Entry/boundary order deliberately differs from the canvas selection order.
    path = tmp_path / 'original.emoproj'
    saveProject(path, payload)
    w = window()
    assert w.loadProjectDirectory(str(path))
    session = w.pageCoordinator.session
    before, raw = session.payload(), path.read_bytes()
    for target in ['child', 'main', 'child', 'main']:
        w.activateWorkflow(target)
        w.pageCoordinator.sync()
        assert session.payload() == before and not session.dirty
    assert path.read_bytes() == raw and not session._undo
    item = w.flowScene._nodeItems['first']
    item.setPos(item.pos().x() + 120, item.pos().y() + 80)
    w.pageCoordinator.sync()
    assert session.dirty and len(session._undo) == 1
    w.pageCoordinator.history()
    assert session.payload() == before and not session.dirty


def testViewPreferencesAreLocalAndRestoreBelowOldZoomFloor(designerApplication, tmp_path):
    data = {}
    settings = SimpleNamespace(value=lambda key, default=None: data.get(key, default),
        setValue=lambda key, value: data.__setitem__(key, value))
    path = tmp_path / 'view.emoproj'
    saveProject(path, nestedProject())
    w = window(settings)
    w.show()
    assert w.loadProjectDirectory(str(path))
    w.flowView.setZoomFactor(.24)
    w._saveWorkflowViewState()
    before = w.pageCoordinator.session.payload()
    reopened = window(settings)
    reopened.show()
    assert reopened.loadProjectDirectory(str(path))
    assert reopened.flowView.getZoomFactor() == pytest.approx(.24)
    assert not reopened.pageCoordinator.session.dirty
    assert w.pageCoordinator.session.payload() == before
    assert data and all(key.startswith('ui/') for key in data)


def testWorkflowViewIsCapturedBeforeSwitchChangesSceneBounds(designerApplication, tmp_path):
    w = window()
    path = tmp_path / 'views.emoproj'
    saveProject(path, nestedProject())
    w.resize(1100, 760)
    w.show()
    assert w.loadProjectDirectory(str(path))
    w.flowView.setZoomFactor(.6)
    center = w.flowView.mapToScene(w.flowView.viewport().rect().center())
    w.activateWorkflow('child')
    w.flowView.setZoomFactor(.9)
    w.activateWorkflow('main')
    restored = w.flowView.mapToScene(w.flowView.viewport().rect().center())
    assert w.flowView.getZoomFactor() == pytest.approx(.6)
    assert abs(center.x() - restored.x()) < 4 and abs(center.y() - restored.y()) < 4
    assert not w.pageCoordinator.session.dirty


def testNativeSaveWarningCanBeAcknowledgedWithoutLosingPendingInput(designerApplication, tmp_path, record_testsuite_property):
    import sys
    if sys.platform == 'win32' and designerApplication.platformName() == 'offscreen':
        pytest.skip('Windows offscreen QPA cannot open QMessageBox; tested separately with native windows QPA')
    from PySide2.QtCore import QTimer, Qt
    from PySide2.QtGui import QFontInfo
    from PySide2.QtTest import QTest
    record_testsuite_property('qpa', designerApplication.platformName())
    record_testsuite_property('devicePixelRatio', designerApplication.primaryScreen().devicePixelRatio())
    record_testsuite_property('font', QFontInfo(designerApplication.font()).family())
    w = window()
    w.pageCoordinator.session.enablePresentation()
    w.pageCoordinator.showPages()
    editor = w.pageCoordinator.editor
    editor.pageId = editor.store.createPage('warning')
    key = editor.tools.commands().add(editor.pageId, 'text', 0, 0)
    editor.refresh()
    editor.tools.select(key)
    editor.tools.fields['fontSize'].setValue(1)
    observed = []
    def acknowledge():
        modal = designerApplication.activeModalWidget()
        observed.append((modal.windowTitle(), modal.text()))
        QTest.mouseClick(modal.button(QMessageBox.Ok), Qt.LeftButton)
    QTimer.singleShot(0, acknowledge)
    assert not w.saveProjectToDirectory(str(tmp_path / 'invalid.emoproj'))
    assert observed and observed[0][0] == '项目保存失败'
    assert editor.tools.fields['fontSize'].value() == 1
    editor.tools.fields['fontSize'].setValue(16)


@pytest.mark.parametrize('answer', [QMessageBox.Save, QMessageBox.Discard, QMessageBox.Cancel])
def testNewProjectResolvesUnsavedDraftExactlyOnce(designerApplication, monkeypatch, tmp_path, answer):
    w = window()
    old = tmp_path / 'old.emoproj'
    new = tmp_path / 'new.emoproj'
    assert w.saveProjectToDirectory(str(old))
    w.createWorkflow('unsaved')
    oldId, before = w.workflowStore.project['projectId'], w.pageCoordinator.session.payload()
    prompts = []
    def question(_parent, title, *args, **kwargs):
        prompts.append(title)
        assert title == '项目有未保存修改'
        return answer
    monkeypatch.setattr(QMessageBox, 'question', question)
    monkeypatch.setattr(w, '_chooseProjectFile', lambda _: str(new))
    w.newProjectAction()
    assert prompts == ['项目有未保存修改']
    if answer == QMessageBox.Cancel:
        assert w.pageCoordinator.session.payload() == before and not new.exists()
    else:
        assert new.is_file() and w.projectController.currentProjectFile == new
        assert w.workflowStore.project['projectId'] != oldId
        assert list(w.workflowStore.workflows) == ['main']
        assert not w.pageCoordinator.session.dirty
        import json
        previous = json.loads(old.read_bytes())
        assert len(previous['workflows']) == (2 if answer == QMessageBox.Save else 1)


@pytest.mark.parametrize('blocked', ['job', 'debug'])
def testNewDoesNotStopActiveWork(designerApplication, monkeypatch, blocked):
    w = window()
    if blocked == 'job':
        monkeypatch.setattr(w, 'isJobRunning', True)
    else:
        monkeypatch.setattr(w, 'hasActiveDebugSession', lambda: True)
    messages = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *a: messages.append(a))
    monkeypatch.setattr(w, '_chooseProjectFile', lambda _: pytest.fail('must not choose file'))
    w.newProjectAction()
    assert messages


@pytest.mark.parametrize('filename,expected', [('name', 'name.emoproj'), ('name.emoproj', 'name.emoproj'),
    ('name.EMOPROJ', 'name.EMOPROJ')])
def testProjectSuffixBoundary(designerApplication, monkeypatch, tmp_path, filename, expected):
    w = window()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(tmp_path / filename), ''))
    assert Path(w._chooseProjectFile('save')).name == expected


@pytest.mark.parametrize('answer,expected', [(QMessageBox.Yes, 'name.emoproj'),
    (QMessageBox.No, 'name.emoproj.emoproj'), (QMessageBox.Cancel, '')])
def testDuplicateSuffixRequiresExplicitChoice(designerApplication, monkeypatch, tmp_path, answer, expected):
    w = window()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(tmp_path / 'name.emoproj.emoproj'), ''))
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: answer)
    selected = w._chooseProjectFile('save')
    assert (Path(selected).name if selected else '') == expected


def testRecoveryExportsInvalidMemoryBeforeRestoringValidCheckpoint(designerApplication, monkeypatch, tmp_path):
    import json
    from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel
    w = window()
    original = tmp_path / 'original.emoproj'
    recovery = tmp_path / 'recovery.json'
    assert w.saveProjectToDirectory(str(original))
    raw = original.read_bytes()
    before = w.pageCoordinator.session.payload()
    node = w.flowModel.addNode('vision.state.variable_read', 'Broken legacy draft', {}, {'value': 'object'})
    w.flowScene.addFlowNode(FlowNodeViewModel(node, 'Broken legacy draft', 120, 90, {}, {'value': 'object'}))
    with pytest.raises(ValueError):
        w.pageCoordinator.sync()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(recovery), ''))
    assert w.pageCoordinator.offerDraftRecovery(ValueError('explicit project 2.4 migration'))
    captured = json.loads(recovery.read_bytes())
    assert any(item['nodeId'] == node for item in captured['workflows']['main']['nodes'])
    assert w.pageCoordinator.session.payload() == before
    assert node not in w.flowModel.nodes and original.read_bytes() == raw
    assert not w.pageCoordinator.session.dirty


def testFailedWorkflowCreationRestoresOriginalActiveTab(designerApplication, monkeypatch):
    w = window()
    child = w.createWorkflow('child')
    before = w.pageCoordinator.session.payload()
    refresh = w._refreshWorkflowTabs
    def failNew():
        if w.activeWorkflowId != child:
            raise ValueError('injected tab failure')
        refresh()
    monkeypatch.setattr(w, '_refreshWorkflowTabs', failNew)
    with pytest.raises(ValueError, match='tab failure'):
        w.createWorkflow('failed-new')
    assert w.activeWorkflowId == child and w.workflowStore.activeWorkflowId == child
    assert w.pageCoordinator.session.payload() == before
