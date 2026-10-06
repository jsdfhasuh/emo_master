import json
import os
from contextlib import nullcontext
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from PySide2.QtCore import QSettings, QTimer, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QPushButton

from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.core.project.delivery_store import DirectoryOwner
from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
from tests.runtime.production_fixture import productionProject, saveDocument
from tests.ui.operator_view.test_view import waitFor


def preferences(tmp_path):
    return QSettings(str(tmp_path / "preferences.ini"), QSettings.IniFormat)


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
        assert not window.openButton.isEnabled() and window.stopButton.isEnabled()
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


def testRememberedProjectLoadsWithoutStartingAndCheckIgnoresAutoStart(qtApp, tmp_path):
    root = tmp_path / "project"
    productionProject(root, autoStart=False)
    prefs = preferences(tmp_path)
    prefs.setValue("projectPath", str(root))
    window = OperatorWindow(dataRoot=tmp_path / "data", preferences=prefs)
    window.show()
    try:
        waitFor(lambda: window.pages is not None or window.error)
        assert not window.error and not window.controller.jobId
        assert window.pages.hub is None and not window.controller.runtime.jobRepository.all()
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
    code = """
import sys, json
from pathlib import Path
from emo_master.apps.operator_runtime.controller import ProductionRuntime
import emo_master.apps.operator_runtime.main
owner = ProductionRuntime(Path(sys.argv[1]))
try:
    print(json.dumps([s for s in sys.modules if s.startswith('emo_master.apps.designer')]))
finally:
    owner.close()
"""
    env = os.environ.copy()
    sourceRoot = str(script.parents[1] / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (sourceRoot, env.get("PYTHONPATH"))))
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "import-check")],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip()) == []


def testInvalidRememberedProjectStillAllowsSelectionAndClosing(qtApp, tmp_path):
    prefs = preferences(tmp_path)
    prefs.setValue("projectPath", str(tmp_path / "missing"))
    window = OperatorWindow(dataRoot=tmp_path / "data", preferences=prefs)
    window.show()
    try:
        waitFor(lambda: window.error and not window.busy)
        assert window.openButton.isEnabled() and not window.startButton.isEnabled()
        assert set(button.text() for button in window.findChildren(QPushButton)) == {"选择工程", "更新工程", "重新加载", "开始", "停止"}
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
        assert not window.controller.jobId and window.openButton.isEnabled()
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
    document = productionProject(source)
    destination = tmp_path / "installed"
    installRuntimePackage(buildRuntimePackage(source, tmp_path / "packages"), destination)
    (destination / "outputs").mkdir()
    history = destination / "outputs/history.csv"
    history.write_bytes(b"site history")
    window = OperatorWindow(destination, dataRoot=tmp_path / "data", preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.pages is not None and not window.busy or window.error)
        assert not window.error and window.updateButton.isEnabled()
        document.production.autoStart = autoStart
        document.production.cycleIntervalMs = 200
        saveDocument(source, document)
        window.updateProject(buildRuntimePackage(source, tmp_path / "packages"))
        waitFor(lambda: not window.busy and window.controller.settings.cycleIntervalMs == 200 or window.error)
        assert not window.error and history.read_bytes() == b"site history"
        assert window.controller.settings.autoStart == autoStart
        if autoStart:
            waitFor(lambda: window.hub is not None and bool(window.pages.displayed) or window.error)
            assert not window.error and not window.updateButton.isEnabled()
            first = window.controller.jobId
            window.updateProject(tmp_path / "not-a-package.vxpkg")
            assert window.controller.jobId == first and not window.error
        else:
            assert window.state["state"] == "READY" and not window.controller.jobId
        assert len(window.controller.runtime.jobRepository.all()) == int(autoStart)
    finally:
        closeWindow(window)
