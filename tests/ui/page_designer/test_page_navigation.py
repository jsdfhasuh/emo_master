"""Every visible editor page must also be the property and drop target."""
import pytest
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QInputDialog

from test_workspace import designer  # noqa: F401
from test_editing import sendDrop
from test_pending_drops import setupDropPage
from test_pending_inputs import setupPage
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.core.presentation.models import Action


def navigate(editor, pageId, route):
    row = editor.store.snapshot().pageOrder.index(pageId)
    if route == 'list':
        editor.pageList.setCurrentRow(row)
    elif route == 'keyboard':
        editor.pageList.setFocus()
        key = Qt.Key_Down if row > editor.pageList.currentRow() else Qt.Key_Up
        QTest.keyClick(editor.pageList, key)
    else:
        rect = editor.pageList.visualItemRect(editor.pageList.item(row))
        QTest.mouseClick(editor.pageList.viewport(), Qt.LeftButton, pos=rect.center())
    QApplication.processEvents()


def assertCurrent(editor, pageId):
    assert editor.pageId == editor.renderer.currentPageId == pageId
    assert editor.pageList.currentItem().data(Qt.UserRole) == pageId
    assert editor.renderer.stack.currentWidget() is editor.renderer.pages[pageId]
    assert editor.tools._loadedPage == pageId
    assert all(button.isChecked() == (key == pageId)
               for key, button in editor.renderer.buttons.items())


@pytest.mark.parametrize('route', ['mouse', 'keyboard', 'list'])
def testNavigationTargetsVisiblePageForNativeAdd(designer, route):  # noqa: F811
    coordinator = designer.pageCoordinator
    coordinator.showPages()
    editor = coordinator.editor
    first = editor.store.createPage('A')
    second = editor.store.createPage('B')
    editor.refresh()
    coordinator.session.markSaved()
    history = len(coordinator.session._undo)

    navigate(editor, second, route)

    assertCurrent(editor, second)
    assert editor.tools.selected is None
    assert not coordinator.session.dirty
    assert len(coordinator.session._undo) == history
    assert sendDrop(editor.renderer.pages[second].widget(), {'kind': 'text'})
    assertCurrent(editor, second)
    assert not editor.store.snapshot().pages[first].components
    assert len(editor.store.snapshot().pages[second].components) == 1


def testTopNavigationTargetsVisibleComponentForNativeBinding(designer, monkeypatch):  # noqa: F811
    _, editor, original = setupDropPage(designer)
    first = editor.pageId
    second = editor.store.createPage('B')
    target = editor.tools.commands().add(second, 'indicator', 0, 0)
    editor.refresh()
    navigate(editor, second, 'keyboard')
    assertCurrent(editor, second)
    index = next(i for i, choice in enumerate(editor.tools.choices)
                 if choice.source.kind == 'workflow_output' and choice.source.port == 'decision')
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: ('bind', True))

    assert sendDrop(editor.renderer.widgets[second][target][1], {'choice': index})

    assertCurrent(editor, second)
    assert not _component(editor.store.snapshot(), first, original).bindings
    assert _component(editor.store.snapshot(), second, target).bindings


@pytest.mark.parametrize('route', ['mouse', 'keyboard', 'list'])
def testNavigationCommitsPendingInputToOriginalPage(designer, route):  # noqa: F811
    _, editor, original = setupPage(designer)
    first = editor.pageId
    second = editor.store.createPage('B')
    other = editor.tools.commands().add(second, 'text', 0, 0)
    editor.refresh()
    editor.tools.fields['title'].setText('仅属于 A 的未应用标题')
    editor.tools.fields['text'].setText('A 的内容')

    navigate(editor, second, route)

    assertCurrent(editor, second)
    assert editor.tools.selected is None
    assert _component(editor.store.snapshot(), first, original).props.title == '仅属于 A 的未应用标题'
    assert _component(editor.store.snapshot(), second, other).props.title == ''
    navigate(editor, first, route)
    assertCurrent(editor, first)
    assert editor.renderer.widgets[first][original][1].text() == 'A 的内容'
    editor.tools.select(original)
    assert editor.tools.fields['title'].text() == '仅属于 A 的未应用标题'


@pytest.mark.parametrize('route', ['mouse', 'keyboard', 'list'])
def testInvalidInputBlocksNavigationAndRetainsOriginalTarget(designer, route):  # noqa: F811
    coordinator, editor, original = setupPage(designer, 'indicator')
    first = editor.pageId
    second = editor.store.createPage('B')
    editor.refresh()
    editor.tools.fields['title'].setText('保留 A 的输入')
    editor.tools.addExtraRow(values=('boolean', 'invalid boolean', '保留映射', 'green'))
    before = coordinator.session.payload()
    history = len(coordinator.session._undo)

    navigate(editor, second, route)

    assertCurrent(editor, first)
    assert coordinator.session.payload() == before
    assert len(coordinator.session._undo) == history
    assert editor.tools.selected == original
    assert editor.tools.fields['title'].text() == '保留 A 的输入'
    assert editor.tools.extra.item(0, 1).text() == 'invalid boolean'
    assert 'true or false' in editor.message.text()
    editor.tools.extra.item(0, 1).setText('true')
    navigate(editor, second, route)
    assertCurrent(editor, second)
    assert _component(editor.store.snapshot(), first, original).props.indicatorStates['true'].text == '保留映射'
    assert not editor.store.snapshot().pages[second].components


@pytest.mark.parametrize('state', [None, 'OK'])
@pytest.mark.parametrize('route', ['top', 'action'])
def testPreviewNavigationDoesNotChangeEditingPage(designer, state, route):  # noqa: F811
    coordinator, editor, original = setupPage(designer)
    first = editor.pageId
    second = editor.store.createPage('B')
    editor.refresh()
    editor.tools.fields['text'].setText('预览前保存 A')
    preview = coordinator.preview.openObserver()
    preview.simulation.setCurrentIndex(preview.simulation.findData(state))
    if route == 'top':
        QTest.mouseClick(preview.buttons[second], Qt.LeftButton)
    else:
        preview.act(Action(type='navigate', pageId=second))
    assert preview.currentPageId == second
    assertCurrent(editor, first)
    assert editor.renderer.editing
    assert _component(editor.store.snapshot(), first, original).props.text == '预览前保存 A'
    navigate(editor, second, 'list')
    assert sendDrop(editor.renderer.pages[second].widget(), {'kind': 'text'})
    assert len(editor.store.snapshot().pages[first].components) == 1
    assert len(editor.store.snapshot().pages[second].components) == 1
    assert coordinator.preview.session is None and designer.currentJobId is None


@pytest.mark.parametrize('alreadyOpen', [False, True])
def testInvalidInputBlocksOpeningOrRefreshingPreview(designer, alreadyOpen):  # noqa: F811
    coordinator, editor, original = setupPage(designer, 'indicator')
    existing = coordinator.preview.openObserver() if alreadyOpen else None
    editor.tools.addExtraRow(values=('boolean', 'invalid boolean', '保留输入', 'green'))
    before = coordinator.session.payload()
    with pytest.raises(ValueError):
        coordinator.preview.openObserver()
    assert coordinator.preview.observer is existing
    assert editor.renderer.editing and editor.tools.selected == original
    assert editor.tools.extra.item(0, 1).text() == 'invalid boolean'
    assert coordinator.session.payload() == before
    editor.tools.extra.item(0, 1).setText('false')
    assert coordinator.preview.openObserver() is not None
    assert _component(editor.store.snapshot(), editor.pageId, original).props.indicatorStates['false'].text == '保留输入'


def testDetailPreviewPinsDisplayedResultAndDefersNewLayout(designer, qtApp):  # noqa: F811
    from examples.runtime_pages_p3 import LocalDemo
    from emo_master.clients.runtime.display_session import DisplaySession
    from emo_master.ui.presentation.hub import DisplayHub
    from tests.ui.presentation.test_real import until
    backend = LocalDemo(count=8)
    session = DisplaySession(backend.address, backend.jobId)
    editor = renderer = None
    try:
        coordinator = designer.pageCoordinator
        designer.workflowController.loadPayload(backend.project.model_dump())
        coordinator.session.acceptLoaded()
        coordinator.showPages()
        editor = coordinator.editor
        renderer = coordinator.preview.openObserver()
        hub = DisplayHub(session)
        renderer.hub = hub
        hub.attach(renderer)
        until(qtApp, lambda: bool(renderer.displayed))
        displayed = renderer.displayed['root']
        hub.timer.stop()
        until(qtApp, lambda: session.readSnapshot().scopes.get('root') and
              session.readSnapshot().scopes['root'].result.identity.resultOrdinal > displayed.result.identity.resultOrdinal)
        editor.tools.select('overview-count')
        editor.tools.fields['title'].setText('仅应用到总览页')
        editor.tools.fields['fontSize'].setValue(1)
        before = coordinator.session.payload()
        history = len(coordinator.session._undo)
        QTest.mouseClick(renderer.widgets['overview']['overview-nav'][1], Qt.LeftButton)
        assertCurrent(editor, 'overview')
        assert renderer.currentPageId == 'detail'
        ticket = renderer.frozen
        assert ticket is not None and hub.pins[renderer] == ticket
        until(qtApp, lambda: session.pins().read(ticket).state == 'PINNED')
        assert session.pins().read(ticket).scope.result == displayed.result
        assert renderer.displayed['root'].result == displayed.result
        assert coordinator.session.payload() == before and len(coordinator.session._undo) == history
        originalConfig = renderer.config
        editor.tools.fields['fontSize'].setValue(16)
        editor.tools.commitPending()
        editor.refresh()
        assert renderer.config == originalConfig and renderer.pendingPresentation == editor.store.snapshot()
        assert renderer.frozen == ticket
        coordinator.history()
        assert editor.store.snapshot() == originalConfig
        assert renderer.pendingPresentation is None and renderer.frozen == ticket
        coordinator.history(True)
        assert renderer.pendingPresentation == editor.store.snapshot()
        renderer.resumeLive()
        assert renderer.frozen is None and renderer.config == editor.store.snapshot()
        item = _component(editor.store.snapshot(), 'overview', 'overview-count')
        assert item.props.title == '仅应用到总览页' and item.props.fontSize == 16
        assert len(coordinator.session._undo) == history + 1 and len(backend.presentation.jobs) == 1
    finally:
        if renderer is not None:
            renderer.close()
        if editor is not None:
            editor.tools.fields['fontSize'].setValue(16)
        session.close()
        backend.close()
