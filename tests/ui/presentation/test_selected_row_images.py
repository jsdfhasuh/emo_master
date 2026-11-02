"""Page-local table/image integration; no Runtime or existing database needed."""
import sqlite3
import time

import pytest
from PySide2.QtGui import QColor, QImage

from emo_master.clients.runtime.view_state import SessionView
from emo_master.core.presentation.models import Presentation
from emo_master.core.project.snapshots import captureDefinition
from emo_master.ui.presentation.renderer import RuntimePages


def settle(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, 'page did not settle'
        app.processEvents()
        time.sleep(.005)
    app.processEvents()


@pytest.fixture
def history(tmp_path):
    for name, color in [('原图.png', 'red'), ('结果.png', 'green'), ('下一张.png', 'blue')]:
        image = QImage(48, 32, QImage.Format_RGB32)
        image.fill(QColor(color))
        assert image.save(str(tmp_path / name))
    path = tmp_path / 'history.db'
    connection = sqlite3.connect(path)
    try:
        connection.execute('CREATE TABLE history (id INTEGER, original_image TEXT, result_image TEXT)')
        connection.executemany('INSERT INTO history VALUES (?,?,?)', [
            (1, '原图.png', '结果.png'), (2, '下一张.png', '不存在.png'), (3, None, None)])
        connection.commit()
    finally:
        connection.close()
    return Presentation.model_validate(dict(defaultPageId='history', pageOrder=['history', 'other', 'third'], pages={
        'history': dict(name='历史', layout={'columns': 2}, components=[
            # Images deliberately precede their nested source table.
            dict(componentId='original', type='image', props={'imagePath': dict(
                kind='selected_row', componentId='records', field='original_image', baseDirectory=str(tmp_path))}),
            dict(componentId='result', type='image', layout={'column': 1}, props={'imagePath': dict(
                kind='selected_row', componentId='records', field='result_image', baseDirectory=str(tmp_path))}),
            dict(componentId='group', type='container', layout={'row': 1, 'columnSpan': 2}, children=[
                dict(componentId='records', type='sqlite_table', props={'pageSize': 2, 'sqlite': dict(
                    databasePath=str(path), table='history', columns=['id'], orderBy='id', descending=False)})])]),
        'other': dict(name='其他'), 'third': dict(name='第三页')}))


def openHistory(app, config):
    window = RuntimePages(config)
    window.show()
    table = window.widgets['history']['records'][1]
    settle(app, lambda: table.loaded and not table.busy)
    return window, table, window.widgets['history']['original'][1], window.widgets['history']['result'][1]


def testOneSelectedRowDrivesTwoImagesWithoutShowingPathColumns(qtApp, history):
    window, table, original, result = openHistory(qtApp, history)
    assert table.model.columns == ('id',)
    assert original.image.isNull() and result.image.isNull()
    table.table.selectRow(0)
    settle(qtApp, lambda: not original.image.isNull() and not result.image.isNull())
    assert original.image.pixelColor(0, 0) == QColor('red')
    assert result.image.pixelColor(0, 0) == QColor('green')
    window.releaseReplacedImages(SessionView(0, 0, '', '', 'OFFLINE', 'no job', {}, {}, {}))
    window.submit(SessionView(0, 0, '', '', 'OFFLINE', 'no job', {}, {}, {}))
    assert original.image.pixelColor(0, 0) == QColor('red')
    assert window.imageSources() == set() and not window.records
    table.table.selectRow(1)
    settle(qtApp, lambda: not original.image.isNull() and '不存在' in result.message)
    assert original.image.pixelColor(0, 0) == QColor('blue')
    assert result.image.isNull()
    table.nextButton.click()
    assert table.selectedRow() is None and original.image.isNull() and result.image.isNull()
    settle(qtApp, lambda: not table.busy)
    table.table.selectRow(0)
    assert original.image.isNull() and result.image.isNull()
    window.close()


@pytest.mark.parametrize('action', ['refresh', 'sort', 'filter'])
def testNewQueryImmediatelyClearsSelectionAndImages(qtApp, history, action):
    window, table, original, _result = openHistory(qtApp, history)
    table.table.selectRow(0)
    settle(qtApp, lambda: not original.image.isNull())
    if action == 'refresh':
        table.refreshButton.click()
    elif action == 'sort':
        table.sortColumn(0)
    else:
        table.search.setText('2')
        table.filterButton.click()
    assert table.selectedRow() is None and original.image.isNull()
    settle(qtApp, lambda: not table.busy)
    if action == 'refresh':
        settle(qtApp, lambda: not original.image.isNull())
        assert table.table.selectionModel().selectedRows()[0].row() == 0
        assert original.image.pixelColor(0, 0) == QColor('red')
    else:
        # The selected record no longer belongs to this filtered/sorted page.
        assert table.selectedRow() is None and original.image.isNull()
    window.close()


def testSelectionsAreWindowLocalAndNavigationReloadRetiresImages(qtApp, history):
    first, table, image, _ = openHistory(qtApp, history)
    second, secondTable, secondImage, _ = openHistory(qtApp, history)
    table.table.selectRow(0)
    secondTable.table.selectRow(1)
    settle(qtApp, lambda: not image.image.isNull() and not secondImage.image.isNull())
    assert image.image.pixelColor(0, 0) == QColor('red')
    assert secondImage.image.pixelColor(0, 0) == QColor('blue')
    first.navigate('other')
    assert table.selectedRow() is None and image.image.isNull()
    first.navigate('third')  # Evicts history from the two-page cache.
    first.navigate('history')
    assert first.widgets['history']['original'][1].image.isNull()
    changed = history.model_copy(deep=True)
    changed.pages['history'].components.pop()  # Deleted source remains a visible draft error.
    first.reload(changed)
    missing = first.widgets['history']['original'][1]
    settle(qtApp, lambda: '表格不存在' in missing.message)
    assert missing.image.isNull() and '表格不存在' in missing.message
    assert secondImage.image.pixelColor(0, 0) == QColor('blue')
    first.close()
    second.close()


def testFixedPathEditorAndSimulationDoNotReadFiles(qtApp, history, monkeypatch):
    from emo_master.ui.presentation import path_image

    config = history.model_copy(deep=True)
    source = config.pages['history'].components[0].props.imagePath
    source.kind, source.componentId, source.field, source.path = 'fixed', None, '', '原图.png'
    calls = []
    # Worker creation is sufficient to detect unwanted I/O in a preview.
    originalStart = path_image.QThreadPool.start

    def start(pool, worker, *args):
        calls.append(worker)
        return originalStart(pool, worker, *args)

    monkeypatch.setattr(path_image.QThreadPool, 'start', start)
    window = RuntimePages(config)
    window.setEditorHost()
    window.show()
    qtApp.processEvents()
    window.setDesignExamples(True)
    window.setSimulationState('OK')
    qtApp.processEvents()
    assert not calls
    assert window.widgets['history']['original'][1].image.isNull()
    window.close()
    live = RuntimePages(config)
    live.show()
    image = live.widgets['history']['original'][1]
    settle(qtApp, lambda: not image.image.isNull())
    assert image.image.pixelColor(0, 0) == QColor('red')
    live.hide()
    assert image.image.isNull()
    live.show()
    settle(qtApp, lambda: not image.image.isNull())
    live.close()


def testPagePathBindingsDoNotChangeWorkflowCapture(history):
    baseline = history.model_copy(deep=True)
    for component in baseline.pages['history'].components[:2]:
        component.props.imagePath = None
    assert captureDefinition(history) == captureDefinition(baseline)
