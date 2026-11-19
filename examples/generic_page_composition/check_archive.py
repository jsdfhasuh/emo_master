"""Run the delivered project in an isolated software Runtime and verify its archive."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from uuid import uuid4

import cv2
import grpc
import numpy as np

from emo_master.apps.designer.state.project_store import loadProjectDocument
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.core.presentation.history import historyRevision

from build_example import DATABASE, HISTORY, PROJECT, SCOPE


def waitFor(callback):
    end = monotonic() + 35
    while monotonic() < end:
        if result := callback():
            return result
        sleep(.08)
    raise TimeoutError('Runtime did not produce a terminal result in 35 seconds')


def unpack(reply):
    assert reply.ok, reply.message
    return json.loads(reply.result_json)


def byRow(reply, row):
    return dict(zip((column['name'] for column in reply['columns']), row['values']))


def asPixels(data):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def verify(executableProject):
    project = loadProjectDocument(executableProject)
    projectRoot = executableProject.parent
    assert not project.devices.definitions and project.presentation.pages.keys() == {'production', 'history'}
    with TemporaryDirectory(prefix='emo-generic-archive-') as temporary:
        root = Path(temporary)
        copy = root / 'project'
        archive = copy / DATABASE
        archive.parent.mkdir(parents=True)
        for name in (PROJECT, DATABASE):
            shutil.copyfile(projectRoot / name, copy / name)
        shutil.copytree(projectRoot / 'assets', copy / 'assets')
        with closing(sqlite3.connect(archive)) as database:
            assert database.execute('SELECT COUNT(*) FROM records').fetchone() == (0,)
        runtime = RuntimeService(dbPath=root / 'runtime.db', workspaceRoot=root / 'jobs')
        server = None
        try:
            loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(copy / PROJECT)), None)
            assert loaded.ok, loaded.message
            server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
            server.start()
            with grpc.insecure_channel(f'127.0.0.1:{port}') as channel:
                grpc.channel_ready_future(channel).result(timeout=10)
                return verifyJobs(channel, runtime.runtimeInstanceId, project, archive)
        finally:
            if server is not None:
                server.stop(0).wait()
            runtime.close()


def verifyJobs(channel, runtimeId, project, database):
    runtime = rpc.RuntimeServiceStub(channel)
    display = rpc.DisplayServiceStub(channel)
    projectId = project.project.projectId
    historyContext = pb.HistoryContext(runtime_instance_id=runtimeId, project_id=projectId,
        history_definition_revision=historyRevision(project.presentation))

    def query(**options):
        return unpack(runtime.QueryHistoryPage(pb.QueryHistoryPageRequest(context=historyContext,
            component_id=HISTORY, page_size=3, **options), timeout=10))

    def anchored(selection, step):
        page = query(anchor_key_json=json.dumps(selection['keyValues']), anchor_step=step)
        row = next(row for row in page['rows'] if row['rowId'] == page['selectedRowId'])
        return page, row

    def start(sequence, expectedCode):
        started = runtime.StartJob(pb.StartJobRequest(project_id=projectId, start_request_id=uuid4().hex,
            capture_presentation=True, expected_runtime_instance_id=runtimeId), timeout=15)
        assert started.ok, started.message

        def terminal():
            value = runtime.GetJobStatus(pb.GetJobStatusRequest(job_id=started.job_id), timeout=5)
            if value.status in {'COMPLETED', 'FAILED', 'ABORTED'}:
                return value
            return None

        job = waitFor(terminal)
        assert job.status == 'COMPLETED', (job.status, job.error_code, job.message)
        replay = pb.DisplayRequest(runtime_instance_id=runtimeId, job_id=started.job_id, replay=True)
        results = waitFor(lambda: display.Snapshot(replay, timeout=5).results)
        result = next(result for result in results if result.identity.result_scope_id == SCOPE)
        capture = {item.source_id: item for item in result.sources}
        values = {key: json.loads(item.value_json) for key, item in capture.items() if item.HasField('value_json')}
        assert values['sequence'] == sequence and values['result-code'] == expectedCode
        assert values['archive-status'] == 'COMMITTED' and values['angle'] == expectedCode * 5
        assert result.identity.invocation_id == values['request-id']
        image = display.ReadAsset(pb.DisplayAssetRequest(runtime_instance_id=runtimeId, job_id=started.job_id,
            resource_id=capture['overlay'].image.resource_id), timeout=8).content
        page = query()
        row = page['rows'][0]
        record = byRow(page, row)
        assert row['keyValues'] == [values['record-key']] and record['result_code'] == expectedCode
        assert record['sequence'] == sequence and record['review_note'] is None and '\n' in record['record_log']
        root = database.parent.parent
        with closing(sqlite3.connect(database)) as connection:
            key, mainPath, detailPath = connection.execute(
                'SELECT workflow_run_id, main_path, detail_path FROM records WHERE write_id = ?',
                (values['record-key'],)).fetchone()
        assert key == values['request-id']
        dimensions = {}
        for component, file in (('history-main', mainPath), ('history-detail', detailPath)):
            assert not Path(file).is_absolute() and Path(file).parent == Path('archive/images')
            reference = row['imageRefs'][component]['imageRef']
            actual = runtime.ReadHistoryImage(pb.ReadHistoryImageRequest(context=historyContext, image_ref=reference), timeout=5)
            assert actual.ok and actual.status == 'AVAILABLE', actual.message
            assert actual.data == (root / file).read_bytes()
            dimensions[component] = [actual.width, actual.height]
            if component == 'history-main':
                # The display service may use a different PNG compression level.
                pixels = asPixels(actual.data)
                np.testing.assert_array_equal(asPixels(image), pixels)
                # This run's selected upper/lower centers are (222, 206) and
                # (402, 282). Check dashed pixels between their colored boxes.
                xs = np.arange(290, 320)
                ys = np.rint(206 + (xs - 222) * (282 - 206) / (402 - 222)).astype(int)
                white = np.all(pixels[ys, xs] == 255, axis=1)
                assert any(white) and not all(white)

        def release():
            try:
                return display.ReleaseJob(replay, timeout=8)
            except grpc.RpcError as error:
                if error.code() != grpc.StatusCode.FAILED_PRECONDITION or error.details() != 'display IPC still retiring':
                    raise
                return None

        waitFor(release)
        return dict(sequence=sequence, code=expectedCode, recordKey=values['record-key'],
                    invocationId=key, dimensions=dimensions, displaySha256=hashlib.sha256(image).hexdigest())

    produced = [start(sequence, code) for sequence, code in enumerate([1, 1, 1, 2, 2], 1)]
    first = query()
    assert first['hasNext'] and [byRow(first, row)['sequence'] for row in first['rows']] == [5, 4, 3]
    selected = first['rows'][-1]
    nextPage, older = anchored(selected, 1)
    assert nextPage['page'] == 1 and byRow(nextPage, older)['sequence'] == 2
    produced.append(start(6, 3))
    returnPage, restored = anchored(selected, 0)
    assert restored['rowId'] == selected['rowId'] and returnPage['page'] == 1
    assert restored['imageRefs']['history-main']['imageRef'] != selected['imageRefs']['history-main']['imageRef']
    page, older = anchored(restored, 1)
    assert byRow(page, older)['sequence'] == 2
    page, newer = anchored(older, -1)
    assert byRow(page, newer)['sequence'] == 3
    latest = query()
    assert latest['rows'][0]['keyValues'] == [produced[-1]['recordKey']]
    filtered = query(filter_column='result_code', filter_text='2')
    assert [byRow(filtered, row)['sequence'] for row in filtered['rows']] == [5, 4]
    total = unpack(runtime.QueryHistoryAggregate(pb.QueryHistoryAggregateRequest(context=historyContext,
        component_id=HISTORY, filter_column='result_code', filter_text='2'), timeout=5))
    assert total['value'] == 2
    assert produced[0]['displaySha256'] != produced[-1]['displaySha256']
    return dict(records=produced, pageSize=3, restoredAfterAppend=3, nextAfterAppend=2,
                returnedToLatest=6, filtered=[5, 4], filteredCount=2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    evidence = verify(parser.parse_args().project.resolve())
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
