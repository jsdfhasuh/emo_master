"""Local history reads and native page controls, using temporary databases."""
import sqlite3
import threading
import time

import pytest
from PySide2.QtCore import Qt

from emo_master.core.presentation.models import SqliteSource
from emo_master.core.presentation.sqlite_reader import inspectDatabase, readPage, readValue
from emo_master.ui.presentation.sqlite_widgets import SqliteTableView, SqliteValueView


@pytest.fixture
def database(tmp_path):
    path = tmp_path / '已有记录.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE "inspection log" (id INTEGER PRIMARY KEY, "check result" TEXT, value REAL, image BLOB)')
        connection.executemany('INSERT INTO "inspection log" VALUES (?, ?, ?, ?)', [
            (1, 'OK', 1.5, b'a'), (2, 'NG', 2.5, b'b'), (3, '100%_OK', 3.5, b'c')])
        connection.execute('CREATE VIEW summary AS SELECT id, value FROM "inspection log"')
    return SqliteSource(databasePath=str(path), table='inspection log',
                        columns=['id', 'check result', 'value'], orderBy='id')


def waitRead(qtApp, view):
    deadline = time.monotonic() + 4
    while view.busy and time.monotonic() < deadline:
        qtApp.processEvents()
        time.sleep(0.005)
    qtApp.processEvents()
    assert not view.busy


def testMetadataPaginationFilteringAndQuotedNames(database):
    schema = inspectDatabase(database.databasePath)
    assert schema['inspection log'] == ['id', 'check result', 'value', 'image']
    assert schema['summary'] == ['id', 'value']
    first = readPage(database, pageSize=2)
    assert first.rows == ((3, '100%_OK', 3.5), (2, 'NG', 2.5)) and first.hasNext
    last = readPage(database, pageSize=2, page=1)
    assert last.rows == ((1, 'OK', 1.5),) and not last.hasNext
    filtered = readPage(database, filterColumn='check result', filterText='%_')
    assert filtered.rows == ((3, '100%_OK', 3.5),)
    injection = readPage(database, filterColumn='check result', filterText="' OR 1=1 --")
    assert not injection.rows
    with sqlite3.connect(database.databasePath) as connection:
        connection.execute('CREATE TABLE "a""b" ("c""d" TEXT)')
        connection.execute('INSERT INTO "a""b" VALUES (?)', ('quoted',))
    quoted = database.model_copy(update={'table': 'a"b', 'columns': ['c"d'], 'orderBy': 'c"d'})
    assert readPage(quoted).rows == (('quoted',),)


def testReadsNeverCreateDatabaseAndRejectStaleSelection(database, tmp_path):
    absent = tmp_path / 'absent.db'
    with pytest.raises(ValueError, match='不存在'):
        inspectDatabase(str(absent))
    assert not absent.exists()
    with pytest.raises(ValueError, match='显示字段'):
        readPage(database.model_copy(update={'columns': ['removed']}))
    with pytest.raises(ValueError, match='数据表'):
        readPage(database.model_copy(update={'table': 'removed'}))
    with sqlite3.connect(database.databasePath) as connection:
        assert connection.execute('SELECT COUNT(*) FROM "inspection log"').fetchone()[0] == 3


@pytest.mark.parametrize('aggregate, expected', [('count', 3), ('sum', 7.5), ('avg', 2.5), ('min', 1.5), ('max', 3.5)])
def testAggregates(database, aggregate, expected):
    source = database.model_copy(update={'aggregate': aggregate, 'valueColumn': 'value'})
    assert readValue(source) == expected


def testNonNumericSumAndLargeDisplayValues(database):
    with pytest.raises(ValueError, match='非数值'):
        readValue(database.model_copy(update={'aggregate': 'sum', 'valueColumn': 'check result'}))
    with sqlite3.connect(database.databasePath) as connection:
        connection.execute('UPDATE "inspection log" SET "check result"=?, image=? WHERE id=3',
                           ('x' * 10000, b'x' * 100000))
    source = database.model_copy(update={'columns': ['check result', 'image']})
    page = readPage(source)
    assert len(page.rows[0][0]) == 4097 and page.rows[0][1] == '[BLOB 100000 字节]'
    source = source.model_copy(update={'aggregate': 'max', 'valueColumn': 'image'})
    assert readValue(source) == '[BLOB 100000 字节]'


def testNativeHistoryPagingFilteringSortingAndRefresh(qtApp, database):
    view = SqliteTableView(database, pageSize=2)
    assert not view.busy and not view.loaded
    view.show()
    waitRead(qtApp, view)
    assert view.model.rows[0][0] == 3 and view.nextButton.isEnabled()
    view.nextButton.click()
    waitRead(qtApp, view)
    assert view.model.rows[0][0] == 1 and not view.nextButton.isEnabled()
    view.filterColumn.setCurrentText('check result')
    view.search.setText('NG')
    view.filterButton.click()
    waitRead(qtApp, view)
    assert view.page == 0 and view.model.rows == ((2, 'NG', 2.5),)
    view.search.clear()
    view.filterButton.click()
    waitRead(qtApp, view)
    view.sortColumn(0)
    waitRead(qtApp, view)
    assert view.model.rows[0][0] == 1
    with sqlite3.connect(database.databasePath) as connection:
        connection.execute('DELETE FROM "inspection log" WHERE id=1')
    view.refreshButton.click()
    waitRead(qtApp, view)
    assert view.model.rows[0][0] == 2
    assert not view.model.flags(view.model.index(0, 0)) & Qt.ItemIsEditable


def testDesignPreviewDoesNotReadAndLateResultCannotReplacePreview(qtApp, database, monkeypatch):
    from emo_master.ui.presentation import sqlite_widgets
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = sqlite_widgets.readPage

    def slowRead(*args, **kwargs):
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite_widgets, 'readPage', slowRead)
    view = SqliteTableView(database, preview=True)
    view.show()
    qtApp.processEvents()
    assert not calls and not view.refreshButton.isEnabled()
    view.setPreview(False)
    assert not calls
    view.refreshButton.click()
    assert entered.wait(1)
    assert calls[0] != threading.get_ident()
    view.setPreview(True)
    release.set()
    waitRead(qtApp, view)
    assert not view.model.rows and '设计示例' in view.message.text()
    view.setPreview(False)
    view.refreshButton.click()
    waitRead(qtApp, view)
    assert view.model.rowCount() == 3


def testValueRefreshErrorAndNull(qtApp, database):
    source = database.model_copy(update={'aggregate': 'avg', 'valueColumn': 'value'})
    view = SqliteValueView(source, decimals=1, unit='mm')
    view.show()
    waitRead(qtApp, view)
    assert view.value.text() == '2.5 mm'
    with sqlite3.connect(database.databasePath) as connection:
        connection.execute('DELETE FROM "inspection log"')
    view.refreshButton.click()
    waitRead(qtApp, view)
    assert view.value.text() == '—' and '没有可统计' in view.message.text()
    with sqlite3.connect(database.databasePath) as connection:
        connection.execute('DROP TABLE "inspection log"')
    view.refreshButton.click()
    waitRead(qtApp, view)
    assert view.value.text() == '—' and '数据表不存在' in view.message.text()
    assert view.refreshButton.isEnabled()


def testClosingHistoryDuringQueryReleasesWorkerWithoutUpdatingDeletedWidget(qtApp, database, monkeypatch):
    from PySide2.QtCore import QCoreApplication, QEvent, QThreadPool
    from emo_master.ui.presentation import sqlite_widgets
    import shiboken2

    entered, release = threading.Event(), threading.Event()
    original = sqlite_widgets.readPage

    def delayedRead(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite_widgets, 'readPage', delayedRead)
    view = SqliteTableView(database)
    view.show()
    assert entered.wait(1)
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not shiboken2.isValid(view)
    release.set()
    assert QThreadPool.globalInstance().waitForDone(3000)
    qtApp.processEvents()
