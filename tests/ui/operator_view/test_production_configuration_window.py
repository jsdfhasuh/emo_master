"""Native operator controls for current-schema project configuration."""
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QFileDialog

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
from tests.runtime.current_production_fixture import currentProductionProject, node, savedPixels
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_view import waitFor


def ready(window):
    waitFor(lambda: not window.busy and window.pages is not None or window.error)
    assert not window.error, window.error


def runOnce(window):
    waitFor(lambda: not window.busy and window.startButton.isEnabled())
    previous = window.controller.jobId
    QTest.mouseClick(window.startButton, Qt.LeftButton)
    waitFor(lambda: window.error or (window.controller.jobId and window.controller.jobId != previous
                                   and not window.busy and window.state['canLoad']))
    assert not window.error, window.error
    assert window.state['jobStatus'] == 'COMPLETED'


def testReloadButtonAppliesCurrentProjectAndShowsCommittedRevision(qtApp, tmp_path):
    path, document = currentProductionProject(tmp_path / 'project')
    window = OperatorWindow(path, dataRoot=tmp_path / 'runtime', preferences=preferences(tmp_path))
    window.show()
    try:
        ready(window)
        runOnce(window)
        node(document, 'save').params['outputPath'] = 'outputs/changed.png'
        document.production.cycleIntervalMs = 432
        saveProject(path, document.model_dump(mode='json'))
        QTest.mouseClick(window.reloadButton, Qt.LeftButton)
        waitFor(lambda: window.error or (not window.busy and window.controller.settings.cycleIntervalMs == 432))
        assert not window.error, window.error
        assert window.state['documentRevision'] == 2
        assert '2' in window.message.text()
        runOnce(window)
        assert (path.parent / 'outputs/changed.png').is_file()
        waitFor(lambda: window.pages.displayed and 'COMPLETED' in window.pages.jobStatus.text())
        assert window.controller.jobId in window.pages.jobStatus.text()
        assert window.state['resourcesReleased']
        assert not window.controller.runtime.jobSupervisor.ownsJobResources(window.controller.jobId)
        assert window.pages.lastView.job.captureEnabled
        assert not window.pages.lastView.job.resourcesReleased  # The displayed result remains retained.
        assert window.grab().save(str(tmp_path / 'reloaded-runtime.png'))
    finally:
        closeWindow(window)


def testSelectionFailureKeepsOriginalPagesAndSuccessfulSelectionUpdatesPath(qtApp, tmp_path, monkeypatch):
    first, _document = currentProductionProject(tmp_path / 'first')
    second, document = currentProductionProject(tmp_path / 'second', value=193)
    node(document, 'load').params['imagePath'] = 'not-configured.png'
    saveProject(second, document.model_dump(mode='json'))
    prefs = preferences(tmp_path)
    window = OperatorWindow(first, dataRoot=tmp_path / 'runtime', preferences=prefs)
    window.show()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(second), ''))
    try:
        ready(window)
        QTest.mouseClick(window.openButton, Qt.LeftButton)
        waitFor(lambda: window.error and not window.busy)
        assert window.pages is not None and window.startButton.isEnabled()
        assert window.projectPath == str(first)
        assert prefs.value('projectPath') == str(first)
        node(document, 'load').params['imagePath'] = 'input.png'
        saveProject(second, document.model_dump(mode='json'))
        QTest.mouseClick(window.openButton, Qt.LeftButton)
        waitFor(lambda: not window.busy and window.projectPath == str(second))
        assert not window.error
        assert prefs.value('projectPath') == str(second)
        assert window.projectLabel.text() == document.project.name
        runOnce(window)
        assert (savedPixels(second.parent / 'outputs/result.png') == 193).all()
    finally:
        closeWindow(window)


def testPackageButtonPublishesNewAutoStartPolicyAndStopsOnClose(qtApp, tmp_path, monkeypatch):
    source, document = currentProductionProject(tmp_path / 'source')
    destination = tmp_path / 'installed'
    installRuntimePackage(buildRuntimePackage(source, tmp_path / 'packages'), destination)
    window = OperatorWindow(destination, dataRoot=tmp_path / 'runtime', preferences=preferences(tmp_path))
    window.show()
    try:
        ready(window)
        document.production.mode = 'continuous'
        document.production.autoStart = True
        document.production.cycleIntervalMs = 50
        saveProject(source, document.model_dump(mode='json'))
        package = buildRuntimePackage(source, tmp_path / 'packages')
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(package), ''))
        QTest.mouseClick(window.updateButton, Qt.LeftButton)
        waitFor(lambda: window.error or (window.pages is not None and window.pages.displayed and not window.busy))
        assert not window.error, window.error
        assert window.controller.settings.autoStart
        assert window.controller.settings.cycleIntervalMs == 50
        assert window.state['documentRevision'] == 2
        assert window.state['state'] == 'RUNNING'
        job = window.controller.jobId
        QTest.mouseClick(window.stopButton, Qt.LeftButton)
        waitFor(lambda: not window.busy and window.state['canStart'])
        assert not window.controller.runtime.jobSupervisor.ownsJobResources(job)
        QTest.mouseClick(window.startButton, Qt.LeftButton)
        waitFor(lambda: window.controller.jobId != job and not window.busy)
    finally:
        closeWindow(window)
    assert window.controller.closed
    assert all(not window.controller.runtime.jobSupervisor.ownsJobResources(record.jobId)
               for record in window.controller.runtime.jobRepository.all())


def testRememberedStartupRestoresOnlineUntilExplicitReload(qtApp, tmp_path):
    path, document = currentProductionProject(tmp_path / 'project')
    prefs = preferences(tmp_path)
    data = tmp_path / 'runtime'
    window = OperatorWindow(path, dataRoot=data, preferences=prefs)
    window.show()
    try:
        ready(window)
    finally:
        closeWindow(window)
    document.production.cycleIntervalMs = 432
    saveProject(path, document.model_dump(mode='json'))
    restored = OperatorWindow(dataRoot=data, preferences=prefs)
    restored.show()
    try:
        ready(restored)
        assert restored.controller.settings.cycleIntervalMs == 100
        assert restored.state['documentRevision'] == 1
        QTest.mouseClick(restored.reloadButton, Qt.LeftButton)
        waitFor(lambda: restored.error or (not restored.busy and restored.state['documentRevision'] == 2))
        assert not restored.error
        assert restored.controller.settings.cycleIntervalMs == 432
    finally:
        closeWindow(restored)
