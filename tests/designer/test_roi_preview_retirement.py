"""Regression of the reported manager-close and focused-wheel counterexamples.

Real Qt widgets and Runtime RPCs, with an explicitly delayed client return;
these are automated fault-injection tests, not manual desktop acceptance.
"""
import json
from pathlib import Path
import threading

import pytest
from PySide2.QtCore import QPoint, QPointF, Qt
from PySide2.QtGui import QWheelEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QScrollArea

from emo_master.apps.designer.operator_editors import OperatorEditorManager
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from tests.designer.test_builtin_operator_editor_ui import _Settings
from tests.designer.test_operator_debug_window import wait
from tests.designer.test_roi_preview_export import roi as roi
from tests.runtime.test_draft_context_preview import png, project
from tests.runtime.test_operator_debug_rpc import runtime as runtime


@pytest.mark.parametrize("closePath", ["manager", "window", "invalidate"])
@pytest.mark.parametrize("workerFails", [False, True])
def testRetireEveryProducerBeforeSessionRelease(runtime, tmp_path, monkeypatch, closePath, workerFails):
    manifest = json.loads(Path("src/emo_master/plugins/builtins/roi/manifest.json").read_text(encoding="utf-8"))
    payload = project(manifest["operatorId"])
    manager = OperatorEditorManager(runtimeClient=RuntimeClient(runtime), settingsStore=_Settings(),
        applyParams=lambda *_: pytest.fail("must not Apply"), appendLog=lambda *_: None,
        getPreviewProject=lambda _: payload, cacheRoot=tmp_path / "cache")
    window = manager.open(projectId="draft", workflowId="main", nodeId="node", operatorId=manifest["operatorId"],
        displayName=manifest["displayName"], schema=manifest["paramSchema"], values={},
        operatorDefinition=dict(version=manifest["version"], editorSpec=manifest["editor"]))
    controller, editor = window._controller, window.context
    entered, release = threading.Event(), threading.Event()
    original = editor._pureRuntimeClient.runOperatorPreview
    closes = []
    originalClose = editor._pureRuntimeClient.closeDraftPurePreviewSession

    def close(session):
        closes.append(session)
        return originalClose(session)

    def delayed(*args, **kwargs):
        reply = original(*args, **kwargs)
        assert reply.ok, reply.message
        if not entered.is_set():
            entered.set()
            assert release.wait(10), "test must always release the injected producer"
            if workerFails:
                raise RuntimeError("injected late transport failure")
        return reply

    monkeypatch.setattr(editor._pureRuntimeClient, "runOperatorPreview", delayed)
    monkeypatch.setattr(editor._pureRuntimeClient, "closeDraftPurePreviewSession", close)
    try:
        controller.currentAssetId = editor.uploadPreviewImage(png(), "input.png")
        session = editor._pureDraftSessionId
        controller._startPreview()
        old = controller._future
        assert entered.wait(3)
        controller._startPreview()
        wait(lambda: controller._future.done())
        controller._pollResults()
        assert controller._previewSnapshot is not None and not old.done()
        assets = set(runtime.previewAssetStore._assets)
        assert len(assets) == 5
        if closePath == "manager":
            manager.closeAll()
        elif closePath == "window":
            window.forceClose()
        else:
            editor.invalidatePreviewSources()
        assert controller._previewSnapshot is None
        assert session in runtime.pureDraftPreviewSessions.sessions
        assert set(runtime.previewAssetStore._assets) == assets
        assert closes == []
        # Repeated invalidation must neither over-release nor allow old results
        # to become current. Replacing a session may coexist with its retirement.
        editor.closePurePreview()
        if closePath == "invalidate":
            editor.uploadPreviewImage(png(), "new.png")
            replacement = editor._pureDraftSessionId
            assert replacement != session
        release.set()
        wait(lambda: old.done() and session not in runtime.pureDraftPreviewSessions.sessions)
        assert closes.count(session) == 1
        if closePath == "invalidate":
            assert editor._pureDraftSessionId == replacement
            assert replacement in runtime.pureDraftPreviewSessions.sessions
    finally:
        release.set()
        manager.closeAll()
        wait(lambda: not controller._retirementThread.is_alive())
    assert not runtime.previewAssetStore._assets
    assert not runtime.pureDraftPreviewSessions.sessions
    assert not list(runtime.previewAssetStore.transientRoot.iterdir())


@pytest.mark.parametrize("field", ["width", "height", "x", "padValue", "roiType", "interpolation"])
@pytest.mark.parametrize("focused", [False, True])
def testRoiWheelScrollsParametersWithoutEditingEvenWhenFocused(roi, field, focused):
    window, controller, _ = roi
    control = controller.controls[field]
    scroll = next(widget for widget in window.findChildren(QScrollArea)
                  if widget.isAncestorOf(control))
    # Force a scrollable viewport independent of test host screen geometry.
    scroll.setFixedHeight(180)
    QApplication.processEvents()
    bar = scroll.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(0)
    if focused:
        control.setFocus(Qt.TabFocusReason)
    else:
        controller.localImageButton.setFocus()
    before = controller.collectParams()
    generation, snapshot = controller._generation, controller._previewSnapshot
    point = control.rect().center()
    event = QWheelEvent(QPointF(point), QPointF(control.mapToGlobal(point)),
                        QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(control, event)
    assert controller.collectParams() == before
    assert controller._generation == generation and controller._previewSnapshot is snapshot
    assert controller.exportArchiveButton.isEnabled()
    assert bar.value() > 0
    if field == "width":
        control.setFocus(Qt.TabFocusReason)
        QTest.keyClick(control, Qt.Key_Up)
        assert control.value() > before["width"]


def testRoiDimensionsPositiveAtDisplayedPrecisionButCoordinatesRemainSigned(roi):
    _, controller, _ = roi
    for name in ("width", "height"):
        control = controller.controls[name]
        assert control.decimals() == 3
        assert control.minimum() == .001
        control.setValue(-2)
        assert control.value() == .001
    for name in ("x", "y", "centerX", "centerY"):
        controller.controls[name].setValue(-2)
        assert controller.controls[name].value() == -2
