"""Native drop commands must respect the visible, unapplied property form."""
import json

import pytest
from PySide2.QtCore import QPointF, Qt
from PySide2.QtGui import QDragEnterEvent, QDropEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QInputDialog

from test_workspace import designer  # noqa: F401
from test_pending_inputs import setupPage
from emo_master.apps.designer.page_designer.tools import mime
from emo_master.apps.designer.state.presentation_store import _component


def setupDropPage(window):
    coordinator = window.pageCoordinator
    payload = coordinator.session.payload()
    workflow = payload['workflows'][payload['entryWorkflowId']]
    workflow['outputs'] = {'decision': 'boolean'}
    for node in workflow['nodes']:
        if node['kind'] == 'workflow_output':
            node['inputPorts'] = {'decision': 'boolean'}
    window.workflowController.loadPayload(payload)
    coordinator.session.acceptLoaded()
    coordinator, editor, key = setupPage(window, 'indicator')
    coordinator.sync()
    QApplication.processEvents()
    return coordinator, editor, key


def sendPendingDrop(editor, key, operation):
    action = Qt.MoveAction if operation == 'move' else Qt.CopyAction
    if operation == 'output':
        target = editor.renderer.widgets[editor.pageId][key][1]
        point = target.rect().center()
        index = next(i for i, choice in enumerate(editor.tools.choices)
                     if choice.source.kind == 'workflow_output' and choice.source.port == 'decision')
        payload = {'choice': index}
    else:
        if operation == 'move':
            selected = editor.renderer.widgets[editor.pageId][key][1]
            # A real move first presses the already selected canvas component.
            QTest.mousePress(selected, Qt.LeftButton)
            assert editor.tools.dragStart[0] == key
        target = editor.renderer.pages[editor.pageId].widget()
        target.layout().activate()
        point = target.layout().cellRect(1, 0).center()
        payload = {'move': key} if operation == 'move' else {'kind': 'text'}
    data = mime(payload)
    enter = QDragEnterEvent(point, action, data, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, enter)
    assert enter.isAccepted()
    event = QDropEvent(QPointF(point), action, data, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, event)
    if operation == 'move':
        QTest.mouseRelease(selected, Qt.LeftButton)
    QApplication.processEvents()
    return event


@pytest.mark.parametrize('operation', ['palette', 'output', 'move'])
def testNativeDropCommitsPendingPropertiesBeforeRefreshAndSave(designer, monkeypatch, tmp_path, operation):  # noqa: F811
    coordinator, editor, key = setupDropPage(designer)
    pageId = editor.pageId
    tools = editor.tools
    tools.fields['title'].setText('未按应用的标题')
    tools.fields['fontSize'].setValue(32)
    tools.addExtraRow(values=('boolean', 'true', '保留判定文字', 'green'))
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: ('bind', True))
    history = len(coordinator.session._undo)

    event = sendPendingDrop(editor, key, operation)

    assert event.isAccepted(), editor.message.text()
    assert event.dropAction() == (Qt.MoveAction if operation == 'move' else Qt.CopyAction)
    item = _component(editor.store.snapshot(), pageId, key)
    assert item.props.title == '未按应用的标题'
    assert item.props.fontSize == 32
    assert item.props.indicatorStates['true'].text == '保留判定文字'
    assert len(coordinator.session._undo) == history + 2  # one form edit, one drop
    if operation == 'palette':
        assert len(editor.store.snapshot().pages[pageId].components) == 2
        assert tools.selected != key
    elif operation == 'output':
        assert item.bindings
    else:
        assert item.layout.row > 0
    tools.select(key)
    assert tools.fields['title'].text() == item.props.title
    assert tools.fields['fontSize'].value() == item.props.fontSize
    assert tools.extra.item(0, 2).text() == '保留判定文字'
    savedPresentation = editor.store.snapshot()
    directory = tmp_path / 'saved'
    assert designer.saveProjectToDirectory(str(directory))
    saved = json.loads((directory / 'project.json').read_text(encoding='utf-8'))
    assert saved['presentation'] == savedPresentation.model_dump(mode='json')
    assert designer.loadProjectDirectory(str(directory))
    coordinator.showPages()
    assert coordinator.editor.store.snapshot() == savedPresentation
    assert not coordinator.session.dirty


@pytest.mark.parametrize('operation', ['palette', 'output', 'move'])
def testNativeDropRejectsInvalidPendingFormWithoutMutation(designer, monkeypatch, operation):  # noqa: F811
    coordinator, editor, key = setupDropPage(designer)
    tools = editor.tools
    tools.fields['title'].setText('保留未提交的标题')
    tools.fields['fontSize'].setValue(32)
    tools.addExtraRow(values=('boolean', 'invalid boolean', '保留输入', 'green'))
    dialogs = []
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: (dialogs.append('binding') or 'bind', True))
    before = coordinator.session.payload()
    history = len(coordinator.session._undo)

    event = sendPendingDrop(editor, key, operation)

    assert not event.isAccepted()
    assert coordinator.session.payload() == before
    assert len(coordinator.session._undo) == history
    assert tools.selected == key
    assert tools.fields['title'].text() == '保留未提交的标题'
    assert tools.fields['fontSize'].value() == 32
    assert tools.extra.item(0, 1).text() == 'invalid boolean'
    assert tools.extra.item(0, 2).text() == '保留输入'
    assert 'true or false' in editor.message.text()
    assert not tools.pendingRefresh
    assert not dialogs
    # Correcting the retained field permits the same native gesture.
    tools.extra.item(0, 1).setText('false')
    assert sendPendingDrop(editor, key, operation).isAccepted(), editor.message.text()
    assert _component(editor.store.snapshot(), editor.pageId, key).props.indicatorStates['false'].text == '保留输入'
