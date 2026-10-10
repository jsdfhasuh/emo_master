from PySide2.QtCore import Qt
from PySide2.QtWidgets import QApplication

from test_workspace import designer  # noqa: F401
from test_pending_inputs import setupPage


def testNativePaletteSplitterAndSearch(designer):  # noqa: F811
    c = designer.pageCoordinator
    c.showPages()
    e = c.editor
    designer.resize(1280, 720)
    QApplication.processEvents()
    assert e.splitter.count() == 3
    assert e.tools.palette.count() == 8
    assert e.libraryTabs.count() == 2
    history = len(c.session._undo)
    e.tools.search.setText('图像')
    assert [e.tools.palette.item(i).data(Qt.UserRole) for i in range(8)
            if not e.tools.palette.item(i).isHidden()] == ['image']
    e.tools.search.clear()
    if e.splitter.sizes()[0] == 0:
        e.togglePanel(0)
    e.togglePanel(0)
    assert e.splitter.sizes()[0] == 0
    e.togglePanel(0)
    assert e.splitter.sizes()[0] > 0
    assert len(c.session._undo) == history
    assert e.propertyScroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    assert not e.tools.palette.grab().isNull()


def testSamplesAreTransientAndRealStatesNeverFallback(designer, tmp_path):  # noqa: F811
    import json
    from emo_master.clients.runtime.view_state import SessionView
    c, e, image = setupPage(designer, 'image')
    number = e.tools.commands().add(e.pageId, 'number', 1, 0)
    table = e.tools.commands().add(e.pageId, 'table', 2, 0)
    e.refresh()
    before, history = c.session.payload(), len(c.session._undo)
    r = e.renderer
    assert r.designExamples
    assert not r.widgets[e.pageId][image][1].image.isNull()
    assert r.widgets[e.pageId][number][1].text() == '128.50'
    assert r.widgets[e.pageId][table][1].model.rowCount() == 3
    assert not r.displayed and r.lastView is None
    assert r.editorResourceUsage()['design_image_bytes'] == 320 * 180 * 4
    assert c.session.payload() == before and len(c.session._undo) == history
    assert designer.saveProjectToDirectory(str(tmp_path / 'saved'))
    saved = json.loads((tmp_path / 'saved/project.json').read_text(encoding='utf-8'))
    assert saved['presentation'] == before['presentation']
    for status in ('CONNECTED', 'DISCONNECTED', 'ERROR'):
        r.submit(SessionView(1, 1, 'runtime', 'job', status, '', {}, {}, {}))
        assert not r.designExamples
        assert r.widgets[e.pageId][image][1].image.isNull()
        assert r.widgets[e.pageId][table][1].model.columns == []
    e.refresh()
    assert not r.designExamples
    assert c.preview.backend is None and c.preview.session is None
    assert designer.currentJobId is None


def testTypePropertiesPreserveHiddenValuesAndSharedSample(designer):  # noqa: F811
    from emo_master.apps.designer.state.presentation_store import _component
    c, e, key = setupPage(designer, 'number')
    def configure(p):
        _component(p, e.pageId, key).props.text = 'keep hidden text'
    c.session.editPresentation(configure)
    e.refresh()
    e.tools.select(key)
    e.tools.fields['text'].setText('hidden stale field')
    e.tools.fields['unit'].setText(' 件')
    e.tools.fields['decimals'].setValue(2)
    e.tools.apply()
    assert _component(e.store.snapshot(), e.pageId, key).props.text == 'keep hidden text'
    e.refresh()
    assert e.renderer.widgets[e.pageId][key][1].text() == '128.50 件'
    visible = {name for row, _widget, name, _group in e.tools.propertyGroups.rows if not row.isHidden()}
    assert 'unit' in visible and 'pageSize' not in visible and 'text' not in visible
    image1 = e.tools.commands().add(e.pageId, 'image', 1, 0)
    image2 = e.tools.commands().add(e.pageId, 'image', 2, 0)
    e.refresh()
    widgets = e.renderer.widgets[e.pageId]
    assert widgets[image1][1].image.cacheKey() == widgets[image2][1].image.cacheKey()
    e.renderer.setDesignExamples(False)
    assert e.renderer.editorResourceUsage()['design_image_bytes'] == 0


def testPagePropertiesAndLongNamesRemainInSidePanel(designer):  # noqa: F811
    import pytest
    c, e, key = setupPage(designer)
    e.tools.select(None)
    e.tools.pageName.setText('页面属性修改')
    e.tools.fields['columns'].setValue(2)
    history = len(c.session._undo)
    e.tools.commitPending()
    assert len(c.session._undo) == history + 1
    assert e.store.snapshot().pages[e.pageId].name == '页面属性修改'
    e.tools.select(key)
    e.tools.fields['title'].setText('很长的标题ABC' * 80)
    e.tools.commitPending()
    designer.resize(1280, 720)
    e.refresh()
    QApplication.processEvents()
    assert e.width() <= designer.width()
    for field in e.tools.fields.values():
        if field.isVisibleTo(e.propertyScroll.widget()):
            assert field.width() <= e.propertyScroll.viewport().width()
    assert e.propertyScroll.horizontalScrollBar().maximum() == 0
    e.tools.select(None)
    before = c.session.payload()
    e.tools.pageName.clear()
    with pytest.raises(ValueError):
        e.tools.commitPending()
    assert c.session.payload() == before
    assert QApplication.focusWidget() is e.tools.pageName
    e.tools.pageName.setText('页面属性修改')


def testLegacyOfflineExampleCannotSurviveRealUncapturedSession(designer):  # noqa: F811
    from emo_master.clients.runtime.view_state import SessionView
    _c, e, key = setupPage(designer, 'number')
    e.renderer.setSimulationState('OK')
    assert e.renderer.simulationState == 'OK'
    for status in ('CONNECTING', 'CONNECTED', 'DISCONNECTED', 'ERROR'):
        e.renderer.submit(SessionView(0, 0, '', '', status, '', {}, {}, {}))
        assert e.renderer.simulationState is None
        assert not e.renderer.designExamples
        assert '模拟' not in e.renderer.widgets[e.pageId][key][1].text()
        assert not e.renderer.displayed


def testWorkerCannotClearDesignSamplesBeforeThreadGuard(designer):  # noqa: F811
    import threading
    from emo_master.clients.runtime.view_state import SessionView
    _c, e, key = setupPage(designer, 'image')
    errors = []
    def worker():
        try:
            e.renderer.submit(SessionView(0, 0, '', '', 'CONNECTING', '', {}, {}, {}))
        except RuntimeError as error:
            errors.append(str(error))
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    assert errors == ['Qt presentation requires the GUI thread']
    assert e.renderer.designExamples
    assert not e.renderer.widgets[e.pageId][key][1].image.isNull()
