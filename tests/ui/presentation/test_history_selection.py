"""Stable selection reconciliation for independently refreshed history results."""
import json
import time

import pytest

from emo_master.clients.runtime.history import HistoryClient, HistoryImageRef
from emo_master.core.presentation.models import Presentation, SqliteSource
from emo_master.core.presentation.sqlite_reader import SqlitePage
from emo_master.ui.presentation.sqlite_widgets import SqliteTableView


@pytest.mark.parametrize('stable', [False, True])
def testClientHonorsStableIdentityFlagEvenWithNonemptyRowIds(stable):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    payload = dict(columns=[dict(name='id', type='scalar')], rows=[
        dict(rowId='response-local-id', values=[1], imageRefs={})], page=0, hasNext=False,
        stableRowIdentity=stable)
    client = HistoryClient(lambda _method, _request: pb.HistoryReply(ok=True, result_json=json.dumps(payload)),
        runtimeInstanceId='runtime', projectId='project', presentation=Presentation())
    result = client.readPage('rows')
    assert result.rowIds == (('response-local-id',) if stable else ('',))
    assert result.rows == ((1,),)


def page(ids, revision, *, rowIds=None, pageNumber=0, hasNext=False):
    return SqlitePage(('id',), tuple((value,) for value in ids), pageNumber, hasNext,
        tuple({'image': HistoryImageRef(imageRef=f'{revision}-{value}')} for value in ids),
        tuple(str(value) for value in ids) if rowIds is None else tuple(rowIds))


class History:
    def __init__(self, result):
        self.result = result

    def readPage(self, _componentId, **_arguments):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def waitRead(app, view):
    deadline = time.monotonic() + 4
    while view.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert not view.busy


@pytest.fixture
def historyView(qtApp):
    source = SqliteSource(databasePath='history.db', table='history', columns=['id'],
                          orderBy='id', descending=False, location='runtime')
    view = SqliteTableView(source, pageSize=2, selectionFields=('image',))
    view.historyClient = History(page((1, 2), 'old', hasNext=True))
    view.show()
    waitRead(qtApp, view)
    view.table.selectRow(0)
    assert view.selectedRow()['image'].imageRef == 'old-1'
    yield view
    view.close()
    waitRead(qtApp, view)


@pytest.mark.parametrize('action', ['refresh', 'sort', 'filter'])
def testSameIdentityUsesFreshReferencesAfterReordering(qtApp, historyView, action):
    view = historyView
    changes = []
    view.selectedRowChanged.connect(changes.append)
    view.historyClient.result = page((2, 1), 'new')
    if action == 'sort':
        view.sortColumn(0)
    elif action == 'filter':
        view.search.setText('1')
        view.applyFilter()
    else:
        view.refresh()
    assert view.selectedRow() is None and changes[-1] is None
    waitRead(qtApp, view)
    assert view.table.selectionModel().selectedRows()[0].row() == 1
    assert view.selectedRow()['image'].imageRef == 'new-1'
    assert changes[-1]['image'].imageRef == 'new-1'


@pytest.mark.parametrize('ids, rowIds', [((2, 3), ('2', '3')), ((1, 2), ('', ''))])
def testMissingOrUnstableIdentityClearsInsteadOfRestoringRowNumber(qtApp, historyView, ids, rowIds):
    view = historyView
    view.historyClient.result = page(ids, 'new', rowIds=rowIds)
    view.refresh()
    waitRead(qtApp, view)
    assert view.selectedRow() is None
    assert not view.table.selectionModel().selectedRows()


def testUnstableOriginalSelectionDoesNotBecomeStableAfterRefresh(qtApp, historyView):
    view = historyView
    view.historyClient.result = page((1, 2), 'unstable', rowIds=('', ''))
    view.refresh()
    waitRead(qtApp, view)
    view.table.selectRow(0)
    assert view.selectedRow()['image'].imageRef == 'unstable-1'
    view.historyClient.result = page((1, 2), 'stable')
    view.refresh()
    waitRead(qtApp, view)
    assert view.selectedRow() is None


def testQueryFailureDiscardsSelectionBeforeNextSuccessfulRefresh(qtApp, historyView):
    view = historyView
    view.historyClient.result = ValueError('connection unavailable')
    view.refresh()
    waitRead(qtApp, view)
    assert view.selectedRow() is None and 'connection unavailable' in view.message.text()
    view.historyClient.result = page((1, 2), 'reconnected')
    view.refresh()
    waitRead(qtApp, view)
    assert view.model.rowCount() == 2 and view.selectedRow() is None


@pytest.mark.parametrize('reset', ['hide', 'source', 'preview', 'clear'])
def testPendingRefreshCannotRestoreSelectionAfterContextReset(qtApp, historyView, reset):
    view = historyView
    view.historyClient.result = page((2, 1), 'new')
    view.refresh()
    if reset == 'hide':
        view.hide()
    elif reset == 'source':
        view.setProjectRoot('changed-project-root')
    elif reset == 'preview':
        view.setPreview(True)
    else:
        view.clearSelection()
    waitRead(qtApp, view)
    assert view.selectedRow() is None and not view.table.selectionModel().selectedRows()


def testPagingDoesNotRestoreEvenIfRecordOccursOnNewPage(qtApp, historyView):
    view = historyView
    view.historyClient.result = page((1, 3), 'next', pageNumber=1)
    view.move(1)
    waitRead(qtApp, view)
    assert view.page == 1 and view.selectedRow() is None
