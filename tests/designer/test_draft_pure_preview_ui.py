"""Real specialized editors, current Qt draft, fresh Runtime, and no formal Job."""
from copy import deepcopy
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import emo_master  # noqa: F401
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest

from emo_master.apps.designer.operator_editors import OperatorEditorManager
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.plugins.builtins import _editor_support as support
from tests.designer.test_builtin_operator_editor_ui import _Settings
from tests.designer.test_operator_debug_window import wait
from tests.runtime.test_draft_context_preview import png, project
from tests.runtime.test_operator_debug_rpc import runtime as runtime


@pytest.mark.parametrize("version", ["2.1", "2.2", "2.3", "2.4"])
@pytest.mark.parametrize("directory", ["roi", "histogram"])
def testSpecializedLocalImageErrorRecoveryPreviewAndClose(runtime, tmp_path, monkeypatch, version, directory):
    manifest = json.loads((Path("src/emo_master/plugins/builtins") / directory / "manifest.json").read_text(encoding="utf-8"))
    payload = project(manifest["operatorId"], version)
    before = deepcopy(payload)
    owner = threading.get_ident()
    def draft(_key):
        assert threading.get_ident() == owner, "Mutable Designer drafts belong to the Qt thread"
        return payload
    manager = OperatorEditorManager(runtimeClient=RuntimeClient(runtime), settingsStore=_Settings(),
        applyParams=lambda *_: pytest.fail("Preview must not Apply"), appendLog=lambda *_: None,
        getPreviewProject=draft, cacheRoot=tmp_path / "ui-cache")
    window = manager.open(projectId="draft", workflowId="main", nodeId="node", operatorId=manifest["operatorId"],
        displayName=manifest["displayName"], schema=manifest["paramSchema"], values={},
        operatorDefinition=dict(version=manifest["version"], editorSpec=manifest["editor"]))
    controller = window._controller
    try:
        assert controller is not None
        good, bad = tmp_path / "gradient.png", tmp_path / "bad.png"
        good.write_bytes(png())
        bad.write_bytes(b"not an image")
        path = [bad]
        monkeypatch.setattr(support.QFileDialog, "getOpenFileName", lambda *_: (str(path[0]), ""))
        QTest.mouseClick(controller.localImageButton, Qt.LeftButton)
        assert not controller.currentAssetId and not runtime.previewAssetStore._assets
        assert window._statusLabel.text()
        path[0] = good
        QTest.mouseClick(controller.localImageButton, Qt.LeftButton)
        try:
            wait(lambda: "预览已刷新" in window._statusLabel.text())
        except AssertionError as error:
            future = controller._future
            raise AssertionError(f"Preview did not render: status={window._statusLabel.text()!r}, "
                f"asset={controller.currentAssetId!r}, generation={controller._generation}, "
                f"future={future!r}, reply={future.result() if future is not None and future.done() else None!r}") from error
        assert controller.currentAssetId in window.context._pureDraftAssets
        if directory == "roi":
            assert not controller.canvas._image.isNull()
            assert all(label.pixmap() is not None and not label.pixmap().isNull()
                for label in controller.outputLabels.values())
            controller.controls["width"].setValue(9)
            controller.controls["height"].setValue(5)
            wait(lambda: controller._future is not None and controller._future.done()
                and controller._future.result().assets[0].width == 9)
        else:
            assert controller.chart._histogram["pixelCount"] == 24 * 16
            assert not controller.sourceImageLabel.pixmap().isNull()
        assert payload == before
        assert runtime.loadedDocument is None and not runtime.jobRepository.all()
    finally:
        manager.closeAll()
        wait(lambda: not controller._retirementThread.is_alive())
    assert not runtime.previewAssetStore._assets
    assert not list(runtime.previewAssetStore.transientRoot.iterdir())


def testCurrentPreviewResultSurvivesLateStaleCompletion():
    controller = support.PurePreviewControllerBase()
    rendered = []
    controller.context = SimpleNamespace(setError=lambda message: pytest.fail(message))
    controller._generation = 2
    controller.handlePreviewResult = lambda outputs, assets: rendered.append((outputs, assets))
    controller._results.put((2, SimpleNamespace(ok=True, outputs_json='{"current":true}', assets=[]), None))
    controller._results.put((1, None, RuntimeError("older preview completed late")))
    try:
        controller._pollResults()
        assert rendered == [({"current": True}, {})]
        assert controller._results.empty()
    finally:
        controller.disposePreviewBase()


def testClosingPreviewWaitsForAllLateProducersBeforeReleasingAssets():
    started, release = threading.Event(), threading.Event()
    events = []
    controller = support.PurePreviewControllerBase()
    controller.context = SimpleNamespace(bindPreviewInvalidation=lambda *_: None,
        closePurePreview=lambda: events.append("release"))
    def produce():
        started.set()
        assert release.wait(5)
        events.append("late-output")
    controller._future = controller._executor.submit(produce)
    assert started.wait(2)
    try:
        controller.disposePreviewBase()
        assert not events and controller._retirementThread.is_alive()
    finally:
        release.set()
        controller._retirementThread.join(5)
    assert events == ["late-output", "release"]
    assert not controller._retirementThread.is_alive()


def testClosingPreviewWaitsForOlderWorkerEvenWhenNewestResultIsDone():
    from concurrent.futures import ThreadPoolExecutor
    started, unblock = threading.Event(), threading.Event()
    events = []
    controller = support.PurePreviewControllerBase()
    tasks = []
    def old():
        started.set()
        assert unblock.wait(5)
        events.append("old-output")
    tasks.extend([old, lambda: events.append("new-output")])
    controller.context = SimpleNamespace(bindPreviewInvalidation=lambda *_: None,
        preparePurePreview=lambda *_: tasks.pop(0), closePurePreview=lambda: events.append("release"),
        cancelPurePreview=lambda *_: None, log=lambda *_: None, setError=lambda message: pytest.fail(message))
    controller.collectParams = lambda: {}
    controller.currentAssetId = "local-image"
    # Use the real scheduling path: retaining only _future would lose the old
    # running producer once a second, faster request completes.
    controller._startPreview()
    assert started.wait(2)
    controller._startPreview()
    try:
        with ThreadPoolExecutor(1) as waiter:
            waiter.submit(controller._future.result).result(timeout=2)
        assert events == ["new-output"]
        controller.disposePreviewBase()
        assert controller._retirementThread.is_alive() and "release" not in events
    finally:
        unblock.set()
        controller.disposePreviewBase()
        controller._retirementThread.join(5)
    assert events == ["new-output", "old-output", "release"]
    assert not controller._retirementThread.is_alive()
