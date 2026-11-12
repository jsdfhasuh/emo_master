"""ROI layout and localized controls through the Designer editor entrypoint."""

import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide2.QtCore import QPoint, QPointF, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QScrollArea, QToolButton

from emo_master.apps.designer.operator_editors import OperatorEditorManager
from emo_master.plugins.builtins.roi.editor import OUTPUT_TITLES
from emo_master.plugins.builtins.roi.operator import RoiOperator
from tests.designer.test_builtin_operator_editor_ui import _Runtime, _Settings
from tests.designer.test_operator_debug_window import wait
from tests.runtime.test_global_variables import variable
from tests.designer.test_image_view import drag, wheel


@pytest.fixture
def roiEditor(tmp_path, designerApplication):
    root = Path("src/emo_master/plugins/builtins/roi")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    applied = []
    manager = OperatorEditorManager(
        runtimeClient=_Runtime({manifest["operatorId"]: (root / manifest["editor"]["uiResource"]).read_bytes()}),
        settingsStore=_Settings(), applyParams=lambda _key, params: applied.append(params) or True,
        appendLog=lambda *_: None, cacheRoot=tmp_path / "cache",
    )
    window = manager.open(
        projectId="draft", workflowId="main", nodeId="roi", operatorId=manifest["operatorId"],
        displayName="区域提取", schema=manifest["paramSchema"],
        values=dict(roiType="bbox", x=40, y=30, width=180, height=120, interpolation="linear", padValue=12),
        variableDefinitions=lambda: {"mode": variable("bbox", "string")}, variableBindings=[],
        operatorDefinition=dict(version=manifest["version"], editorSpec=manifest["editor"]),
    )
    try:
        yield window, window._controller, applied
    finally:
        manager.closeAll()
        wait(lambda: not window._controller._retirementThread.is_alive())


@pytest.mark.parametrize("kind,title", [("bbox", "矩形"), ("rotatedBox", "旋转矩形"), ("polygon", "多边形")])
@pytest.mark.parametrize("interpolation,title2", [("nearest", "最近邻插值"), ("linear", "双线性插值"), ("cubic", "双三次插值")])
def testChineseChoicesRoundTripInternalValues(roiEditor, kind, title, interpolation, title2):
    window, controller, applied = roiEditor
    params = controller.collectParams()
    params.update(roiType=kind, interpolation=interpolation,
                  points=[dict(x=0, y=0), dict(x=10, y=0), dict(x=0, y=10)])
    controller.loadParams(params)
    assert controller.controls["roiType"].currentText() == title
    assert controller.controls["interpolation"].currentText() == title2
    assert controller.collectParams() == params
    assert controller.canvas._roiType == kind
    assert controller.controls["x"].isHidden() == (kind != "bbox")
    assert controller.controls["centerX"].isHidden() == (kind != "rotatedBox")
    assert controller.controls["points"].isHidden() == (kind != "polygon")
    assert window.applyChanges(), window._statusLabel.text()
    assert applied[-1] == params
    controller.loadParams(applied[-1])
    assert controller.collectParams() == params


def testCollapsedSettingsAndBindingsRetainValues(roiEditor):
    window, controller, _ = roiEditor
    original = controller.collectParams()
    advanced = window.findChild(QToolButton, "roiAdvancedSectionToggle")
    bindings = window.findChild(QToolButton, "roiBindingsSectionToggle")
    assert not advanced.isChecked() and not bindings.isChecked()
    assert not controller.controls["padValue"].isVisible()
    assert not window._bindingScroll.isVisible()
    QTest.mouseClick(advanced, Qt.LeftButton)
    assert controller.controls["padValue"].isVisible()
    QTest.mouseClick(bindings, Qt.LeftButton)
    assert window._bindingScroll.isVisible()
    source = window._bindingPanel.fields[("roiType",)]
    source.setCurrentIndex(source.findData("mode"))
    before = window._bindingPanel.bindings()
    QTest.mouseClick(advanced, Qt.LeftButton)
    QTest.mouseClick(bindings, Qt.LeftButton)
    assert controller.collectParams() == original
    assert window._bindingPanel.bindings() == before
    assert not controller.controls["points"].isHidden()


@pytest.mark.parametrize("target", ["source", "croppedImage", "maskedImage", "croppedMask", "fullMask"])
def testBothPreviewsZoomAndPanWithoutEditingOrReexecuting(roiEditor, designerApplication, target):
    from PySide2.QtGui import QPixmap
    window, controller, _ = roiEditor
    designerApplication.processEvents()
    view = controller.canvas if target == "source" else controller.outputViews[target]
    if target != "source":
        controller.outputTabs.setCurrentWidget(view.parentWidget())
    image = QPixmap(640, 480)
    image.fill(Qt.green)
    view.setImage(image)
    view.setZoom(2)
    designerApplication.processEvents()
    params, generation, dirty = controller.collectParams(), controller._generation, window.isDirty()
    wheel(designerApplication, view)
    assert view.zoom > 2
    if target == "source":
        window.findChild(QToolButton, "roiPanButton").click()
    before = view.mapToScene(view.viewport().rect().center())
    drag(designerApplication, view, Qt.LeftButton, QPoint(140, 120), QPoint(110, 90))
    assert view.mapToScene(view.viewport().rect().center()) != before
    before = view.mapToScene(view.viewport().rect().center())
    drag(designerApplication, view, Qt.MiddleButton, QPoint(140, 120), QPoint(110, 90))
    assert view.mapToScene(view.viewport().rect().center()) != before
    prefix = "roiSource" if target == "source" else "roiResult"
    window.findChild(QToolButton, prefix + "ActualButton").click()
    assert view.zoom == 1
    window.findChild(QToolButton, prefix + "FitButton").click()
    assert view.fitting
    assert controller.collectParams() == params
    assert controller._generation == generation
    assert window.isDirty() == dirty


@pytest.mark.parametrize("kind", ["bbox", "rotatedBox", "polygon"])
def testDrawingUsesImageCoordinatesAfterZoomAndPan(roiEditor, designerApplication, kind):
    from PySide2.QtGui import QPixmap
    window, controller, _ = roiEditor
    params = controller.collectParams()
    params.update(roiType=kind, points=[dict(x=0, y=0), dict(x=10, y=0), dict(x=0, y=10)], angleDegrees=25)
    controller.loadParams(params)
    image = QPixmap(640, 480)
    image.fill(Qt.green)
    canvas = controller.canvas
    canvas.setImage(image)
    designerApplication.processEvents()
    canvas.setZoom(2)
    canvas.centerOn(200, 150)
    controller._setTool("polygon" if kind == "polygon" else "bbox")
    # Middle-button panning remains available without leaving selection mode.
    drag(designerApplication, canvas, Qt.MiddleButton, QPoint(140, 120), QPoint(120, 100))
    points = [QPointF(180, 140), QPointF(220, 170), QPointF(180, 170)]
    start, end = [canvas.mapFromScene(point) for point in points[:2]]
    assert canvas.viewport().rect().contains(start) and canvas.viewport().rect().contains(end)
    if kind == "polygon":
        for point in points:
            QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(point))
        assert len(controller.collectParams().get("regions", [])) == 0
        QTest.keyClick(canvas, Qt.Key_Return)
    else:
        drag(designerApplication, canvas, Qt.LeftButton, start, end)
    params = controller.collectParams()
    assert params["roiType"] == "regions"
    assert len(params["regions"]) == 2
    actual = params["regions"][-1]
    if kind == "bbox":
        assert actual["x"] == pytest.approx(180, abs=1)
        assert actual["y"] == pytest.approx(140, abs=1)
    elif kind == "rotatedBox":
        assert actual["centerX"] == pytest.approx(200, abs=1)
        assert actual["centerY"] == pytest.approx(155, abs=1)
        assert actual["angleDegrees"] == 25
    else:
        for actualPoint, expected in zip(actual["points"], points):
            assert actualPoint["x"] == pytest.approx(expected.x(), abs=1)
            assert actualPoint["y"] == pytest.approx(expected.y(), abs=1)
        assert len(actual["points"]) == 3
    if kind != "polygon":
        assert actual["width"] == pytest.approx(40, abs=1)
        assert actual["height"] == pytest.approx(30, abs=1)


@pytest.mark.parametrize("width", [980, 1280])
def testSideBySideLayoutAndNamedResults(roiEditor, designerApplication, width):
    window, controller, _ = roiEditor
    window.show()
    # Let the application's initial screen-fit timer finish before testing sizes.
    designerApplication.processEvents()
    window.resize(width, 720)
    image = np.full((300, 400, 3), 220, np.uint8)
    cv2.rectangle(image, (40, 30), (220, 150), (65, 145, 190), -1)
    cv2.circle(image, (100, 90), 28, (50, 65, 70), -1)
    cv2.rectangle(image, (265, 180), (355, 260), (90, 155, 85), -1)
    controller.onSourceImage(cv2.imencode(".png", image)[1].tobytes(), "image/png")
    outputs = RoiOperator().executeNode({"image": image}, controller.collectParams(), {})["outputs"]
    for port, label in controller.outputViews.items():
        label.setImageBytes(cv2.imencode(".png", outputs[port])[1].tobytes())
    designerApplication.processEvents()
    assert window.width() == width
    canvas = controller.canvas
    tabs = controller.outputTabs
    params = window.findChild(QScrollArea, "roiParametersScroll")
    assert canvas.mapTo(window, QPoint()).x() + canvas.width() <= tabs.mapTo(window, QPoint()).x()
    assert tabs.mapTo(window, QPoint()).x() + tabs.width() <= params.mapTo(window, QPoint()).x()
    assert canvas.height() >= 280 and tabs.height() >= 280
    assert params.width() >= 200
    original = controller.collectParams()
    for index in range(tabs.count()):
        tabs.setCurrentIndex(index)
        designerApplication.processEvents()
        port = tabs.tabBar().tabData(index)
        assert tabs.tabText(index) == OUTPUT_TITLES[port]
        assert tabs.currentWidget().isAncestorOf(controller.outputViews[port])
        assert tabs.currentWidget().isVisible()
        assert sum(label.isVisible() for label in controller.outputViews.values()) == 1
        assert not controller.outputViews[port].pixmap().isNull()
    assert controller.collectParams() == original
    tabs.setCurrentWidget(controller.outputViews["croppedImage"].parentWidget())
    designerApplication.processEvents()
    screenshots = os.environ.get("EMO_ROI_SCREEN_DIR")
    if screenshots:
        target = Path(screenshots)
        target.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(target / f"roi-layout-{width}.png"))
