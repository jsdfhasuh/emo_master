"""Page publication keeps exact viewing identities over the local gRPC endpoint."""
from copy import deepcopy
import json
import sqlite3
from uuid import uuid4

import grpc
import pytest
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.core.project.configuration import projectChanges
from emo_master.core.project.models import ProjectDocument
from tests.runtime.current_production_fixture import currentProductionProject
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_run_view_selection import remoteStart, selectJob
from tests.ui.operator_view.test_view import waitFor


def publish(window, update):
    runtime = window.controller.runtime
    snapshot = runtime.projectConfiguration.snapshot()
    candidate = deepcopy(snapshot['payload'])
    update(candidate)
    paths = [change['path'] for change in projectChanges(snapshot['payload'], candidate)]
    requestId = uuid4().hex
    oldPages = window.pages
    with grpc.insecure_channel(window.controller.address) as channel:
        reply = rpc.RuntimeServiceStub(channel).WriteProjectConfiguration(pb.ProjectConfigurationWriteRequest(
            request_id=requestId, expected_runtime_instance_id=runtime.runtimeInstanceId,
            expected_revision=snapshot['documentRevision'], local_payload_json=json.dumps(candidate),
            selected_paths=paths), timeout=10)
    assert reply.ok, reply.message
    waitFor(lambda: runtime.projectConfiguration.active is None)
    assert runtime.projectConfiguration.status(requestId)['state'] == 'COMMITTED'
    waitFor(lambda: window.pages is not None and window.pages is not oldPages and not window.busy or window.error)
    assert not window.error


def rename(candidate):
    candidate['presentation']['pages']['main']['name'] = 'Published page'


def testLivePublicationPreservesSubscriptionAndRefreshesHistory(qtApp, tmp_path):
    path, _ = currentProductionProject(tmp_path / 'project', mode='continuous', autoStart=True)
    with sqlite3.connect(path.parent / 'history.db') as db:
        db.execute('CREATE TABLE results (value INTEGER)')
        db.execute('INSERT INTO results VALUES (42)')
    window = OperatorWindow(path, dataRoot=tmp_path / 'data', preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.pages is not None and bool(window.pages.displayed) and not window.busy or window.error)
        assert not window.error
        session, job = window.session, window.controller.jobId

        def update(candidate):
            rename(candidate)
            page = candidate['presentation']['pages']['main']
            page['components'][0]['props']['title'] = 'Live inspection'
            page['components'].append(dict(componentId='history', type='sqlite_table', props=dict(sqlite=dict(
                location='runtime', databasePath='history.db', table='results', columns=['value']))))

        publish(window, update)
        waitFor(lambda: bool(window.pages.displayed))
        assert window.session is session and window.hub.session is session
        assert window.controller.jobId == job and window.controller.runtime.jobRepository.get(job).status == 'RUNNING'
        assert len(window.controller.runtime.jobRepository.all()) == 1
        assert window.pages.config.pages['main'].name == 'Published page'
        assert window.pages.widgets['main']['image'][0].props.title == 'Live inspection'
        assert {scope.result.identity.jobId for scope in window.pages.displayed.values()} == {job}
        table = window.pages.widgets['main']['history'][1]
        table.refresh()
        waitFor(lambda: not table.busy and table.table.model().rowCount() == 1)
        assert table.table.model().index(0, 0).data() == '42'
        assert window.pages.captureCoverage.jobId == job
    finally:
        closeWindow(window)


@pytest.mark.parametrize('mode', ['continuous', 'single'])
def testPublicationKeepsExplicitViewAndOriginalStopSelection(qtApp, tmp_path, monkeypatch, mode):
    path, _ = currentProductionProject(tmp_path / 'project', mode=mode, autoStart=True)
    window = OperatorWindow(path, dataRoot=tmp_path / 'data', preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.pages is not None and bool(window.pages.displayed) and not window.busy or window.error)
        assert not window.error
        first = window.controller.jobId
        with grpc.insecure_channel(window.controller.address) as channel:
            second = remoteStart(window, channel)
        waitFor(lambda: any(row['jobId'] == second for row in window.state.get('runCatalog', [])) and not window.busy)
        if mode == 'single':
            waitFor(lambda: window.controller.runtime.jobRepository.get(second).isTerminal
                    and not window.controller.runtime.jobSupervisor.ownsJobResources(second))
        selectJob(monkeypatch, second)
        QTest.mouseClick(window.viewRunsButton, Qt.LeftButton)
        waitFor(lambda: window.viewSession is not None and not window.busy or window.error)
        assert not window.error
        waitFor(lambda: bool(window.pages.displayed))
        session = window.viewSession
        stopTarget = window.controller.sessions['main'].jobId
        publish(window, rename)
        waitFor(lambda: bool(window.pages.displayed))
        assert window.viewSession is session and session.jobId == second
        assert window.controller.sessions['main'].jobId == stopTarget
        if mode == 'continuous':
            assert stopTarget == first
        assert {scope.result.identity.jobId for scope in window.pages.displayed.values()} == {second}
        assert window.pages.config.pages['main'].name == 'Published page'
        assert window.viewCoverage.jobId == second and len(window.controller.runtime.jobRepository.all()) == 2
    finally:
        closeWindow(window)


def testDeletingViewedCompletedWorkflowReturnsToRemainingPage(qtApp, tmp_path, monkeypatch):
    path, document = currentProductionProject(tmp_path / 'project', autoStart=True)
    payload = document.model_dump(mode='json')
    payload['workflows']['remaining'] = deepcopy(payload['workflows']['main'])
    payload['workflowOrder'].append('remaining')
    payload['entryWorkflowId'] = 'remaining'
    payload['runtime']['startupWorkflowIds'] = ['remaining']
    presentation = payload['presentation']
    presentation['resultScopes']['remaining-root'] = dict(entryWorkflowId='remaining', scopeWorkflowId='remaining')
    presentation['dataSources']['remaining-image'] = dict(kind='node_output', resultScopeId='remaining-root',
        workflowId='remaining', nodeId='load', port='image', expectedType='image')
    presentation['pages']['remaining'] = dict(name='Remaining workflow', components=[
        dict(componentId='remaining-image', type='image', bindings={'image': 'remaining-image'})])
    presentation['pageOrder'].append('remaining')
    saveProject(path, ProjectDocument.model_validate(payload).model_dump(mode='json'))
    window = OperatorWindow(path, dataRoot=tmp_path / 'data', preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.pages is not None and bool(window.pages.displayed) and not window.busy or window.error)
        assert not window.error
        waitFor(lambda: window.state['canLoad'] and not window.busy)
        remainingJob = window.controller.sessions['remaining'].jobId
        with grpc.insecure_channel(window.controller.address) as channel:
            viewedJob = remoteStart(window, channel, 'main')
        waitFor(lambda: window.controller.runtime.jobRepository.get(viewedJob).isTerminal
                and not window.controller.runtime.jobSupervisor.ownsJobResources(viewedJob))
        waitFor(lambda: any(row['jobId'] == viewedJob for row in window.state.get('runCatalog', [])) and not window.busy)
        selectJob(monkeypatch, viewedJob)
        QTest.mouseClick(window.viewRunsButton, Qt.LeftButton)
        waitFor(lambda: window.viewSession is not None and not window.busy or window.error)
        assert not window.error
        waitFor(lambda: bool(window.pages.displayed))
        oldSession = window.viewSession
        calls = []
        for name in ('StartJob', 'StopJob'):
            original = getattr(window.controller.runtime, name)
            def record(request, context, name=name, original=original):
                calls.append(name)
                return original(request, context)
            monkeypatch.setattr(window.controller.runtime, name, record)

        def remove(candidate):
            del candidate['workflows']['main']
            candidate['workflowOrder'].remove('main')
            page = candidate['presentation']
            del page['resultScopes']['root']
            del page['dataSources']['image']
            del page['pages']['main']
            page['pageOrder'].remove('main')
            page['defaultPageId'] = 'remaining'

        publish(window, remove)
        assert window.viewSession is None and window.viewRunInfo is None
        assert oldSession.stop.is_set()
        assert window.pages.config.pageOrder == ['remaining'] and window.pages.isVisible()
        assert not window.returnRunButton.isVisible()
        assert '已从工程删除' in window.viewingLabel.text() and window.viewingLabel.isVisible()
        assert window.controller.sessions['remaining'].jobId == remainingJob
        assert window.selectedWorkflowIds == ['remaining'] and window.startButton.isEnabled()
        assert not calls and len(window.controller.runtime.jobRepository.all()) == 2
    finally:
        closeWindow(window)
