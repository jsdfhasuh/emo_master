"""Operator source navigation and export against the real Runtime log query."""
import json
from threading import Event, current_thread

import grpc
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QFileDialog, QPushButton, QTreeWidgetItemIterator

from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.clients.runtime.global_logs import RuntimeGlobalLogs
from tests.runtime.current_production_fixture import currentProductionProject
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_run_view_selection import remoteStart
from tests.ui.operator_view.test_runtime_log_view import waitMs
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

        logs = window.logView
        assert logs.sources.count() == 3 and logs.sources.currentIndex() == 0
        window.showSettings()
        window.startupPage.ensureWidgetVisible(window.startupPage.allLogsButton)
        QTest.mouseClick(window.startupPage.allLogsButton, Qt.LeftButton)
        assert window.contentStack.currentWidget() is logs and logs.sources.currentIndex() == 0
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
        QTest.mouseClick(jobs.catalogHistory, Qt.LeftButton)
        jobs.catalogProject.setText(projectId)
        jobs.catalogStatus.setCurrentIndex(jobs.catalogStatus.findData('FAILED'))
        jobs.catalogDateRange.startControl.setChecked(True)
        refresh = next(button for button in jobs.catalog.findChildren(QPushButton) if button.text() == '刷新')
        QTest.mouseClick(refresh, Qt.LeftButton)
        waitFor(lambda: jobs.historyScope.get('statuses') == ('FAILED',))
        assert jobs.directory.topLevelItem(0).child(0).text(0) == '暂无任务'

        window.showSettings()
        window.startupPage.ensureWidgetVisible(window.startupPage.allLogsButton)
        QTest.mouseClick(window.startupPage.allLogsButton, Qt.LeftButton)
        assert jobs.catalog.isVisible() and jobs.catalogTitle.text() == '运行任务'
        assert not jobs.catalogProject.text() and not jobs.catalogStatus.currentData()
        assert jobs.catalogDateRange.bounds() == (None, None)
        assert jobs.directory.topLevelItem(1).text(0) == '最近已结束'
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
        QTest.mouseClick(jobs.clearFilters, Qt.LeftButton)
        assert jobs.level.currentIndex() == 0 and logs.sources.currentIndex() == 0

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
            window.showSettings()
            waitFor(lambda: logs.runtimeLogs.reader is None)
            assert logs.runtimeLogs.export[1].is_active() and not logs.runtimeLogs.export[2].is_set()
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


def testJobReaderStopsAndResumesFromDeliveredCursor(qtApp, tmp_path, monkeypatch):
    path, document = currentProductionProject(tmp_path / 'project')
    window = OperatorWindow(path, dataRoot=tmp_path / 'data', preferences=preferences(tmp_path))
    window.show()
    reached, release = Event(), Event()
    bootstrapReady, releaseBootstrap = Event(), Event()
    try:
        waitFor(lambda: window.error or (window.pages is not None and not window.busy), seconds=60)
        assert not window.error
        service = window.controller.runtime
        with grpc.insecure_channel(window.controller.address) as channel:
            job = remoteStart(window, channel)
            waitFor(lambda: service.jobRepository.get(job).isTerminal
                    and not service.jobSupervisor.ownsJobResources(job), seconds=60)
        assert window.contentStack.currentWidget() is window.businessHost
        query, publish = service.QueryRuntimeLogs, RuntimeGlobalLogs._publish
        calls, bootstrapUpper = [], []

        def observeQuery(request, context):
            reply = query(request, context)
            if current_thread().name == 'runtime-global-logs':
                calls.append((request.runtime_instance_id, request.after_event_id))
                if len(calls) == 1:
                    bootstrapUpper.append(reply.upper_event_id)
                    bootstrapReady.set()
                    assert releaseBootstrap.wait(10)
            return reply

        def append(label):
            service.eventStore.append(job, 'node.log', label, projectId=document.project.projectId, workflowId='main')

        monkeypatch.setattr(service, 'QueryRuntimeLogs', observeQuery)
        logs = window.logView
        host, jobs = logs.runtimeLogs, logs.jobLogs
        window.showCommunication()
        dialog = window.communicationDialog
        dialog.timer.stop()
        dialog._receiveState(document.project.projectId, ({}, service.runtimeInstanceId, {
            'software-device': {'name': '模拟验收设备', 'deviceTypeId': 'camera', 'projectLoadGeneration': 1},
        }))
        dialog.deviceList.setCurrentItem(dialog.deviceItems['software-device'])
        dialog.diagnostics.setPage({'deviceId': 'software-device'})
        QTest.mouseClick(dialog.diagnostics.runtimeLogsButton, Qt.LeftButton)
        assert logs.sources.currentIndex() == 2 and logs.deviceId == 'software-device' and not dialog.isVisible()
        assert host.reader is None and not calls

        jobs.search.setText('过期的 Job 筛选')
        logs.levelCombo.setCurrentIndex(1)
        window.showSettings()
        window.startupPage.ensureWidgetVisible(window.startupPage.allLogsButton)
        QTest.mouseClick(window.startupPage.allLogsButton, Qt.LeftButton)
        assert logs.sources.currentIndex() == 0 and not jobs.search.text()
        assert logs.levelCombo.currentIndex() == 0 and not logs.deviceId
        waitFor(bootstrapReady.is_set)
        clickTab(logs, 2)
        append('closeout-created-during-bootstrap-pause')
        releaseBootstrap.set()
        waitFor(lambda: host.reader is None)
        assert host._delivery == (service.runtimeInstanceId, bootstrapUpper[0])
        clickTab(logs, 0)
        waitFor(lambda: host._delivery[1] > bootstrapUpper[0] and any(row.job_id == job for row in jobs.recent))
        assert calls[1] == (service.runtimeInstanceId, bootstrapUpper[0])
        openCatalogJob(jobs, job)
        before = jobs.window
        jobs.mode.setCurrentIndex(1)
        confirmed = host._delivery

        def holdProgress(reader, kind, value):
            if kind == 'progress' and value[1] > confirmed[1] and not reached.is_set():
                reached.set()
                assert release.wait(10)
            return publish(reader, kind, value)

        monkeypatch.setattr(RuntimeGlobalLogs, '_publish', holdProgress)
        append('closeout-delivered-before-pause')
        waitFor(reached.is_set)
        waitFor(lambda: any(row.message == 'closeout-delivered-before-pause' for row in before.entries))
        entry = next(row for row in before.entries if row.message == 'closeout-delivered-before-pause')
        key = ('event', entry.eventId)
        waitFor(lambda: key in jobs._items)
        jobs.events.setCurrentItem(jobs._items[key])
        assert jobs._detailEntry.eventId == entry.eventId

        first = host.reader
        clickTab(logs, 1)
        assert first.stop.is_set() and not first.context.is_active()
        clickTab(logs, 0)
        waitMs(100)
        assert host.reader is first and first.thread.is_alive()
        assert host._delivery == confirmed
        append('closeout-created-during-retirement')
        nextCall = len(calls)
        release.set()
        waitFor(lambda: host.reader is not first and len(calls) > nextCall and before.newCount == 1)
        assert not first.thread.is_alive() and calls[nextCall] == confirmed
        assert jobs.window is before and jobs._detailEntry.eventId == entry.eventId
        assert sum(row.eventId == entry.eventId for row in before.entries) == 1

        clickTab(logs, 2)
        waitFor(lambda: host.reader is None and not host.timer.isActive())
        stoppedCalls = len(calls)
        append('closeout-created-in-jsonl')
        waitMs(550)
        assert len(calls) == stoppedCalls
        clickTab(logs, 0)
        waitFor(lambda: before.newCount == 2)

        QTest.mouseClick(window.backButton, Qt.LeftButton)
        assert window.contentStack.currentWidget() is window.businessHost
        waitFor(lambda: host.reader is None and not host.timer.isActive())
        stoppedCalls = len(calls)
        append('closeout-created-in-production')
        waitMs(550)
        assert len(calls) == stoppedCalls
        QTest.mouseClick(window.logsButton, Qt.LeftButton)
        waitFor(lambda: before.newCount == 3)
        assert logs.sources.currentIndex() == 0 and jobs.window is before and jobs._detailEntry.eventId == entry.eventId
        latest = next(button for button in jobs.jobPage.findChildren(QPushButton) if button.text() == '返回最新')
        QTest.mouseClick(latest, Qt.LeftButton)
        waitFor(lambda: not jobs._opening)
        assert [row.message for row in jobs.window.entries if row.message.startswith('closeout-')] == [
            'closeout-created-during-bootstrap-pause', 'closeout-delivered-before-pause', 'closeout-created-during-retirement',
            'closeout-created-in-jsonl', 'closeout-created-in-production',
        ]
    finally:
        releaseBootstrap.set()
        release.set()
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
            if current_thread().name != 'runtime-global-logs':
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
