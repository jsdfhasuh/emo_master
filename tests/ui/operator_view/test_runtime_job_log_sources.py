"""Operator source navigation and export against the real Runtime log query."""
import json
from threading import Event

import grpc
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QFileDialog, QPushButton, QTreeWidgetItemIterator

from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from tests.runtime.current_production_fixture import currentProductionProject
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_run_view_selection import remoteStart
from tests.ui.operator_view.test_view import waitFor


def clickTab(view, index):
    QTest.mouseClick(view.sources, Qt.LeftButton, pos=view.sources.tabRect(index).center())
    assert view.sources.currentIndex() == index


def openCatalogJob(jobs, jobId):
    iterator = QTreeWidgetItemIterator(jobs.directory)
    while iterator.value() is not None:
        item = iterator.value()
        if getattr(item.data(0, Qt.UserRole), 'job_id', None) == jobId:
            jobs.directory.setCurrentItem(item)
            break
        iterator += 1
    assert jobs.openButton.isEnabled()
    QTest.mouseClick(jobs.openButton, Qt.LeftButton)
    waitFor(lambda: jobs.job is not None and jobs.job.job_id == jobId and not jobs._opening)


def testOperatorRuntimeSourcesAndExactExportScopes(qtApp, tmp_path, monkeypatch):
    path, document = currentProductionProject(tmp_path / 'project')
    window = OperatorWindow(path, dataRoot=tmp_path / 'data', preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.error or (window.pages is not None and not window.busy), seconds=60)
        assert not window.error
        service = window.controller.runtime
        projectId = document.project.projectId

        def runsFinished(jobId):
            return service.jobRepository.get(jobId).isTerminal and not service.jobSupervisor.ownsJobResources(jobId)

        with grpc.insecure_channel(window.controller.address) as channel:
            first = remoteStart(window, channel)
            waitFor(lambda: runsFinished(first), seconds=60)
            second = remoteStart(window, channel)
            waitFor(lambda: runsFinished(second), seconds=60)
        assert service.jobRepository.get(first).status == service.jobRepository.get(second).status == 'COMPLETED'

        def append(partition, label, level='WARN', **payload):
            service.eventStore.append(partition, 'node.log', label, level=level, projectId=projectId,
                                      workflowId='main', payload=payload)

        append(first, 'r3-window-excluded')
        for index in range(205):
            append('__runtime__', f'r3-owned-{index:03d}', jobId=first)
        append('__runtime__', 'r3-system-only')
        append(first, 'r3-current-info', 'INFO')
        append(first, 'r3-current-error', 'ERROR')
        append(second, 'r3-other-job')

        scope = dict(runtime_instance_id=service.runtimeInstanceId, project_id=projectId)

        def rows(**options):
            return service.QueryRuntimeLogs(pb.QueryRuntimeLogsRequest(**scope, limit=1000, **options), None).events

        ids = {row.event.message: row.event_id for row in rows()}
        allFirst = {row.event_id for row in rows(job_id=first)}
        assert ids['r3-owned-204'] in allFirst and ids['r3-system-only'] not in allFirst

        window.showLogs()
        logs = window.logView
        assert logs.sources.count() == 3 and logs.sources.tabText(2) == '本机 JSONL'
        queryService = service.QueryRuntimeLogs
        systemPages = []

        def observeSystem(request, context):
            reply = queryService(request, context)
            if not request.job_id and list(request.levels) == ['WARN']:
                systemPages.append((request.after_event_id, reply))
            return reply

        monkeypatch.setattr(service, 'QueryRuntimeLogs', observeSystem)
        clickTab(logs, 1)
        system = logs.systemLogs
        system.level.setCurrentIndex(system.level.findData('WARN'))
        waitFor(lambda: system.pending is None and any(row.eventId == ids['r3-system-only'] for row in system.rows))
        assert not allFirst.intersection(row.eventId for row in system.rows)
        assert system.table.item(0, 0) is not None
        assert systemPages[0][1].has_more and all(row.event_id in allFirst for row in systemPages[0][1].events)
        assert systemPages[1][0] == systemPages[0][1].next_event_id

        clickTab(logs, 0)
        jobs = logs.jobLogs
        waitFor(lambda: any(row.job_id == first for row in jobs.recent))
        assert jobs.stopButton is None
        registered = next(row for row in service.ListOperators(pb.ListOperatorsRequest(), None).operators
                          if row.operator_id == 'vision.io.image_loader')
        description = jobs.detail.operatorLookup(registered.operator_id, registered.version)
        assert description['paramSchema'] == json.loads(registered.param_schema_json)
        assert not jobs.detail.operatorLookup(registered.operator_id, 'version-not-registered')
        openCatalogJob(jobs, first)
        assert first[:8] in jobs.jobTitle.text() and jobs.jobTitle.toolTip() == first
        loaded = {entry.eventId: entry for entry in jobs.window.entries}
        assert {ids[label] for label in ('r3-owned-204', 'r3-current-info', 'r3-current-error')} <= loaded.keys()
        assert all(ids[label] not in loaded for label in ('r3-system-only', 'r3-window-excluded', 'r3-other-job'))
        QTest.mouseClick(jobs.filterToggle, Qt.LeftButton)
        warn = jobs.level.findText('WARN')
        jobs.level.setCurrentIndex(warn)
        matching = {eventId for eventId, entry in loaded.items() if entry.level == 'WARN'}

        filteredPath = tmp_path / 'current-filter.json'

        def chooseFilteredPath(*_):
            jobs.level.setCurrentIndex(0)
            return str(filteredPath), 'JSON (*.json)'

        monkeypatch.setattr(QFileDialog, 'getSaveFileName', chooseFilteredPath)
        jobs.exportFilteredAction.trigger()
        waitFor(lambda: filteredPath.is_file() and logs.runtimeLogs.export is None)
        filtered = json.loads(filteredPath.read_text(encoding='utf-8'))
        assert filtered['scope'] == 'filtered_job' and filtered['filters']['jobId'] == first
        assert {entry['eventId'] for entry in filtered['entries']} == matching
        assert filtered['entryCount'] == len(matching)
        jobs.level.setCurrentIndex(warn)

        query = logs.runtimeLogs.client.queryRuntimeLogs
        pages = []
        firstPageReady, releasePage = Event(), Event()

        def queryAndAppend(**options):
            reply = query(**options)
            if options.get('jobId') == first and not options.get('latestFirst'):
                pages.append((options, reply))
                if len(pages) == 1:
                    append(first, 'r3-after-export-bound')
                    firstPageReady.set()
                    assert releasePage.wait(15)
            return reply

        monkeypatch.setattr(logs.runtimeLogs.client, 'queryRuntimeLogs', queryAndAppend)
        wholePath = tmp_path / 'whole-job.json'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_: (str(wholePath), 'JSON (*.json)'))
        jobs.exportWholeAction.trigger()
        try:
            waitFor(firstPageReady.is_set)
            returnToJobs = next(button for button in jobs.jobPage.findChildren(QPushButton)
                                if button.text() == '返回运行任务')
            QTest.mouseClick(returnToJobs, Qt.LeftButton)
            openCatalogJob(jobs, second)
            assert jobs.exportCancel.isVisible() and not jobs.exportButton.isEnabled()
        finally:
            releasePage.set()
        waitFor(lambda: wholePath.is_file() and logs.runtimeLogs.export is None)
        whole = json.loads(wholePath.read_text(encoding='utf-8'))
        late = next(row.event_id for row in rows(job_id=first) if row.event.message == 'r3-after-export-bound')
        bound = whole['filters']['upperEventId']
        assert whole['scope'] == 'whole_job' and whole['filters']['jobId'] == first
        assert {entry['eventId'] for entry in whole['entries']} == allFirst
        assert whole['entryCount'] == len(allFirst)
        assert {ids[label] for label in ('r3-window-excluded', 'r3-current-info', 'r3-current-error')} <= allFirst - matching
        assert ids['r3-other-job'] not in allFirst | matching and late > bound and late not in allFirst
        assert len(pages) >= 2 and pages[0][1]['hasMore']
        assert all(options['upperEventId'] == bound for options, _reply in pages[1:])
        print(f'Job {first}; other {second}; system {ids["r3-system-only"]}; owned {ids["r3-owned-204"]}; '
              f'filtered {len(matching)}; whole {len(allFirst)}; upper {bound}; excluded late {late}')
    finally:
        closeWindow(window)


def testCloseCancelsInFlightSystemQueryAndWaitsForItsRetirement(qtApp, tmp_path, monkeypatch):
    window = OperatorWindow(dataRoot=tmp_path / 'data', preferences=preferences(tmp_path))
    window.show()
    entered, release = Event(), Event()
    calls = []
    try:
        waitFor(lambda: window.error or (window.controller is not None and not window.busy), seconds=60)
        assert not window.error
        service = window.controller.runtime
        query = service.QueryRuntimeLogs

        def inFlight(request, context):
            reply = query(request, context)
            calls.append(context)
            entered.set()
            assert release.wait(10)
            return reply

        monkeypatch.setattr(service, 'QueryRuntimeLogs', inFlight)
        window.showLogs()
        clickTab(window.logView, 1)
        waitFor(entered.is_set)
        window.close()
        waitFor(lambda: getattr(window, '_closeOperationDone', False))
        assert not calls[0].is_active() and window.logView.runtimeLogs.client is None
        assert not window.logView.idle and not window.done
        release.set()
        waitFor(lambda: window.done)
        assert window.logView.idle and not window.isVisible()
    finally:
        release.set()
        closeWindow(window)
