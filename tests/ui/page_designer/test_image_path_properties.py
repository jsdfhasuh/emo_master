"""Image-path choices use normal page draft, save and undo controls."""
import json

import pytest

from test_workspace import designer  # noqa: F401
from test_pending_inputs import setupPage
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.core.presentation.models import ImagePathSource
from emo_master.core.presentation.validation import validateBindings


def choose(combo, value):
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def testFixedPathModeSwitchKeepsPendingTextAndSavesWithoutReading(designer, tmp_path):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'image')
    form = editor.tools.imageProperties
    path = str(tmp_path / '未读取的图片.png')
    before = coordinator.session.payload()
    choose(form.mode, 'path')
    form.path.setText(path)
    choose(form.mode, 'resource')
    choose(form.mode, 'workflow')
    choose(form.mode, 'path')
    assert form.path.text() == path
    assert coordinator.session.payload() == before
    assert editor.tools.binding.parentWidget().isHidden()
    assert editor.tools.fields['imageResourceId'].parentWidget().isHidden()
    editor.tools.commitPending()
    source = _component(editor.store.snapshot(), editor.pageId, key).props.imagePath
    assert source == ImagePathSource(kind='fixed', path=path)
    assert coordinator.session.undo()
    assert _component(editor.store.snapshot(), editor.pageId, key).props.imagePath is None
    assert coordinator.session.redo()
    assert designer.saveProjectToDirectory(str(tmp_path / 'saved'))
    payload = json.loads((tmp_path / 'saved/project.json').read_text(encoding='utf-8'))
    saved = payload['presentation']['pages'][editor.pageId]['components'][0]
    assert saved['props']['imagePath']['path'] == path
    assert saved['bindings'] == {} and 'imageResourceId' not in saved['props']
    assert not (tmp_path / '未读取的图片.png').exists()
    assert designer.loadProjectDirectory(str(tmp_path / 'saved'))
    assert _component(coordinator.session.presentation.snapshot(), editor.pageId, key).props.imagePath == source


def testSelectedRowAcceptsHiddenFieldAndRetainsMissingSource(designer, tmp_path, monkeypatch):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'image')
    table = editor.tools.commands().add(editor.pageId, 'sqlite_table', 1, 0)
    def configure(p):
        item = _component(p, editor.pageId, table)
        item.props.title = '记录表'
        item.props.sqlite.columns = ['时间', '判定']
    coordinator.session.editPresentation(configure)
    editor.refresh()
    editor.tools.select(key)
    from emo_master.core.presentation import sqlite_reader
    def forbidRead(*args):
        raise AssertionError('properties must not query a database')
    monkeypatch.setattr(sqlite_reader, 'inspectDatabase', forbidRead)
    form = editor.tools.imageProperties
    choose(form.mode, 'path')
    choose(form.kind, 'selected_row')
    choose(form.component, table)
    assert {form.field.itemText(i) for i in range(form.field.count())} == {'时间', '判定'}
    form.field.setEditText('result image path')
    form.baseDirectory.setText(str(tmp_path))
    editor.tools.commitPending()
    source = _component(editor.store.snapshot(), editor.pageId, key).props.imagePath
    assert source.componentId == table and source.field == 'result image path'
    editor.tools.commands().delete(editor.pageId, table)
    editor.refresh()
    editor.tools.select(key)
    assert form.component.currentData() == table and '来源缺失' in form.component.currentText()
    assert form.field.currentText() == 'result image path'
    assert any(i.code == 'image_path_source_missing' for i in validateBindings(coordinator.session.document(), {}))
    assert designer.saveProjectToDirectory(str(tmp_path / 'draft'))


def testUnfinishedSelectedRowKeepsPendingFormAndOriginalDraft(designer):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'image')
    form = editor.tools.imageProperties
    choose(form.mode, 'path')
    choose(form.kind, 'selected_row')
    before = coordinator.session.payload()
    with pytest.raises(ValueError, match='来源表格'):
        editor.tools.commitPending()
    assert coordinator.session.payload() == before
    assert form.kind.currentData() == 'selected_row'
    choose(form.kind, 'fixed')
    editor.tools.commitPending()
    assert _component(editor.store.snapshot(), editor.pageId, key).props.imagePath == ImagePathSource(kind='fixed')


def testApplyingWorkflowModeClearsOnlyLocalImageSource(designer):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'image')
    form = editor.tools.imageProperties
    choose(form.mode, 'path')
    form.path.setText('C:/data/picture.png')
    editor.tools.commitPending()
    editor.refresh()
    editor.tools.select(key)
    choose(form.mode, 'workflow')
    assert _component(editor.store.snapshot(), editor.pageId, key).props.imagePath is not None
    editor.tools.commitPending()
    item = _component(editor.store.snapshot(), editor.pageId, key)
    assert item.props.imagePath is None and item.props.imageResourceId is None and not item.bindings
    assert coordinator.session.undo()
    assert _component(editor.store.snapshot(), editor.pageId, key).props.imagePath.path == 'C:/data/picture.png'
