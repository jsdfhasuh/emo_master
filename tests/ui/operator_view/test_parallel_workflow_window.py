from PySide2.QtCore import Qt
from PySide2.QtTest import QTest

from emo_master.apps.operator_runtime.main import OperatorWindow
from tests.runtime.two_station_fixture import twoStationProject
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_view import waitFor


def testIndependentNativeWorkflowStartStopRestartPreservesOtherView(qtApp, tmp_path):
    root = tmp_path / "engineering"
    twoStationProject(root)
    window = OperatorWindow(root, dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.controller and window.controller.document and not window.busy or window.error)
        assert not window.error
        assert window.selectedWorkflowIds == ["main"]
        window.selectedWorkflowIds = ["main", "second"]
        window._controls()
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        waitFor(lambda: len(window.workflowHubs) == 2 or window.error)
        assert not window.error
        assert window.workflowTabs.count() == 2
        assert window.workflowPages["main"].config.pageOrder == ["main"]
        assert window.workflowPages["second"].config.pageOrder == ["second"]
        waitFor(lambda: bool(window.workflowPages["main"].displayed))
        window.workflowTabs.setCurrentIndex(1)
        waitFor(lambda: bool(window.workflowPages["second"].displayed))
        window.workflowTabs.setCurrentIndex(0)
        waitFor(lambda: not window.busy and all(row["state"] == "RUNNING" for row in window.state["workflows"].values()))
        secondSession, secondHub, secondPages = (window.workflowSessions["second"], window.workflowHubs["second"], window.workflowPages["second"])
        secondJob = window.controller.sessions["second"].jobId
        firstJob = window.controller.sessions["main"].jobId
        window.stopWorkflows("main")
        waitFor(lambda: not window.busy and window.state["workflows"]["main"]["canStart"])
        assert window.workflowControls["main"]["start"].isEnabled()
        assert not window.reloadButton.isEnabled()  # Other workflow still running.
        window.startWorkflows("main")
        waitFor(lambda: not window.busy and window.controller.sessions["main"].jobId != firstJob or window.error)
        assert not window.error
        waitFor(lambda: bool(window.workflowPages["main"].displayed))
        assert (window.workflowSessions["second"], window.workflowHubs["second"], window.workflowPages["second"]) == (secondSession, secondHub, secondPages)
        assert window.controller.sessions["second"].jobId == secondJob
        assert len(window.controller.presentation.jobs) == 2
        window.grab().save(str(tmp_path / "dual-workflow-window.png"))
    finally:
        closeWindow(window)
    assert window.controller.closed and not window.workflowSessions


def testAddingWorkflowsToSingleRunningJobAndThenAddingThird(qtApp, tmp_path):
    from copy import deepcopy
    from tests.runtime.production_fixture import saveDocument
    root = tmp_path / 'engineering'
    document = twoStationProject(root)
    document.runtime.maxConcurrentJobs = 3
    document.workflows['third'] = deepcopy(document.workflows['second'])
    document.workflowOrder.append('third')
    binding = deepcopy(document.resources.parameterBindings[0])
    binding.target.workflowId = 'third'
    document.resources.parameterBindings.append(binding)
    saveDocument(root, document)
    window = OperatorWindow(root, dataRoot=tmp_path / 'data', preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.controller and window.controller.document and not window.busy or window.error)
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        waitFor(lambda: window.session and window.pages and window.pages.displayed or window.error)
        assert not window.error
        firstJob, firstSession = window.controller.sessions['main'].jobId, window.session
        window.selectedWorkflowIds = ['main', 'second']
        window.startDetection()
        waitFor(lambda: window.workflowTabs and window.workflowTabs.count() == 2 and not window.busy or window.error)
        assert not window.error
        assert window.controller.sessions['main'].jobId == firstJob
        assert window.workflowSessions['main'] is firstSession and not firstSession.stop.is_set()
        pages = dict(window.workflowPages)
        window.selectedWorkflowIds = ['main', 'second', 'third']
        window.startDetection()
        waitFor(lambda: window.workflowTabs.count() == 3 and not window.busy or window.error)
        assert not window.error
        assert all(window.workflowPages[key] is value for key, value in pages.items())
        assert window.controller.sessions['main'].jobId == firstJob
    finally:
        closeWindow(window)
