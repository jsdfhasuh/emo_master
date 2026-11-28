import json
import os
from contextlib import nullcontext
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import grpc
import pytest
from PySide2.QtCore import QRect, QSettings, QTimer, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QInputDialog

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.core.project.delivery_store import DirectoryOwner
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
from emo_master.core.presentation.models import Action
from tests.runtime.current_production_fixture import currentProductionProject
from tests.runtime.production_fixture import productionProject, saveDocument
from tests.ui.operator_view.test_view import waitFor


def preferences(tmp_path):
    return QSettings(str(tmp_path / "preferences.ini"), QSettings.IniFormat)


def testOnlinePagePublicationRetiresOldDisplayAndDoesNotRebindOldResult(qtApp, tmp_path):
    from copy import deepcopy
    from emo_master.core.project.migration import migrateProjectPayload
    from emo_master.core.project.models import ProjectDocument
    root = tmp_path / "project"
    document = productionProject(root, mode="single", autoStart=True, save=False)
    saveDocument(root, ProjectDocument.model_validate(migrateProjectPayload(document.model_dump(), enableDevices=True)))
    window = OperatorWindow(root, dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.pages is not None and bool(window.pages.displayed) or window.error)
        assert not window.error
        waitFor(lambda: not window.busy and window.state["canStart"])
        oldPages = window.pages
        assert window.session is not None
        coordinator = window.controller.runtime.projectConfiguration
        candidate = deepcopy(coordinator.snapshot()["payload"])
        candidate["presentation"]["pages"]["main"]["name"] = "Published"
        coordinator.start(dict(requestId="page-publication", expectedRuntimeInstanceId=window.controller.runtime.runtimeInstanceId,
            expectedRevision=1, localPayload=candidate, selectedPaths=["/presentation/pages/main/name"]))
        waitFor(lambda: coordinator.active is None)
        assert coordinator.status("page-publication")["state"] == "COMMITTED"
        waitFor(lambda: window.pages is not None and window.pages is not oldPages and not window.busy)
        assert window.session is None and window.hub is None
        assert not window.pages.displayed
        assert window.controller.presentationForWorkflow("main").pages["main"].name == "Published"
    finally:
        closeWindow(window)


def closeWindow(window):
    if not window.done:
        window.close()
        waitFor(lambda: window.done)


def testOperatorOnlyProjectPagesContinuousAutoStartStopRestartAndClose(qtApp, tmp_path):
    root = tmp_path / "project"
    productionProject(root, autoStart=True)
    prefs = preferences(tmp_path)
    window = OperatorWindow(root, dataRoot=tmp_path / "data", preferences=prefs)
    window.show()
    try:
        waitFor(lambda: window.hub is not None or window.error)
        assert not window.error
        waitFor(lambda: bool(window.pages.displayed))
        waitFor(lambda: not window.busy and window.state["state"] == "RUNNING")
        first = window.controller.jobId
        assert not window.pages.editing and not window.pages.editorHost and not window.pages.designExamples
        assert len(window.controller.runtime.jobRepository.all()) == 1
        assert not window.openAction.isEnabled() and window.stopButton.isEnabled()
        result = next(iter(window.pages.displayed.values())).result
        assert result.identity.mode == "runtime" and len(result.sources) == 2
        assert next(source.valueJson for source in result.sources if source.sourceId == "count") == "2"
        QTest.mouseClick(window.stopButton, Qt.LeftButton)
        waitFor(lambda: not window.busy and window.state["canStart"])
        assert not window.controller.runtime.jobSupervisor.ownsJobResources(first)
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        waitFor(lambda: window.hub is not None and window.controller.jobId != first or window.error)
        assert not window.error
        waitFor(lambda: bool(window.pages.displayed))
        assert len(window.controller.presentation.jobs) == 1
        assert len(window.controller.runtime.jobRepository.all()) == 2
        assert prefs.value("projectPath") == str(root / "project.json")
        window.grab().save(str(tmp_path / "operator-runtime.png"))
    finally:
        closeWindow(window)
    assert window.controller.closed and window.controller.runtime._closed
    assert window.session is None


@pytest.mark.parametrize('showControls', [True, False])
def testPagedToolDockPreservesAppearanceAndStopsOriginalJobOnce(qtApp, tmp_path, monkeypatch, showControls):
    path, document = currentProductionProject(tmp_path / 'project', mode='continuous', autoStart=True)
    payload = document.model_dump(mode='json')
    payload['project']['name'] = '产线长项目名称 ' * 12
    label = '本项目配置的较长停止按钮'
    payload['presentation']['shell'] = {
        'showControls': showControls,
        'startControl': {'label': '本项目开始'},
        'stopControl': {'label': label, 'appearance': {
            'minWidth': 290, 'showIcon': True, 'iconPosition': 'right', 'iconSize': 26,
            'customStyle': {'qtQss': {'enabled': True,
                'code': 'QPushButton { min-width:290px; max-width:290px; background:#9B2340; }'}}}},
    }
    saveProject(path, ProjectDocument.model_validate(payload).model_dump(mode='json'))
    window = OperatorWindow(path, dataRoot=tmp_path / 'data', preferences=preferences(tmp_path))
    window.resize(650, 550)
    window.show()
    blocked, release = threading.Event(), threading.Event()
    calls = []
    originalStop = ProductionRuntime.stop

    def stop(runtime, *args, jobId=None):
        calls.append(jobId)
        blocked.set()
        assert release.wait(10)
        return originalStop(runtime, *args, jobId=jobId)

    def unexpectedDialog(*_args):
        pytest.fail('单活动 Job 应直接停止')

    monkeypatch.setattr(ProductionRuntime, 'stop', stop)
    monkeypatch.setattr(QInputDialog, 'getItem', unexpectedDialog)
    try:
        waitFor(lambda: window.pages is not None and not window.busy and window.state['state'] == 'RUNNING')
        stopButton, startButton = window.stopButton, window.startButton
        job, pages, session, hub = window.controller.jobId, window.pages, window.session, window.hub
        assert not pages.diagnostics.isVisible() and not window.contextBanner.isVisible()
        assert stopButton.isVisible() is showControls and startButton.isVisible() is showControls
        assert pages.hostControls.indexOf(stopButton) >= 0 and stopButton.text() == label
        originalStyle = stopButton.styleSheet()
        waitFor(lambda: bool(pages.displayed))
        pages.act(Action(type='navigate', pageId='main', context='displayed_result', resultScopeId='root'))
        pin = pages.frozen
        assert pin is not None and hub.pins[pages] == pin
        history = pages.historyReviewState()
        QTest.mouseClick(window.logsButton, Qt.LeftButton)
        QTest.mouseClick(window.settingsButton, Qt.LeftButton)
        assert window.contentStack.currentWidget() is window.startupPage
        assert window.hostControls.indexOf(stopButton) >= 0 and stopButton.isVisible() and stopButton.isEnabled()
        assert startButton.isVisible() is showControls
        assert stopButton.text() == '停止' and '#9B2340' not in stopButton.styleSheet()
        assert stopButton.maximumWidth() > 290 and stopButton.iconSize().width() != 26
        for button in (window.backButton, stopButton):
            assert window.rect().contains(button.mapTo(window, button.rect().bottomRight()))
        assert payload['project']['name'] in window.projectLabel.toolTip()
        QTest.mouseClick(window.backButton, Qt.LeftButton)
        assert window.contentStack.currentWidget() is window.logView
        QTest.mouseClick(window.backButton, Qt.LeftButton)
        assert window.contentStack.currentWidget() is window.businessHost
        assert (window.controller.jobId, window.pages, window.session, window.hub) == (job, pages, session, hub)
        assert pages.historyReviewState() == history and not session.stop.is_set()
        assert pages.frozen == pin and hub.pins[pages] == pin
        assert pages.hostControls.indexOf(stopButton) >= 0 and stopButton.styleSheet() == originalStyle
        assert stopButton.text() == label and stopButton.iconSize().width() == 26
        assert stopButton.isVisible() is showControls
        QTest.mouseClick(window.logsButton, Qt.LeftButton)
        waitFor(lambda: not window.busy and stopButton.isEnabled())
        QTest.mouseClick(stopButton, Qt.LeftButton)
        waitFor(blocked.is_set)
        assert stopButton.isVisible() and not stopButton.isEnabled()
        assert window.contextBanner.isVisible() and window.progress.isVisible()
        if not showControls:
            window.close()
            assert window.closing and stopButton.isVisible() and not stopButton.isEnabled()
        release.set()
        waitFor(lambda: window.done or not window.busy and window.state['canLoad'])
        assert calls == [job] and window.controller.runtime.jobRepository.get(job).isTerminal
        if showControls:
            assert not window.contextBanner.isVisible()
    finally:
        release.set()
        closeWindow(window)


def testWindowPreferencesClampAndRestoreMaximizedNormalGeometry(qtApp, tmp_path):
    prefs = preferences(tmp_path)
    area = qtApp.primaryScreen().availableGeometry()
    prefs.setValue('window/geometry', QRect(area.right() + 500, area.bottom() + 500, 4000, 3000))
    window = OperatorWindow(dataRoot=tmp_path / 'data', preferences=prefs)
    window.show()
    try:
        waitFor(lambda: window.controller is not None and not window.busy)
        assert area.contains(window.frameGeometry())
        window.setGeometry(area.adjusted(28, 45, -65, -65))
        normal = window.geometry()
        window.showMaximized()
        waitFor(window.isMaximized)
    finally:
        closeWindow(window)
    assert prefs.value('window/maximized', type=bool) and prefs.value('window/geometry') == normal
    restored = OperatorWindow(dataRoot=tmp_path / 'data', preferences=prefs)
    restored.show()
    try:
        waitFor(restored.isMaximized)
        restored.showNormal()
        waitFor(lambda: not restored.isMaximized())
        assert restored.geometry() == normal
        assert preferences(tmp_path / 'other').value('window/geometry') is None
    finally:
        closeWindow(restored)


@pytest.mark.parametrize('autoStart', [False, True])
def testRestartUsesRememberedProjectAndAutoStart(qtApp, tmp_path, autoStart):
    path, document = currentProductionProject(tmp_path / 'project', mode='continuous', autoStart=autoStart)
    prefs = preferences(tmp_path)
    identities = []
    for requested in (path, None):
        window = OperatorWindow(requested, dataRoot=tmp_path / 'data', preferences=prefs)
        window.show()
        try:
            waitFor(lambda: (window.pages is not None and not window.busy) or window.error)
            assert not window.error
            if autoStart:
                waitFor(lambda: not window.busy and window.state.get('jobStatus') == 'RUNNING')
            runtime = window.controller.runtime
            identities.append(runtime.runtimeInstanceId)
            info = runtime.GetRuntimeInfo(pb.RuntimeInfoRequest(), None)
            assert info.project_id == window.controller.document.project.projectId == document.project.projectId
            assert info.document_revision == window.state['documentRevision']
            assert Path(window.projectPath) == Path(prefs.value('projectPath')) == path
            jobs = runtime.jobRepository.all()
            assert len(jobs) == int(autoStart)
            assert bool(window.pages.hub) == bool(window.controller.jobId) == autoStart
        finally:
            closeWindow(window)
    assert identities[0] != identities[1]


def testAutoStartSharesCapacityDecisionUntilManualStart(qtApp, tmp_path, monkeypatch):
    from copy import deepcopy

    from emo_master.core.project.models import ProjectDocument

    path, document = currentProductionProject(tmp_path / 'project', mode='continuous', autoStart=True)
    payload = document.model_dump(mode='json')
    payload['workflows']['second'] = deepcopy(payload['workflows']['main'])
    payload['workflowOrder'].append('second')
    payload['runtime']['maxConcurrentJobs'] = 1
    document = ProjectDocument.model_validate(payload)
    saveProject(path, document.model_dump(mode='json'))
    originalLoad = ProductionRuntime.load
    runtimeStarts = []
    concurrentJobs = []

    def loadWithOccupiedSlot(owner, *args, **kwargs):
        loaded = originalLoad(owner, *args, **kwargs)
        with grpc.insecure_channel(owner.address) as channel:
            client = rpc.RuntimeServiceStub(channel)
            result = client.StartJob(pb.StartJobRequest(project_id=loaded.project.projectId,
                workflow_id='second', expected_runtime_instance_id=owner.runtime.runtimeInstanceId,
                start_request_id=uuid4().hex, legacy_snapshot_policy='NONE'), timeout=10)
            assert result.ok, result.message
            concurrentJobs.append(result.job_id)
        originalStart = owner.runtime.StartJob

        def recordStart(request, context):
            runtimeStarts.append(request.workflow_id)
            return originalStart(request, context)

        monkeypatch.setattr(owner.runtime, 'StartJob', recordStart)
        return loaded

    monkeypatch.setattr(ProductionRuntime, 'load', loadWithOccupiedSlot)
    window = OperatorWindow(path, dataRoot=tmp_path / 'data', preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: (window.workflowTabs is not None and not window.busy) or window.error)
        assert not window.error and window.selectedWorkflowIds == ['main']
        [otherJobId] = concurrentJobs
        runtime = window.controller.runtime
        with grpc.insecure_channel(window.controller.address) as channel:
            client = rpc.RuntimeServiceStub(channel)
            workflows = client.ListWorkflows(pb.ListWorkflowsRequest(project_id=document.project.projectId), timeout=5)
            main = next(row for row in workflows.workflows if row.workflow_id == 'main')
            assert not main.can_start and main.start_block_reason == 'E_MAX_CONCURRENT_JOBS'
            assert window.state['workflowStart']['main'] == dict(canStart=False, startBlockReason=main.start_block_reason)
            assert not window.startButton.isEnabled() and not window.workflowControls['main']['start'].isEnabled()
            assert 'E_MAX_CONCURRENT_JOBS' in window.message.text() and '并发运行名额不足' in window.message.text()
            assert not runtimeStarts and {row.jobId for row in runtime.jobRepository.all()} == {otherJobId}

            stopped = client.StopJob(pb.StopJobRequest(job_id=otherJobId, expected_project_id=document.project.projectId,
                expected_runtime_instance_id=runtime.runtimeInstanceId), timeout=5)
            assert stopped.ok, stopped.message
            waitFor(lambda: not window.busy and window.startButton.isEnabled())
            assert runtime.jobRepository.get(otherJobId).isTerminal
            assert not runtime.jobSupervisor.ownsJobResources(otherJobId)
            assert not runtimeStarts and {row.jobId for row in runtime.jobRepository.all()} == {otherJobId}
            assert 'E_MAX_CONCURRENT_JOBS' not in window.message.text()

        QTest.mouseClick(window.startButton, Qt.LeftButton)
        waitFor(lambda: not window.busy and window.state['workflows']['main'].get('jobStatus') == 'RUNNING')
        ownJobId = window.state['workflows']['main']['jobId']
        assert runtimeStarts == ['main']
        assert {row.jobId for row in runtime.jobRepository.all()} == {otherJobId, ownJobId}
    finally:
        closeWindow(window)


def testCloseDuringInitialLoadDoesNotAutoStartAndGuiRemainsResponsive(qtApp, tmp_path, monkeypatch):
    root = tmp_path / "project"
    productionProject(root, autoStart=True)
    entered, release = threading.Event(), threading.Event()
    original = ProductionRuntime.load
    def delayed(self, path):
        entered.set()
        assert release.wait(5)
        return original(self, path)
    monkeypatch.setattr(ProductionRuntime, "load", delayed)
    window = OperatorWindow(root, dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    pulses = []
    timer = QTimer()
    timer.timeout.connect(lambda: pulses.append(1))
    timer.start(5)
    try:
        waitFor(entered.is_set)
        window.close()
        waitFor(lambda: len(pulses) >= 3)
        assert window.closing and not window.done
        release.set()
        waitFor(lambda: window.done)
        assert window.controller.closed and not window.controller.runtime.jobRepository.all()
        assert window.hub is None
    finally:
        timer.stop()
        release.set()
        closeWindow(window)


def testSourceEntryImportsNoDesignerAndCheckNeverExecutes(tmp_path):
    root = tmp_path / "project"
    productionProject(root, autoStart=True)
    script = Path(__file__).resolve().parents[3] / "scripts/run_operator.py"
    result = subprocess.run([sys.executable, str(script), str(root), "--check",
                             "--data-root", str(tmp_path / "check")],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "no Job started" in result.stdout and not (root / "outputs/result.png").exists()
    source, _ = currentProductionProject(tmp_path / "log-source", autoStart=True)
    code = """
import sys, json, time
from pathlib import Path
from PySide2.QtCore import QSettings, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication
from emo_master.apps.operator_runtime.main import OperatorWindow

def wait(predicate):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError(window.error or 'standalone Operator did not finish the log step')

data = Path(sys.argv[1])
app = QApplication([])
window = OperatorWindow(Path(sys.argv[2]), dataRoot=data,
    preferences=QSettings(str(data.parent / 'operator-import-check.ini'), QSettings.IniFormat))
window.show()
try:
    wait(lambda: window.pages is not None and window.controller.jobId and not window.busy)
    window.showLogs()
    logs = window.logView
    job = window.controller.jobId
    assert logs.sources.currentIndex() == 0
    wait(lambda: any(row.job_id == job for row in logs.jobLogs.recent))
    QTest.mouseClick(logs.sources, Qt.LeftButton, pos=logs.sources.tabRect(1).center())
    wait(lambda: logs.systemLogs.page is not None and logs.systemLogs.page['ok'])
    print(json.dumps([s for s in sys.modules if s.startswith('emo_master.apps.designer')]))
finally:
    window.close()
    wait(lambda: window.done)
"""
    env = os.environ.copy()
    sourceRoot = str(script.parents[1] / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (sourceRoot, env.get("PYTHONPATH"))))
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "import-check"), str(source)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip()) == []


def testInvalidRememberedProjectStillAllowsSelectionAndClosing(qtApp, tmp_path):
    prefs = preferences(tmp_path)
    prefs.setValue("projectPath", str(tmp_path / "missing"))
    window = OperatorWindow(dataRoot=tmp_path / "data", preferences=prefs)
    window.show()
    try:
        waitFor(lambda: window.error and not window.busy)
        assert window.openAction.isEnabled() and not window.startButton.isEnabled()
        assert window.communicationButton.isVisible() and window.communicationButton.isEnabled()
        assert {window.openAction, window.reloadAction, window.updateAction, window.targetsAction,
                window.viewRunsAction}.issubset(window.projectMenu.actions())
        assert window.projectButton.isVisible() and window.reloadAction.isEnabled()
        assert not window.updateAction.isEnabled() and not window.targetsAction.isEnabled()
        assert window.settingsButton.isEnabled() and window.logsButton.isEnabled()
        assert not window.startupPage.retryButton.isVisible()  # Project error is not a failed Runtime constructor.
        QTest.mouseClick(window.logsButton, Qt.LeftButton)
        assert window.contentStack.currentWidget() is window.logView
    finally:
        closeWindow(window)


@pytest.mark.parametrize("failure", ["occupied", "invalid"])
def testFailedProjectSwitchKeepsStartControlAndOwnershipConsistent(qtApp, tmp_path, failure):
    first, second = tmp_path / "first", tmp_path / "second"
    productionProject(first)
    productionProject(second)
    if failure == "invalid":
        (second / "project.json").write_text("{}", encoding="utf-8")
    window = OperatorWindow(first, dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.pages is not None and not window.busy or window.error)
        assert not window.error
        previous = window.controller.projectOwner
        with DirectoryOwner(second) if failure == "occupied" else nullcontext():
            window.loadProject(second)
            waitFor(lambda: window.error and not window.busy)
        assert not window.controller.jobId and window.openAction.isEnabled()
        if failure == "occupied":
            assert window.state["state"] == "READY" and window.startButton.isEnabled()
            assert window.controller.projectOwner is previous and previous.stream is not None
            with pytest.raises(RuntimeError, match="in use"):
                DirectoryOwner(first).acquire()
        else:
            assert window.state["state"] == "EMPTY" and not window.startButton.isEnabled()
            assert window.controller.document is None and window.controller.projectOwner is None
            with DirectoryOwner(first):
                pass
        assert not window.controller.runtime.jobRepository.all()
    finally:
        closeWindow(window)


def testStatusPollDoesNotDiscardOperatorStartOrAdmitTwice(qtApp, tmp_path):
    root = tmp_path / "project"
    productionProject(root)
    window = OperatorWindow(root, dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    entered, release = threading.Event(), threading.Event()
    try:
        waitFor(lambda: window.pages is not None and not window.busy or window.error)
        assert not window.error
        def delayedStatus():
            entered.set()
            assert release.wait(5)
            return window.controller.status()
        window.launch("status", delayedStatus)
        waitFor(entered.is_set)
        assert window.startButton.isEnabled()
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        assert window.pendingCommand is not None and not window.controller.jobId
        release.set()
        waitFor(lambda: window.hub is not None or window.error)
        assert not window.error and len(window.controller.runtime.jobRepository.all()) == 1
    finally:
        release.set()
        closeWindow(window)


@pytest.mark.parametrize("autoStart", [False, True])
def testWindowUpdatePreservesOutputsAndUsesNewAutoStartPolicy(qtApp, tmp_path, autoStart):
    source = tmp_path / "engineering"
    sourceFile, document = currentProductionProject(source, mode="continuous", declared=True)
    destination = tmp_path / "installed"
    installRuntimePackage(buildRuntimePackage(source, tmp_path / "packages"), destination)
    (destination / "outputs").mkdir()
    history = destination / "outputs/history.csv"
    history.write_bytes(b"site history")
    window = OperatorWindow(destination, dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.pages is not None and not window.busy or window.error)
        assert not window.error and window.updateAction.isEnabled()
        document.production.autoStart = autoStart
        document.production.cycleIntervalMs = 200
        saveProject(sourceFile, document.model_dump(mode="json"))
        window.updateProject(buildRuntimePackage(source, tmp_path / "packages"))
        waitFor(lambda: not window.busy and window.controller.settings.cycleIntervalMs == 200 or window.error)
        assert not window.error and history.read_bytes() == b"site history"
        assert window.controller.settings.autoStart == autoStart
        if autoStart:
            waitFor(lambda: window.hub is not None and bool(window.pages.displayed) or window.error)
            assert not window.error and not window.updateAction.isEnabled()
            first = window.controller.jobId
            window.updateProject(tmp_path / "not-a-package.vxpkg")
            assert window.controller.jobId == first and not window.error
        else:
            assert window.state["state"] == "READY" and not window.controller.jobId
        assert len(window.controller.runtime.jobRepository.all()) == int(autoStart)
    finally:
        closeWindow(window)
