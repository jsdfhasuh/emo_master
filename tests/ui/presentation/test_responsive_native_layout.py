"""Window resizing repositions existing native widgets without refreshing data."""
from PySide2.QtWidgets import QApplication

from emo_master.core.presentation.models import Component, Page, Presentation, ResponsiveLayout
from emo_master.ui.presentation.renderer import RuntimePages


def settle():
    for _ in range(5):
        QApplication.processEvents()


def testResizeReflowsStableCardsWithoutChangingConfiguration(qtApp):
    page = Page(name='Responsive', layout=ResponsiveLayout(direction='wrap'), components=[
        Component(componentId=str(i), type='text', props={'text': 'Result ' + str(i)},
                  responsive={'minWidth': 160, 'preferredWidth': 160}) for i in range(4)])
    presentation = Presentation(defaultPageId='p', pageOrder=['p'], pages={'p': page})
    viewer = RuntimePages(presentation)
    viewer.resize(1200, 700)
    viewer.show()
    settle()
    try:
        cards = dict(viewer.cards['p'])
        widgets = {key: widget for key, (_item, widget) in viewer.widgets['p'].items()}
        before = viewer.config.model_dump()
        wide = [cards[str(i)].geometry() for i in range(4)]
        assert len({rect.y() for rect in wide}) == 1
        viewer.resize(480, 700)
        settle()
        narrow = [cards[str(i)].geometry() for i in range(4)]
        assert len({rect.y() for rect in narrow}) == 2
        assert all(viewer.cards['p'][key] is card for key, card in cards.items())
        assert all(viewer.widgets['p'][key][1] is widget for key, widget in widgets.items())
        assert viewer.config.model_dump() == before
        assert viewer.layoutHosts['p'][None].layout().childRects
        viewer.resize(1200, 700)
        settle()
        assert [cards[str(i)].geometry() for i in range(4)] == wide
    finally:
        viewer.close()
        viewer.deleteLater()


def testNestedContainerReflowsAndMeasuresWrappedTitles(qtApp):
    group = Component(componentId='group', type='container', props={'title': 'Long container title ' * 8},
        grid=ResponsiveLayout(direction='horizontal'), children=[
            Component(componentId='image', type='image'),
            Component(componentId='value', type='text', props={'text': 'Result'})])
    presentation = Presentation(defaultPageId='p', pageOrder=['p'], pages={
        'p': Page(name='Nested', components=[group])})
    viewer = RuntimePages(presentation)
    viewer.resize(1000, 700)
    viewer.show()
    settle()
    try:
        host = viewer.layoutHosts['p']['group']
        image, value = viewer.cards['p']['image'], viewer.cards['p']['value']
        assert image.y() == value.y()
        host.resize(260, host.height())
        host.layout().setGeometry(host.rect())
        assert image.y() < value.y()
        assert host.layout().heightForWidth(260) > host.layout().heightForWidth(800)
        assert viewer.cards['p']['group'].containerTitle.label.height() > 0
    finally:
        viewer.close()
        viewer.deleteLater()


def testResizePreservesTablePageSelectionAndDoesNotRepeatSqliteQuery(qtApp, monkeypatch):
    from emo_master.ui.presentation.sqlite_widgets import SqliteTableView

    queries = []
    def query(self, _read):
        queries.append(self)
        self.loaded = True
    monkeypatch.setattr(SqliteTableView, '_start', query)
    components = [Component(componentId='table', type='table', props={
        'columns': [{'title': 'Value', 'fieldPath': ['value']}], 'pageSize': 2}),
        Component(componentId='history', type='sqlite_table')]
    presentation = Presentation(defaultPageId='p', pageOrder=['p'], pages={
        'p': Page(name='Data', layout=ResponsiveLayout(direction='horizontal'), components=components)})
    viewer = RuntimePages(presentation)
    viewer.resize(780, 700)
    viewer.show()
    settle()
    try:
        table = viewer.widgets['p']['table'][1]
        history = viewer.widgets['p']['history'][1]
        table.submit([{'value': i} for i in range(6)], 'result-one', '')
        table.move(1)
        table.table.selectRow(0)
        selected = table.table.selectionModel().selectedRows()[0].row()
        assert queries == [history]
        for width in (480, 720, 540, 780):
            viewer.resize(width, 650)
            settle()
        assert table.model.page == 1 and table.model.key == 'result-one'
        assert table.table.selectionModel().selectedRows()[0].row() == selected
        assert queries == [history]
        assert viewer.widgets['p']['table'][1] is table
        assert viewer.widgets['p']['history'][1] is history
    finally:
        viewer.close()
        viewer.deleteLater()
