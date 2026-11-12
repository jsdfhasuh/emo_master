"""Dedicated ROI UI automation, not manual/physical desktop acceptance."""
import hashlib
import json
from pathlib import Path
import zipfile

import cv2
import numpy as np
import pytest
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QMessageBox, QScrollArea, QToolButton

from emo_master.apps.designer.operator_editors import OperatorEditorManager
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.plugins.builtins import _editor_support as support
from tests.designer.test_builtin_operator_editor_ui import _Settings
from tests.designer.test_operator_debug_window import wait
from tests.runtime.test_draft_context_preview import png, project
from tests.runtime.test_operator_debug_rpc import runtime as runtime


PORTS = {"croppedImage", "croppedMask", "fullMask", "maskedImage"}


def testPolygonDraftRunsThroughRuntimeWithoutApplyingAndExportIdentifiesDraft(roi, tmp_path, monkeypatch):
    from PySide2.QtCore import QPointF
    window, controller, _ = roi
    original = controller.collectParams()
    before = controller._previewSnapshot
    controller.newPurposeCombo.setCurrentIndex(controller.newPurposeCombo.findData("exclude"))
    controller._setTool("polygon")
    for x, y in ((3, 4), (8, 4), (6, 8)):
        QTest.mouseClick(controller.canvas.viewport(), Qt.LeftButton,
                         pos=controller.canvas.mapFromScene(QPointF(x, y)))
    wait(lambda: controller._previewSnapshot is not None)
    snapshot = controller._previewSnapshot
    assert snapshot is not before
    metadata = json.loads(snapshot.metadataJson)
    assert metadata["editorState"]["draftIncluded"] is True
    assert metadata["requestedParams"]["roiType"] == "regions"
    assert len(metadata["requestedParams"]["regions"]) == 2
    assert controller.collectParams() == original
    mask = next(i for i in snapshot.images if i.port == "fullMask")
    pixels = cv2.imdecode(np.frombuffer(mask.content, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert pixels[5, 6] == 0
    assert 0 < np.count_nonzero(pixels) < 56
    assert not window.applyChanges()
    archive = tmp_path / "draft.zip"
    monkeypatch.setattr(support.QFileDialog, "getSaveFileName", lambda *_: (str(archive), ""))
    controller.exportArchiveAction.trigger()
    with zipfile.ZipFile(archive) as bundle:
        assert json.loads(bundle.read("result.json"))["preview"]["editorState"]["draftIncluded"] is True
    controller.canvas.finishDrawing()
    wait(lambda: controller._previewSnapshot is not None)
    assert len(controller.collectParams()["regions"][-1]["points"]) == 3
    assert json.loads(controller._previewSnapshot.metadataJson)["editorState"]["draftIncluded"] is False


def testPreviewRefreshPreservesManualResultZoomAndPan(roi, designerApplication):
    from PySide2.QtCore import QPointF
    _, controller, _ = roi
    view = controller.outputViews["croppedImage"]
    view.setZoom(20)
    view.centerOn(QPointF(5, 4))
    designerApplication.processEvents()
    center = view.mapToScene(view.viewport().rect().center())
    controller.controls["padValue"].setValue(19)
    assert view.pixmap().isNull()
    wait(lambda: controller._previewSnapshot is not None)
    assert view.zoom == 20
    actual = view.mapToScene(view.viewport().rect().center())
    assert actual.x() == pytest.approx(center.x(), abs=.1)
    assert actual.y() == pytest.approx(center.y(), abs=.1)


@pytest.fixture
def roi(runtime, tmp_path, monkeypatch):
    manifest = json.loads(Path("src/emo_master/plugins/builtins/roi/manifest.json").read_text(encoding="utf-8"))
    payload = project(manifest["operatorId"])
    manager = OperatorEditorManager(runtimeClient=RuntimeClient(runtime), settingsStore=_Settings(),
        applyParams=lambda *_: pytest.fail("pure preview must not Apply"), appendLog=lambda *_: None,
        getPreviewProject=lambda _: payload, cacheRoot=tmp_path / "cache")
    window = manager.open(projectId="draft", workflowId="main", nodeId="node", operatorId=manifest["operatorId"],
        displayName=manifest["displayName"], schema=manifest["paramSchema"], values={},
        operatorDefinition=dict(version=manifest["version"], editorSpec=manifest["editor"]))
    controller = window._controller
    image = tmp_path / "gradient.png"
    image.write_bytes(png())
    monkeypatch.setattr(support.QFileDialog, "getOpenFileName", lambda *_: (str(image), ""))
    try:
        controller.controls["x"].setValue(2)
        controller.controls["y"].setValue(3)
        controller.controls["width"].setValue(8)
        controller.controls["height"].setValue(7)
        QTest.mouseClick(controller.localImageButton, Qt.LeftButton)
        wait(lambda: "预览已刷新" in window._statusLabel.text())
        yield window, controller, payload
    finally:
        manager.closeAll()
        wait(lambda: not controller._retirementThread.is_alive())
    assert not runtime.previewAssetStore._assets


def testAllFourImagesHaveFullPixelViewerAndSamePreviewArchive(roi, tmp_path, monkeypatch):
    from emo_master.plugins.builtins.roi import preview_result
    window, controller, _ = roi
    assert set(controller.outputViews) == PORTS
    snapshot = controller._previewSnapshot
    assert snapshot is not None
    meta = json.loads(snapshot.metadataJson)
    assert meta["context"]["draftSessionId"] == window.context._pureDraftSessionId
    assert meta["requestId"] and meta["generation"] == controller._generation
    assert meta["inputAssetId"] == controller.currentAssetId
    assert meta["input"]["sha256"] == hashlib.sha256(png()).hexdigest()
    assert meta["requestedParams"]["width"] == 8
    assert meta["resolvedParams"] is None and "executionId" not in meta
    assert set(json.loads(snapshot.outputsJson)) >= {"croppedFrame", "maskedFrame", "roi"}
    archive = tmp_path / "ROI证据.zip"
    monkeypatch.setattr(support.QFileDialog, "getSaveFileName", lambda *_: (str(archive), ""))
    controller.exportArchiveAction.trigger()
    with zipfile.ZipFile(archive) as data:
        manifest = json.loads(data.read("result.json"))
        assert manifest["preview"] == meta
        assert manifest["outputs"] == json.loads(snapshot.outputsJson)
        assert {row["port"] for row in manifest["images"]} == PORTS
        for row in manifest["images"]:
            raw, mime = window.context.downloadPreviewAsset(row["assetId"])
            exported = data.read(row["file"])
            assert mime == "image/png" and exported == raw
            assert hashlib.sha256(exported).hexdigest() == row["sha256"]
            image = cv2.imdecode(np.frombuffer(exported, np.uint8), cv2.IMREAD_UNCHANGED)
            assert image.shape[:2] == (row["height"], row["width"])
    # The actual viewer QLabel is original pixel size, not the thumbnail size.
    controller.outputTabs.setCurrentWidget(controller.outputViews["maskedImage"].parentWidget())
    QTest.mouseClick(controller.viewImageButton, Qt.LeftButton)
    viewer = controller._resultViewers[-1]
    label = viewer.findChild(QScrollArea).widget()
    assert label.pixmap().size() == label.size()
    assert label.width() == 24 and label.height() == 16
    assert meta["requestId"] in viewer.windowTitle()
    assert preview_result is not None


def testParameterChangeImmediatelyInvalidatesExportsAndOpenViewer(roi, tmp_path, monkeypatch):
    _, controller, _ = roi
    old = controller._previewSnapshot
    controller._viewFullImage()
    viewer = controller._resultViewers[-1]
    path = tmp_path / "must-not-overwrite.png"
    path.write_bytes(b"existing")

    def choose(*_):
        controller.controls["width"].setValue(5)
        return str(path), ""

    monkeypatch.setattr(support.QFileDialog, "getSaveFileName", choose)
    controller._exportImage()
    assert path.read_bytes() == b"existing"
    assert controller._previewSnapshot is None and not controller.exportImageButton.isEnabled()
    assert not viewer.isVisible()
    wait(lambda: controller._previewSnapshot is not None)
    current = controller._previewSnapshot
    assert current is not old and json.loads(current.metadataJson)["requestedParams"]["width"] == 5
    assert json.loads(old.metadataJson)["requestedParams"]["width"] == 8


def testResultTabsExportSelectedPixelsWithoutRecomputing(roi, tmp_path, monkeypatch):
    from PySide2.QtWidgets import QPlainTextEdit
    _, controller, _ = roi
    snapshot = controller._previewSnapshot
    params = controller.collectParams()
    generation = controller._generation
    for index in range(controller.outputTabs.count()):
        bar = controller.outputTabs.tabBar()
        port = bar.tabData(index)
        QTest.mouseClick(controller.outputModeButtons[port], Qt.LeftButton)
        assert controller.outputTabs.currentWidget().isAncestorOf(controller.outputViews[port])
        path = tmp_path / (port + ".png")
        monkeypatch.setattr(support.QFileDialog, "getSaveFileName", lambda *_, target=path: (str(target), ""))
        QTest.mouseClick(controller.exportImageButton, Qt.LeftButton)
        assert path.read_bytes() == next(image.content for image in snapshot.images if image.port == port)
        assert controller._previewSnapshot is snapshot
        assert controller._generation == generation
        assert controller.collectParams() == params
    controller.viewResultAction.trigger()
    details = controller._resultViewers[-1].findChild(QPlainTextEdit)
    assert json.loads(details.toPlainText())["preview"] == json.loads(snapshot.metadataJson)


def testExpandingPreviewRecordKeepsImagesAndParametersUsable(roi, designerApplication):
    window, controller, _ = roi
    designerApplication.processEvents()
    window.resize(980, 720)
    before = controller.collectParams()
    snapshot = controller._previewSnapshot
    toggle = window.findChild(QToolButton, "roiPreviewDetailsToggle")
    QTest.mouseClick(toggle, Qt.LeftButton)
    designerApplication.processEvents()
    assert controller.resultIdentityLabel.isVisible()
    assert window.width() == 980
    assert controller.canvas.width() >= 240
    assert controller.outputTabs.width() >= 240
    assert controller.collectParams() == before
    assert controller._previewSnapshot is snapshot
    QTest.mouseClick(toggle, Qt.LeftButton)
    assert not controller.resultIdentityLabel.isVisible()


def testDraftChangeAndRuntimeReconnectCannotExportFrozenPixelsAsCurrent(roi, tmp_path, monkeypatch):
    window, controller, payload = roi
    path = tmp_path / "old.json"
    path.write_bytes(b"existing")
    monkeypatch.setattr(support.QFileDialog, "getSaveFileName", lambda *_: (str(path), ""))
    payload["project"]["name"] = "different draft"
    controller._exportJson()
    assert path.read_bytes() == b"existing"
    assert controller._previewSnapshot is None
    assert "变更" in window._statusLabel.text()


def testPartialImageDownloadFailureDoesNotPublishMixedResult(roi, monkeypatch):
    window, controller, _ = roi
    reply = controller._future.result()
    assets = {row.port: row.asset_id for row in reply.assets}
    original = window.context.downloadPreviewAsset

    def download(asset):
        if asset == assets["maskedImage"]:
            raise OSError("injected read failure")
        return original(asset)

    monkeypatch.setattr(window.context, "downloadPreviewAsset", download)
    controller.handlePreviewResult(json.loads(reply.outputs_json), assets)
    assert controller._previewSnapshot is None
    assert not controller.exportArchiveAction.isEnabled()
    assert all(label.pixmap() is None or label.pixmap().isNull() for label in controller.outputViews.values())
    assert "injected read failure" in window._statusLabel.text()


@pytest.mark.parametrize("invalid", ["reconnect", "expired", "close_during_picker"])
def testReconnectExpiryAndCloseDuringExportNeverReplaceDestination(roi, tmp_path, monkeypatch, invalid):
    window, controller, _ = roi
    target = tmp_path / "old.zip"
    target.write_bytes(b"existing")
    # Model the user's explicit discard answer. An unattended native modal
    # prompt inside a simulated picker is not a completed close operation.
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Discard)

    def choose(*_):
        if invalid == "reconnect":
            window.context._runtimeClient.runtimeService = object()
        elif invalid == "expired":
            window.context._pureDraftTouched -= 301
        else:
            window.close()
        return str(target), ""

    monkeypatch.setattr(support.QFileDialog, "getSaveFileName", choose)
    controller._exportArchive()
    assert target.read_bytes() == b"existing"
    assert controller._previewSnapshot is None


def testPngSuffixAndJsonAreFromOriginalPixelsNotThumbnail(roi, tmp_path, monkeypatch):
    _, controller, _ = roi
    snapshot = controller._previewSnapshot
    controller.outputTabs.setCurrentWidget(controller.outputViews["maskedImage"].parentWidget())
    target = tmp_path / "原始像素"
    monkeypatch.setattr(support.QFileDialog, "getSaveFileName", lambda *_: (str(target), ""))
    controller._exportImage()
    assert target.with_suffix(".png").read_bytes() == next(i.content for i in snapshot.images if i.port == "maskedImage")
    controller._exportJson()
    assert json.loads(target.with_suffix(".json").read_bytes())["preview"] == json.loads(snapshot.metadataJson)


def testWriteFailureIsVisibleAndDoesNotDestroyCurrentSelection(roi, tmp_path, monkeypatch):
    from emo_master.plugins.builtins.roi import preview_result
    window, controller, _ = roi
    target = tmp_path / "existing.png"
    target.write_bytes(b"existing")
    snapshot = controller._previewSnapshot
    monkeypatch.setattr(support.QFileDialog, "getSaveFileName", lambda *_: (str(target), ""))
    def failed(*_):
        raise OSError("injected disk error")
    monkeypatch.setattr(preview_result.os, "replace", failed)
    controller._exportImage()
    assert target.read_bytes() == b"existing" and controller._previewSnapshot is snapshot
    assert "injected disk error" in window._statusLabel.text()
    assert controller.exportImageButton.isEnabled()


@pytest.mark.parametrize("params", [
    dict(roiType="bbox", x=2, y=3, width=8, height=7),
    dict(roiType="rotatedBox", centerX=10, centerY=8, width=8, height=6, angleDegrees=25),
    dict(roiType="polygon", points=[dict(x=2, y=2), dict(x=18, y=3), dict(x=10, y=12)]),
])
def testEveryGeometryHasAllFourOriginalPixelOutputs(roi, params):
    from emo_master.plugins.builtins.roi.operator import RoiOperator
    _, controller, _ = roi
    controller.loadParams(params)
    controller.schedulePreview(markDirty=False)
    wait(lambda: controller._previewSnapshot is not None)
    snapshot = controller._previewSnapshot
    image = cv2.imdecode(np.frombuffer(png(), np.uint8), cv2.IMREAD_UNCHANGED)
    expected = RoiOperator().executeNode(dict(image=image), json.loads(snapshot.metadataJson)["requestedParams"], {})
    assert expected["status"] == "ok"
    assert {row.port for row in snapshot.images} == PORTS
    for row in snapshot.images:
        actual = cv2.imdecode(np.frombuffer(row.content, np.uint8), cv2.IMREAD_UNCHANGED)
        assert np.array_equal(actual, expected["outputs"][row.port]), row.port


def testAtomicExportPreservesDestinationAndCleansTemporaryOnFailure(tmp_path, monkeypatch):
    from emo_master.plugins.builtins.roi import preview_result
    target = tmp_path / "result.png"
    target.write_bytes(b"existing")

    def broken(_source, _target):
        raise OSError("disk full")

    monkeypatch.setattr(preview_result.os, "replace", broken)
    with pytest.raises(OSError, match="disk full"):
        preview_result.atomicWrite(target, lambda stream: stream.write(b"new"), lambda: None)
    assert target.read_bytes() == b"existing" and list(tmp_path.iterdir()) == [target]


def testAtomicExportRechecksIdentityBeforeReplacingDestination(tmp_path):
    from emo_master.plugins.builtins.roi.preview_result import atomicWrite
    target = tmp_path / "result.json"
    target.write_bytes(b"existing")

    def invalid():
        raise ValueError("selection changed")

    with pytest.raises(ValueError, match="selection changed"):
        atomicWrite(target, lambda stream: stream.write(b"new"), invalid)
    assert target.read_bytes() == b"existing" and list(tmp_path.iterdir()) == [target]
