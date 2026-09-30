"""Typed property controls operate on the existing draft and shared renderer."""
from dataclasses import asdict
import json

import pytest

from test_workspace import designer  # noqa: F401
from test_pending_inputs import setupPage
from test_editing import blank, metadata
from test_preview import waitFor
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.core.presentation.models import ResultScope


def testTypedIndicatorPlainTextFalseAndAppearanceSave(designer, tmp_path):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'indicator')
    tools = editor.tools
    tools.addExtraRow(values=('string', 'OK', '合格', 'green'))
    tools.addExtraRow(values=('boolean', False, '不合格', 'red'))
    tools.fields['fontSize'].setValue(32)
    tools.appearance['fontFamily'].setCurrentIndex(tools.appearance['fontFamily'].findData('monospace'))
    tools.appearance['cardStyle'].setCurrentIndex(tools.appearance['cardStyle'].findData('soft'))
    assert designer.saveProjectToDirectory(str(tmp_path / 'typed'))
    saved = json.loads((tmp_path / 'typed' / 'project.json').read_text(encoding='utf-8'))
    props = saved['presentation']['pages'][editor.pageId]['components'][0]['props']
    assert set(props['indicatorStates']) == {'"OK"', 'false'}
    assert props['fontSize'] == 32 and props['fontFamily'] == 'monospace' and props['cardStyle'] == 'soft'
    editor.refresh()
    tools.select(key)
    assert tools.extra.item(0, 1).text() == 'OK'
    assert tools.extra.item(1, 1).text() == 'false'
    assert not coordinator.session.dirty


def testDuplicateTypedValuesPreservePendingFormAndDraft(designer):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'indicator')
    for title in ('first', 'second'):
        editor.tools.addExtraRow(values=('string', 'OK', title, 'green'))
    before = coordinator.session.payload()
    with pytest.raises(ValueError, match='duplicate'):
        editor.tools.commitPending()
    assert coordinator.session.payload() == before
    assert editor.tools.extra.rowCount() == 2
    editor.tools.extra.setRowCount(0)


def testKnownCollectionFieldsAreSelectedWithoutJsonPaths(designer, tmp_path):  # noqa: F811
    coordinator = designer.pageCoordinator
    designer.workflowController.loadPayload(blank(tmp_path).model_dump())
    coordinator.session.acceptLoaded()
    waitFor(lambda: designer.operatorCatalogController.state == 'ready')
    designer.operatorCatalog = [asdict(m) for m in metadata().values()]
    coordinator.showPages()
    editor = coordinator.editor
    editor.pageId = editor.store.createPage('集合')
    key = editor.tools.commands().add(editor.pageId, 'table', 0, 0)
    editor.refresh()
    index = next(i for i, choice in enumerate(editor.tools.choices)
                 if choice.source.nodeId == 'blob' and choice.source.port == 'blobs' and not choice.source.fieldPath)
    editor.tools.bindChoice(index, key)
    editor.refresh()
    editor.tools.select(key)
    editor.tools.addExtraRow()
    picker = editor.tools.extra.cellWidget(0, 1)
    assert {picker.itemData(i) for i in range(picker.count())} == {('area',), ('centroid', 'x'), ('centroid', 'y')}
    picker.setCurrentIndex(picker.findText('centroid.x'))
    editor.tools.commitPending()
    assert _component(editor.store.snapshot(), editor.pageId, key).props.columns[0].fieldPath == ['centroid', 'x']


def testAllReadOnlyActionsEditedWithExplicitScope(designer):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'navigation_button')
    first = editor.pageId
    second = editor.store.createPage('详情')
    workflow = coordinator.session.document().entryWorkflowId
    def configure(p):
        p.resultScopes['shown'] = ResultScope(entryWorkflowId=workflow, scopeWorkflowId=workflow)
        p.pages[first].resultScopeIds = ['shown']
        p.pages[second].resultScopeIds = ['shown']
    coordinator.session.editPresentation(configure)
    editor.refresh()
    editor.tools.select(key)
    tools = editor.tools
    for mode in ('navigate', 'detail', 'freeze', 'resume_live', 'none'):
        tools.actionMode.setCurrentIndex(tools.actionMode.findData(mode))
        tools.destination.setCurrentIndex(tools.destination.findData(second))
        tools.actionScope.setCurrentIndex(tools.actionScope.findData('shown'))
        tools.commitPending()
        action = _component(editor.store.snapshot(), first, key).actions.get('clicked')
        if mode == 'none':
            assert action is None
        elif mode == 'detail':
            assert action.type == 'navigate' and action.context == 'displayed_result' and action.resultScopeId == 'shown'
        else:
            assert action.type == mode
    assert designer.currentJobId is None


def testOfflineStatesDoNotTouchRuntimeOrDirtyDraft(designer):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'indicator')
    editor.tools.addExtraRow(values=('boolean', True, '模拟合格', 'green'))
    editor.tools.addExtraRow(values=('boolean', False, '模拟不合格', 'red'))
    editor.tools.commitPending()
    editor.refresh()
    coordinator.session.markSaved()
    history = len(coordinator.session._undo)
    editor.tools.preview.setChecked(True)
    for state, text in [('OK', '模拟合格'), ('NG', '模拟不合格'), ('WAITING', '等待触发'), ('ERROR', 'NODE_FAILED')]:
        editor.tools.simulation.setCurrentIndex(editor.tools.simulation.findData(state))
        assert text in editor.renderer.widgets[editor.pageId][key][1].text()
        assert '离线模拟' in editor.renderer.banner.text()
        assert not editor.renderer.displayed
    editor.tools.preview.setChecked(False)
    assert 'NODE_FAILED' not in editor.renderer.widgets[editor.pageId][key][1].text()
    assert not coordinator.session.dirty and len(coordinator.session._undo) == history
    assert coordinator.preview.session is None and coordinator.preview.backend is None
    assert designer.currentJobId is None
