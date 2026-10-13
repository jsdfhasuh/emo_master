"""W01: real OperatorWindow, five While Jobs and five real DisplaySessions."""
import json
import os
from pathlib import Path

import pytest

from PySide2.QtCore import Qt
from PySide2.QtTest import QTest

from examples.runtime_pages_p2 import pluginRoots
from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.ui.presentation.renderer import RuntimePages
from tests.runtime.parallel_workflow_fixture import whileProject
from tests.runtime.production_fixture import saveDocument
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_view import waitFor


@pytest.mark.parametrize("closeWhileRunning", [False, True], ids=["full-lifecycle", "close-while-running"])
def testFiveWhileSessionsTabsReconnectStopRestartAndClose(qtApp, tmp_path, closeWhileRunning):
    root = tmp_path / "engineering"
    document, roots = whileProject(root, count=5, limit=None)
    document.runtime.startupWorkflowIds = roots
    saveDocument(root, document)
    plugins = pluginRoots(tmp_path)
    window = OperatorWindow(root, dataRoot=tmp_path / "data", preferences=preferences(tmp_path),
        runtimeFactory=lambda data: ProductionRuntime(data, pluginRootPaths=plugins))
    window.show()
    second = None
    sessions = []
    server = None
    evidence = {}
    scenarioPassed = False
    output = Path(os.environ.get("EMO_DISPLAY_EVIDENCE", str(tmp_path / "evidence"))) / (
        "close-while-running" if closeWhileRunning else "full-lifecycle")
    output.mkdir(parents=True, exist_ok=True)
    try:
        waitFor(lambda: window.controller and window.controller.document and not window.busy or window.error)
        assert not window.error
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        waitFor(lambda: len(window.workflowSessions) == 5 and not window.busy or window.error, seconds=45)
        assert not window.error
        sessions.extend(window.workflowSessions.values())
        owner = window.controller
        server = owner.server
        waitFor(lambda: server.active["display"] == 5)
        if closeWhileRunning:
            # Independent first-failure teardown path: close without Stop, with
            # all five Jobs and their RPC/session workers still active.
            evidence["status"] = "RUNNING_AT_CLOSE"
            scenarioPassed = True
            return
        jobs = {key: session.jobId for key, session in window.workflowSessions.items()}
        for index, key in enumerate(roots):
            window.workflowTabs.setCurrentIndex(index)
            pages = window.workflowPages[key]
            waitFor(lambda: bool(pages.displayed) and all(scope.images for scope in pages.displayed.values()))
            scope = next(iter(pages.displayed.values()))
            assert scope.result.identity.jobId == jobs[key]
            assert scope.result.status == "COMPLETE" and scope.images
            assert all(s.image.ownerResultKey == scope.result.identity.resultKey
                       for s in scope.result.sources if s.image)
            window.grab().save(str(output / f"tab-{index}.png"))
        # Rapid navigation must not create subscriptions or replace Job identity.
        admitted = len([e for e in server.displaySubscriptions.events if e["action"] == "admitted"])
        for index in list(range(5)) * 4:
            window.workflowTabs.setCurrentIndex(index)
            qtApp.processEvents()
        assert len([e for e in server.displaySubscriptions.events if e["action"] == "admitted"]) == admitted
        # A second local window shares the hub, not a sixth network subscription.
        second = RuntimePages(window.workflowPages[roots[0]].config, hub=window.workflowHubs[roots[0]])
        second.show()
        waitFor(lambda: bool(second.displayed))
        assert server.active["display"] == 5
        second.close()
        second = None
        session = sessions[-1]
        oldStream = session.stream
        oldStream.cancel()
        waitFor(lambda: session.stream is not None and session.stream is not oldStream and session.stream.is_active())
        waitFor(lambda: server.active["display"] == 5)
        assert not [e for e in server.displaySubscriptions.events if e["action"] == "rejected"]
        window.stopWorkflows(roots[0])
        waitFor(lambda: not window.busy and window.state["workflows"][roots[0]]["canStart"])
        assert all(owner.runtime.jobRepository.get(jobs[key]).status == "RUNNING" for key in roots[1:])
        window.startWorkflows(roots[0])
        waitFor(lambda: not window.busy and window.workflowSessions[roots[0]].jobId != jobs[roots[0]])
        sessions.append(window.workflowSessions[roots[0]])
        waitFor(lambda: server.active["display"] == 5)
        window.stopWorkflows()
        waitFor(lambda: not window.busy and window.state["canLoad"])
        assert all(not owner.runtime.jobSupervisor.ownsJobResources(s.jobId) for s in sessions)
        assert not [e for s in sessions for e in s.errors if e[0] == "RESOURCE_EXHAUSTED" and "display" in e[1]]
        evidence["status"] = "PASS"
        scenarioPassed = True
    finally:
        if second:
            second.close()
        server = window.controller.server
        try:
            closeWindow(window)
        finally:
            evidence.update(platform=qtApp.platformName(), errors=[list(s.errors) for s in sessions],
                admission=list(server.displaySubscriptions.events), work_peak=server.displaySubscriptions.workPeak,
                active=dict(server.active), session_threads_alive=[t.name for s in sessions for t in s.threads if t.is_alive()],
                exporter_alive=[bool(s["process"] and s["process"].is_alive()) for s in window.controller.presentation.exporter.slots],
                runtime_closed=window.controller.closed, close_error=window.error, window_done=window.done,
                busy=window.busy, operation=window.operationKind, message=window.message.text(),
                job_resources={s.jobId: window.controller.runtime.jobSupervisor.ownsJobResources(s.jobId) for s in sessions},
                assets=window.controller.presentation.assets.stats())
            retired = (window.done and window.controller.closed and not any(server.active.values())
                       and not evidence["session_threads_alive"] and not any(evidence["exporter_alive"]))
            evidence["status"] = "PASS" if scenarioPassed and retired else "FAIL"
            (output / "five-native-sessions.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        assert not any(server.active.values()) and not evidence["session_threads_alive"]
        assert not any(evidence["exporter_alive"]) and window.controller.closed
        assert not any(evidence["job_resources"].values()) and evidence["assets"]["lease_handles"] == 0
        events = evidence["admission"]
        assert sorted(e["subscriptionId"] for e in events if e["action"] == "admitted") == sorted(
            e["subscriptionId"] for e in events if e["action"] == "retired")
    # Reopen the same on-disk Runtime after full shutdown, not just a new Job.
    previousInstance = window.controller.runtime.runtimeInstanceId
    reopened = OperatorWindow(root, dataRoot=tmp_path / "data", preferences=preferences(tmp_path),
        runtimeFactory=lambda data: ProductionRuntime(data, pluginRootPaths=plugins))
    reopened.show()
    try:
        waitFor(lambda: reopened.controller and reopened.controller.document and not reopened.busy or reopened.error)
        assert not reopened.error
        assert reopened.controller.runtime.runtimeInstanceId != previousInstance
        QTest.mouseClick(reopened.startButton, Qt.LeftButton)
        waitFor(lambda: len(reopened.workflowSessions) == 5 and not reopened.busy or reopened.error, seconds=45)
        assert not reopened.error
        waitFor(lambda: reopened.controller.server.active["display"] == 5)
        assert not ({s.jobId for s in reopened.workflowSessions.values()} & {s.jobId for s in sessions})
    finally:
        closeWindow(reopened)
    assert reopened.controller.closed
