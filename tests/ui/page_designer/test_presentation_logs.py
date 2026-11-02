"""Page-observed Job diagnostics enter the existing Designer log window."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import time

import grpc
import pytest

from emo_master.apps.designer.page_designer.runtime_logs import PreviewRuntimeLogs
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from tests.ui.page_designer.test_current_job_viewing import designer as designerFixture, metadata, waitFor

designer = designerFixture


def event(job, sequence, kind='presentation.source.unavailable', level='WARN'):
    recovered = kind == 'presentation.source.recovered'
    return pb.JobEvent(job_id=job, sequence=sequence, event_type=kind, level=level,
        project_id='project', workflow_id='detect', node_id='preview',
        code='PRESENTATION_RECOVERED' if recovered else 'BUDGET_EXCEEDED',
        message='页面展示已恢复' if recovered else '页面预览图片超过显示上限', timestamp_ms=1000 + sequence,
        payload_json=json.dumps({'sourceId': 'live', 'resultKey': f'result-{sequence}',
                                 'reason': 'raw image 6220800 bytes exceeds source lane limit 4194304; no implicit resize'}),
        iteration_path_json='[2]')


class LogService(rpc.RuntimeServiceServicer):
    def __init__(self):
        self.rows = {}
        self.requests = []
        self.cancelled = set()
        self.failures = []
        self.lock = threading.Lock()

    def StreamJobEvents(self, request, context):
        with self.lock:
            self.requests.append((request.job_id, request.after_sequence, request.follow))
            failure = self.failures.pop(0) if self.failures else None
        cursor = request.after_sequence
        try:
            if failure is not None:
                context.abort(failure, 'controlled log-stream failure')
            while context.is_active():
                for row in list(self.rows.get(request.job_id, ())):
                    if row.sequence > cursor:
                        yield row
                        cursor = row.sequence
                time.sleep(.01)
        finally:
            self.cancelled.add(request.job_id)


@pytest.fixture
def logsServer():
    service = LogService()
    server = grpc.server(ThreadPoolExecutor(max_workers=4))
    rpc.add_RuntimeServiceServicer_to_server(service, server)
    port = server.add_insecure_port('127.0.0.1:0')
    server.start()
    yield service, f'127.0.0.1:{port}'
    server.stop(0).wait(3)


def testPairOmitsOrdinaryFrameEventsAndResumesWithoutReplay(logsServer):
    service, address = logsServer
    service.rows = {
        'producer': [event('producer', 1, 'node.completed', 'INFO'), event('producer', 2)],
        'consumer': [event('consumer', 1, level='ERROR')],
    }
    observer = PreviewRuntimeLogs(address, ['producer', 'consumer'])
    rows = []
    try:
        def ready():
            rows.extend(observer.take())
            return len(rows) == 2
        waitFor(ready)
        assert {row['jobId'] for kind, row in rows if kind == 'event'} == {'producer', 'consumer'}
        assert all(row['payload']['sourceId'] == 'live' and row['iterationPath'] == [2] for _, row in rows)
        assert {row['level'] for _, row in rows} == {'WARN', 'ERROR'}
    finally:
        observer.close()
    assert not observer.take()
    assert all(not thread.is_alive() for thread in observer.threads)
    waitFor(lambda: service.cancelled == {'producer', 'consumer'})
    positions = observer.positions()
    assert positions == {'producer': 2, 'consumer': 1}
    service.rows['consumer'].append(event('consumer', 2, 'presentation.source.recovered', 'INFO'))
    again = PreviewRuntimeLogs(address, ['consumer'], positions)
    try:
        rows = []
        def resumed():
            rows.extend(again.take())
            return bool(rows)
        waitFor(resumed)
        assert len(rows) == 1 and rows[0][1]['eventType'] == 'presentation.source.recovered'
        assert ('consumer', 1, True) in service.requests
    finally:
        again.close()


def testExternalJobForwardsBusinessFailuresAndTerminal(logsServer):
    service, address = logsServer
    service.rows['job'] = [event('job', 1, 'node.completed', 'INFO'),
                           event('job', 2, 'node.failed', 'ERROR'),
                           event('job', 3, 'node.log', 'WARN'),
                           event('job', 4, 'job.aborted', 'INFO')]
    observer = PreviewRuntimeLogs(address, ['job'])
    rows = []
    try:
        def ready():
            rows.extend(observer.take())
            return len(rows) == 3
        waitFor(ready)
        assert [row['eventType'] for kind, row in rows if kind == 'event'] == ['node.failed', 'node.log', 'job.aborted']
        assert observer.positions() == {'job': 4}
    finally:
        observer.close()


def testLocalDisplayDiagnosticsDrainToPageLogsAndOnClose(designer, monkeypatch):
    from emo_master.clients.runtime.display_logs import DisplayLog

    window, client, document = designer
    client.jobs = [metadata(document, 'external-job')]
    preview = window.pageCoordinator.preview
    preview.watchCurrent()
    waitFor(lambda: preview.hub is not None)
    logs = DisplayLog()
    context = {'projectId': document.project.projectId, 'jobId': 'external-job',
               'payload': {'sourceId': 'live', 'identity': {'resultKey': 'result-1'}}}
    logs.failed('image', 'IMAGE_DECODE_FAILED', 'ERROR', '图片校验失败', context)
    session = preview.session
    monkeypatch.setattr(session, 'takeLogEvents', logs.take, raising=False)
    waitFor(lambda: any(entry.code == 'IMAGE_DECODE_FAILED' for entry in window.logEntries))
    entry = next(entry for entry in window.logEntries if entry.code == 'IMAGE_DECODE_FAILED')
    assert entry.source == 'page_designer' and entry.designArea() == 'page'
    assert entry.jobId == 'external-job' and entry.payload['sourceId'] == 'live'
    assert entry.payload['identity']['resultKey'] == 'result-1'
    original = session.close
    def close():
        logs.healthy('image', context)
        logs.finish()
        original()
    monkeypatch.setattr(session, 'close', close)
    preview.stopViewing()
    waitFor(lambda: not preview.active())
    assert any(entry.eventType == 'presentation.client.recovered' for entry in window.logEntries)


def testMissingLogRpcIsReportedOnceAndCancelDoesNotReportFailure(qtApp):
    server = grpc.server(ThreadPoolExecutor(max_workers=1))
    port = server.add_insecure_port('127.0.0.1:0')
    server.start()
    observer = PreviewRuntimeLogs(f'127.0.0.1:{port}', ['external-job'])
    try:
        waitFor(lambda: all(not thread.is_alive() for thread in observer.threads))
        rows = observer.take()
        assert len(rows) == 1 and rows[0][0] == 'error'
        assert 'UNIMPLEMENTED' in rows[0][1] and 'external-job' in rows[0][1]
        observer.close()
        assert observer.take() == []
    finally:
        observer.close()
        server.stop(0).wait(3)


def testSingleDisconnectRetriesAndContinuesReceivingLogs(logsServer):
    service, address = logsServer
    service.failures = [grpc.StatusCode.UNAVAILABLE]
    service.rows['job'] = [event('job', 1)]
    observer = PreviewRuntimeLogs(address, ['job'])
    rows = []
    try:
        def ready():
            rows.extend(observer.take())
            return any(kind == 'event' for kind, _ in rows)
        waitFor(ready)
        assert service.requests == [('job', 0, True), ('job', 0, True)]
        assert len([row for kind, row in rows if kind == 'event']) == 1
        assert len([row for kind, row in rows if kind == 'error']) == 1
        assert not any('已停止' in row for kind, row in rows if kind == 'error')
        assert observer.positions() == {'job': 1}
    finally:
        observer.close()
    assert observer.take() == []


@pytest.mark.parametrize('status,attempts', [
    (grpc.StatusCode.UNAVAILABLE, 4), (grpc.StatusCode.DEADLINE_EXCEEDED, 4),
    (grpc.StatusCode.PERMISSION_DENIED, 1), (grpc.StatusCode.INVALID_ARGUMENT, 1),
])
def testLogRetriesAreBoundedAndPermanentErrorsStopImmediately(logsServer, status, attempts):
    service, address = logsServer
    service.failures = [status] * 5
    observer = PreviewRuntimeLogs(address, ['job'])
    try:
        waitFor(lambda: all(not thread.is_alive() for thread in observer.threads))
        assert len(service.requests) == attempts
        rows = observer.take()
        assert len(rows) == (2 if attempts == 4 else 1)
        assert all(kind == 'error' for kind, _ in rows)
        assert '日志订阅已停止' in rows[-1][1] and '重新查看' in rows[-1][1]
        assert observer.positions() == {'job': 0}
    finally:
        observer.close()


def testObservedJobAppearsInExistingLogWindowWithDetails(designer, logsServer, monkeypatch):
    window, client, document = designer
    service, address = logsServer
    client.capabilities.capabilities.append('presentation_logs')
    client.jobs = [metadata(document, 'external-job')]
    monkeypatch.setattr(client, 'displayAddress', lambda: address)
    row = event('external-job', 1)
    row.project_id = document.project.projectId
    service.rows['external-job'] = [row]
    preview = window.pageCoordinator.preview
    preview.watchCurrent()
    waitFor(lambda: any(entry.eventType == 'presentation.source.unavailable' for entry in window.logEntries))
    entry = next(entry for entry in window.logEntries if entry.eventType == 'presentation.source.unavailable')
    assert entry.source == 'runtime' and entry.level == 'WARN' and entry.jobId == 'external-job'
    assert entry.payload['sourceId'] == 'live' and '6220800' in entry.payload['reason']
    assert entry.projectInstanceToken == ''
    window.openLogDialog()
    waitFor(lambda: entry in window.logDialog.view._visibleEntries)
    index = window.logDialog.view._visibleEntries.index(entry)
    window.logDialog.view._showDetails(index, 0)
    assert 'BUDGET_EXCEEDED' in window.logDialog.view.detailViewer.toPlainText()
    assert 'raw image 6220800' in window.logDialog.view.detailViewer.toPlainText()
    preview.stopViewing()
    waitFor(lambda: not preview.active())
    assert preview.logCursors['external-job'] == 1
    preview.watchCurrent()
    waitFor(lambda: preview.hub is not None)
    service.rows['external-job'].append(event('external-job', 2, 'presentation.source.recovered', 'INFO'))
    waitFor(lambda: any(entry.eventType == 'presentation.source.recovered' for entry in window.logEntries))
    assert len([entry for entry in window.logEntries if entry.eventType == 'presentation.source.unavailable']) == 1
    problem = event('external-job', 3, level='ERROR')
    problem.project_id = document.project.projectId
    problem.message = '页面预览通道异常后尚未释放'
    problem.payload_json = json.dumps({'sourceId': 'live', 'resultKey': 'result-3',
        'reason': 'image slab quarantined until actual owner retirement'})
    service.rows['external-job'].append(problem)
    waitFor(lambda: any(entry.level == 'ERROR' and entry.source == 'runtime' for entry in window.logEntries))
    artifactDirectory = os.environ.get('EMO_PRESENTATION_LOG_ARTIFACTS')
    if artifactDirectory:
        from PySide2.QtWidgets import QApplication, QLabel, QSplitter
        output = Path(artifactDirectory)
        output.mkdir(parents=True, exist_ok=True)
        dialog = window.logDialog
        dialog.setWindowTitle('页面预览日志接入验证（模拟数据，无设备连接）')
        dialog.layout().insertWidget(0, QLabel('模拟数据 · 验证页面预览日志接入现有日志窗口 · 无设备连接'))
        dialog.resize(1300, 760)
        dialog.view.sourceFilter.setCurrentText('runtime')
        waitFor(lambda: len(dialog.view._visibleEntries) == 3)
        dialog.view._showDetails(0, 0)
        assert {row.level for row in dialog.view._visibleEntries} == {'INFO', 'WARN', 'ERROR'}
        dialog.findChild(QSplitter).setSizes([230, 350])
        QApplication.processEvents()
        scroll = dialog.view.detailViewer.verticalScrollBar()
        scroll.setValue(scroll.maximum())
        assert dialog.grab().save(str(output / 'designer-log-window.png'))


def testRunCommandJobKeepsItsExistingLogSubscription(designer, logsServer, monkeypatch):
    window, client, document = designer
    service, address = logsServer
    client.capabilities.capabilities.append('presentation_logs')
    client.jobs = [metadata(document, 'owned-job')]
    monkeypatch.setattr(client, 'displayAddress', lambda: address)
    window._runtimeLogJobProjects['owned-job'] = (document.project.projectId, window._runtimeLogProjectToken)
    preview = window.pageCoordinator.preview
    preview.watchCurrent()
    waitFor(lambda: preview.hub is not None)
    assert preview.runtimeLogs is None and service.requests == []
