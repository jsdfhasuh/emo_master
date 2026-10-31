"""Published history remains usable without a live Job, over the normal RPC."""
from copy import deepcopy
import json
import sqlite3
import time

import grpc
from PIL import Image
import pytest

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.core.presentation.history import historyRevision
from emo_master.core.project.models import ProjectDocument


@pytest.fixture
def historyRuntime(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    Image.new('RGB', (7, 5), 'red').save(root / 'record.png')
    with sqlite3.connect(root / 'records.db') as db:
        db.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, result TEXT, score REAL, image TEXT)')
        db.executemany('INSERT INTO records VALUES(?,?,?,?)', [
            (1, 'OK', 3.5, 'record.png'), (2, 'NG', 1.0, 'missing.png'), (3, 'OK', 4.0, '../outside.png')])
    payload = WorkflowStore().toPayload()
    payload['schemaVersion'] = '2.9'
    payload['presentation'] = dict(schemaVersion='1.2', defaultPageId='history', pageOrder=['history'],
        pages=dict(history=dict(name='History', targets=['desktop', 'web'], components=[
            dict(componentId='rows', type='sqlite_table', props=dict(sqlite=dict(location='runtime',
                databasePath='records.db', table='records', columns=['id', 'result'], orderBy='id', descending=False))),
            dict(componentId='total', type='sqlite_value', props=dict(sqlite=dict(location='runtime',
                databasePath='records.db', table='records', aggregate='sum', valueColumn='score'))),
            dict(componentId='photo', type='image', props=dict(imagePath=dict(kind='selected_row',
                componentId='rows', field='image', baseDirectory='.'))),
            dict(componentId='fixed', type='image', props=dict(imagePath=dict(kind='fixed', location='runtime',
                path='record.png', baseDirectory='.')))])))
    document = ProjectDocument.model_validate(payload)
    path = root / 'project.emoproj'
    path.write_text(document.model_dump_json(), encoding='utf-8')
    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    server = channel = None
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        server, port, _ = createRuntimeServer(port=0, runtimeService=service)
        server.start()
        channel = grpc.insecure_channel(f'127.0.0.1:{port}')
        grpc.channel_ready_future(channel).result(timeout=5)
        context = pb.HistoryContext(runtime_instance_id=service.runtimeInstanceId,
            project_id=document.project.projectId, history_definition_revision=historyRevision(document.presentation))
        yield service, rpc.RuntimeServiceStub(channel), context, document
    finally:
        if channel:
            channel.close()
        if server:
            server.stop(0).wait()
        service.close()


def testPageAggregateAndImagesWithoutJobs(historyRuntime):
    service, stub, context, _document = historyRuntime
    assert not service.jobRepository.all()
    reply = stub.QueryHistoryPage(pb.QueryHistoryPageRequest(context=context, component_id='rows', page_size=2), timeout=5)
    assert reply.ok, reply.message
    page = json.loads(reply.result_json)
    assert page['hasNext'] and page['rows'][0]['values'] == [1, 'OK']
    assert page['stableRowIdentity'] and page['rows'][0]['rowId']
    ref = page['rows'][0]['imageRefs']['photo']['imageRef']
    assert 'record.png' not in reply.result_json
    image = stub.ReadHistoryImage(pb.ReadHistoryImageRequest(context=context, image_ref=ref), timeout=5)
    assert image.ok and image.status == 'AVAILABLE' and (image.width, image.height) == (7, 5)
    missing = stub.ReadHistoryImage(pb.ReadHistoryImageRequest(context=context,
        image_ref=page['rows'][1]['imageRefs']['photo']['imageRef']), timeout=5)
    assert missing.ok and missing.status == 'MISSING'
    fixed = stub.ReadHistoryImage(pb.ReadHistoryImageRequest(context=context, component_id='fixed'), timeout=5)
    assert fixed.ok and fixed.data == image.data
    total = stub.QueryHistoryAggregate(pb.QueryHistoryAggregateRequest(context=context, component_id='total'), timeout=5)
    assert total.ok and json.loads(total.result_json)['value'] == 8.5
    assert not service.jobRepository.all()


def testHistoryIdentitySortFilterAndImageConfinement(historyRuntime):
    _service, stub, context, document = historyRuntime
    bad = deepcopy(context)
    bad.history_definition_revision = 'old'
    reply = stub.QueryHistoryPage(pb.QueryHistoryPageRequest(context=bad, component_id='rows'), timeout=5)
    assert not reply.ok and reply.code == 'E_HISTORY_REVISION'
    reply = stub.QueryHistoryPage(pb.QueryHistoryPageRequest(context=context, component_id='rows', order_by='image'), timeout=5)
    assert not reply.ok and reply.code == 'E_HISTORY_SORT'
    reply = stub.QueryHistoryPage(pb.QueryHistoryPageRequest(context=context, component_id='rows',
        filter_column='result', filter_text='OK'), timeout=5)
    rows = json.loads(reply.result_json)['rows']
    assert len(rows) == 2
    image = stub.ReadHistoryImage(pb.ReadHistoryImageRequest(context=context,
        image_ref=rows[1]['imageRefs']['photo']['imageRef']), timeout=5)
    assert not image.ok and '基准目录' in image.message
    changed = document.presentation.model_copy(deep=True)
    changed.pages['history'].name = 'Renamed'
    assert historyRevision(changed) == context.history_definition_revision
    changed.pages['history'].components[0].props.sqlite.columns = ['id']
    assert historyRevision(changed) != context.history_definition_revision


def testQtHistoryPageUsesRpcForTablesAndSelectedImages(historyRuntime, retainedQtApplication):
    _service, stub, context, document = historyRuntime
    from emo_master.clients.runtime.history import HistoryClient
    from emo_master.ui.presentation.renderer import RuntimePages
    adapter = HistoryClient(lambda name, request: getattr(stub, name)(request, timeout=5),
        runtimeInstanceId=context.runtime_instance_id, projectId=context.project_id, presentation=document.presentation)
    # Deliberately use an unrelated Designer directory. Runtime sources must
    # never try to open its local records.db or picture paths.
    pages = RuntimePages(document.presentation, projectRoot='C:/not-the-runtime-project', historyClient=adapter)
    try:
        pages.show()
        table = pages.widgets['history']['rows'][1]
        photo = pages.widgets['history']['photo'][1]
        total = pages.widgets['history']['total'][1]
        def waitFor(condition):
            deadline = time.monotonic() + 5
            while not condition() and time.monotonic() < deadline:
                retainedQtApplication.processEvents()
                time.sleep(.005)
            assert condition()
        waitFor(lambda: table.loaded and total.loaded and not table.busy)
        assert table.model.rows[0] == (1, 'OK') and total.value.text() == '8.50'
        table.table.selectRow(0)
        waitFor(lambda: not photo.image.isNull())
        assert photo.remoteReader is not None
    finally:
        pages.close()
        pages.deleteLater()
        retainedQtApplication.processEvents()
