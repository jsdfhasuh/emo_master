"""Native Qt selection over actual local RPC and simulated/local-image runs."""
from uuid import uuid4

import grpc
import pytest
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QInputDialog

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.clients.runtime.dispatch_display import DispatchDisplaySession
from emo_master.core.project.models import ProjectDocument
from tests.runtime.current_production_fixture import currentProductionProject
from tests.runtime.dispatch_page_fixture import sharedPageProject
from tests.runtime.test_dispatch_queue_integration import gateConsumers
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_view import waitFor


def selectJob(monkeypatch, job):
    def choose(_parent, _title, _label, items, _current, _editable):
        return next(value for value in items if job in value), True
    monkeypatch.setattr(QInputDialog, 'getItem', choose)


def remoteStart(window, channel, workflow='main'):
    result = rpc.RuntimeServiceStub(channel).StartJob(pb.StartJobRequest(
        project_id=window.controller.document.project.projectId, workflow_id=workflow,
        start_request_id=uuid4().hex, expected_runtime_instance_id=window.controller.runtime.runtimeInstanceId,
        capture_presentation=True, legacy_snapshot_policy='NONE'), timeout=10)
    assert result.ok, result.message
    return result.job_id


def testViewingConcurrentRemoteRunKeepsExplicitStopSelection(qtApp, tmp_path, monkeypatch):
    path, _ = currentProductionProject(tmp_path / 'project', mode='continuous')
    window = OperatorWindow(path, dataRoot=tmp_path / 'data', preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.pages is not None and not window.busy)
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        waitFor(lambda: window.state.get('jobStatus') == 'RUNNING' and not window.busy)
        first = window.controller.jobId
        originalSession = window.session
        with grpc.insecure_channel(window.controller.address) as channel:
            second = remoteStart(window, channel)
            waitFor(lambda: len(window.state.get('activeJobIds', [])) == 2 and not window.busy)
            selectJob(monkeypatch, second)
            QTest.mouseClick(window.viewRunsButton, Qt.LeftButton)
            waitFor(lambda: window.viewSession is not None and not window.busy or window.error)
            assert not window.error
            waitFor(lambda: bool(window.pages.displayed))
            assert {scope.result.identity.jobId for scope in window.pages.displayed.values()} == {second}
            assert window.viewSession.jobId == second and window.session is originalSession
            assert window.controller.sessions['main'].jobId == first and window.state['jobId'] == first
            assert second in window.viewingLabel.text() and window.returnRunButton.isVisible()
            defaults = []
            def stopSelected(_parent, title, _label, items, current, _editable):
                assert title == '停止运行'
                defaults.append(items[current])
                return items[current], True
            monkeypatch.setattr(QInputDialog, 'getItem', stopSelected)
            QTest.mouseClick(window.stopButton, Qt.LeftButton)
            waitFor(lambda: not window.busy and window.state.get('jobId') == second)
            assert defaults and first in defaults[0]
            assert window.controller.runtime.jobRepository.get(first).isTerminal
            assert window.controller.runtime.jobRepository.get(second).status == 'RUNNING'
            assert window.viewSession.jobId == second
            QTest.mouseClick(window.returnRunButton, Qt.LeftButton)
            waitFor(lambda: not window.busy and window.viewSession is None)
            assert window.controller.runtime.jobRepository.get(second).status == 'RUNNING'
            QTest.mouseClick(window.stopButton, Qt.LeftButton)
            waitFor(lambda: not window.busy and window.state['canLoad'])
            third = remoteStart(window, channel)
            waitFor(lambda: not window.busy and window.state.get('jobId') == third)
            assert window.state['state'] == 'RUNNING' and not window.state['canLoad']
    finally:
        closeWindow(window)


def testCompletedNonStartupRunHasIndependentVisiblePage(qtApp, tmp_path, monkeypatch):
    path, document = currentProductionProject(tmp_path / 'project')
    payload = document.model_dump(mode='json')
    from copy import deepcopy
    payload['workflows']['auxiliary'] = deepcopy(payload['workflows']['main'])
    payload['workflowOrder'].append('auxiliary')
    payload['presentation']['resultScopes']['aux-root'] = dict(entryWorkflowId='auxiliary', scopeWorkflowId='auxiliary')
    payload['presentation']['dataSources']['aux-image'] = dict(kind='node_output', resultScopeId='aux-root',
        workflowId='auxiliary', nodeId='load', port='image', expectedType='image')
    payload['presentation']['pages']['auxiliary'] = dict(name='Auxiliary results', components=[
        dict(componentId='aux-image', type='image', bindings={'image': 'aux-image'})])
    payload['presentation']['pageOrder'].append('auxiliary')
    saveProject(path, ProjectDocument.model_validate(payload).model_dump(mode='json'))
    window = OperatorWindow(path, dataRoot=tmp_path / 'data', preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.pages is not None and not window.busy)
        window.timer.stop()  # Complete between two UI status observations.
        with grpc.insecure_channel(window.controller.address) as channel:
            job = remoteStart(window, channel, 'auxiliary')
            waitFor(lambda: window.controller.runtime.jobRepository.get(job).isTerminal
                and not window.controller.runtime.jobSupervisor.ownsJobResources(job))
        window.timer.start()
        waitFor(lambda: any(row['jobId'] == job for row in window.state.get('runCatalog', [])) and not window.busy)
        assert set(window.controller.sessions) == {'main'} and not window.controller.jobId
        selectJob(monkeypatch, job)
        QTest.mouseClick(window.viewRunsButton, Qt.LeftButton)
        waitFor(lambda: window.viewSession is not None and not window.busy or window.error)
        assert not window.error
        waitFor(lambda: bool(window.pages.displayed))
        assert window.pages.config.pageOrder == ['auxiliary']
        assert {scope.result.identity.jobId for scope in window.pages.displayed.values()} == {job}
        assert window.pages.isVisible() and set(window.controller.sessions) == {'main'}
        assert not window.controller.jobId and len(window.controller.runtime.jobRepository.all()) == 1
    finally:
        window.timer.start()
        closeWindow(window)


@pytest.mark.parametrize('target', ['desktop', 'web'])
def testNormalSharedDispatchCaptureAndExplicitConsumerView(qtApp, tmp_path, monkeypatch, target):
    first, rest = gateConsumers(monkeypatch)
    root = tmp_path / 'project'
    root.mkdir()
    document = sharedPageProject(root)
    document.presentation.pages['consumer'].targets = [target]
    path = root / 'dispatch.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    window = OperatorWindow(path, dataRoot=tmp_path / 'data', preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.pages is not None and not window.busy or window.error)
        assert not window.error
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        waitFor(lambda: not window.busy and bool(window.state.get('jobId')) or window.error)
        assert not window.error
        producer = window.controller.jobId
        group = window.controller.runtime.deviceManager.dispatchGroups[producer]
        consumer = group.slots['detection'].jobId
        captures = window.controller.presentation.jobs
        assert captures[producer]['capture'] and captures[consumer]['capture']
        waitFor(lambda: any(row['jobId'] == consumer for row in window.state.get('runCatalog', [])) and not window.busy)
        selectJob(monkeypatch, consumer)
        QTest.mouseClick(window.viewRunsButton, Qt.LeftButton)
        waitFor(lambda: window.viewSession is not None and not window.busy or window.error)
        assert not window.error and isinstance(window.viewSession, DispatchDisplaySession)
        assert set(window.viewSession.sessions) == {producer, consumer}
        assert window.controller.sessions['main'].jobId == producer and 'consumer' not in window.controller.sessions
        if target == 'desktop':
            waitFor(lambda: not window.pages.widgets['consumer']['live'][1].image.isNull())
            assert window.pages.widgets['consumer']['image'][1].image.isNull()
            first.set()
            waitFor(lambda: not window.pages.widgets['consumer']['image'][1].image.isNull())
            assert set(window.pages.displayed) == {'acquisition', 'consumption'}
        else:
            assert window.pages.config.pageOrder == []
            assert {key for member in window.viewSession.coverage.members for key in member.sources} == {'live', 'image', 'count'}
        assert len(window.controller.runtime.jobRepository.all()) == 2
    finally:
        first.set()
        rest.set()
        closeWindow(window)
