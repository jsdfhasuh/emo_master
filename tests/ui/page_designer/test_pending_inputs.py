"""R3 normal editing: uncommitted form inputs must survive save/navigation."""
import json

import pytest

from test_workspace import designer  # noqa: F401
from emo_master.apps.designer.state.presentation_store import _component


def setupPage(designer, kind='text'):  # noqa: F811
    coordinator = designer.pageCoordinator
    coordinator.showPages()
    editor = coordinator.editor
    editor.pageId = editor.store.createPage('运行页')
    key = editor.tools.commands().add(editor.pageId, kind, 0, 0)
    editor.refresh()
    editor.tools.select(key)
    return coordinator, editor, key


def testSaveCommitsVisibleUnappliedProperties(designer, tmp_path):  # noqa: F811
    coordinator, editor, key = setupPage(designer)
    editor.tools.fields['title'].setText('未按应用的标题')
    editor.tools.fields['text'].setText('零件结果')
    assert designer.saveProjectToDirectory(str(tmp_path / 'saved'))
    payload = json.loads((tmp_path / 'saved' / 'project.json').read_text())
    saved = payload['presentation']['pages'][editor.pageId]['components'][0]
    assert saved['props']['title'] == '未按应用的标题'
    assert saved['props']['text'] == '零件结果'
    assert not coordinator.session.dirty
    assert _component(editor.store.snapshot(), editor.pageId, key).props.title == saved['props']['title']


def testInvalidPendingInputBlocksSaveWithoutDiscardingText(designer, tmp_path, monkeypatch):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'indicator')
    editor.tools.addExtraRow(values=('boolean', 'invalid boolean', 'OK', 'green'))
    before = coordinator.session.payload()
    from PySide2.QtWidgets import QMessageBox
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args))
    assert not designer.saveProjectToDirectory(str(tmp_path / 'invalid'))
    assert warnings and warnings[-1][1] == '项目保存失败'
    assert coordinator.session.payload() == before
    assert editor.tools.extra.item(0, 1).text() == 'invalid boolean'
    assert not (tmp_path / 'invalid' / 'project.json').exists()
    # Correcting the visible field makes the same save succeed.
    editor.tools.extra.item(0, 1).setText('true')
    assert designer.saveProjectToDirectory(str(tmp_path / 'fixed'))
    assert 'true' in _component(editor.store.snapshot(), editor.pageId, key).props.indicatorStates


def testSelectingAnotherComponentCommitsPendingInput(designer):  # noqa: F811
    coordinator, editor, first = setupPage(designer)
    second = editor.tools.commands().add(editor.pageId, 'text', 1, 0)
    editor.tools.fields['title'].setText('保留前一个控件')
    editor.tools.select(second)
    assert _component(editor.store.snapshot(), editor.pageId, first).props.title == '保留前一个控件'
    assert editor.tools.selected == second
    coordinator.session.undo()
    assert _component(editor.store.snapshot(), editor.pageId, first).props.title == ''


def testInvalidInputBlocksComponentSelection(designer):  # noqa: F811
    _, editor, first = setupPage(designer, 'indicator')
    second = editor.tools.commands().add(editor.pageId, 'text', 1, 0)
    editor.tools.addExtraRow(values=('boolean', 'not boolean', 'OK', 'green'))
    with pytest.raises(ValueError):
        editor.tools.select(second)
    assert editor.tools.selected == first
    editor.tools.extra.setRowCount(0)  # let test cleanup close normally
