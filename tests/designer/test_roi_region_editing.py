"""Native Qt events against the registered ROI editor, without physical devices."""
import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide2.QtCore import QPointF, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QSlider

from tests.designer.test_roi_editor_layout import roiEditor as roiEditor
from tests.designer.test_image_view import drag
from emo_master.plugins.builtins.roi.operator import RoiOperator


def source(controller, application):
    image = np.full((300, 400, 3), 205, np.uint8)
    cv2.rectangle(image, (40, 30), (220, 150), (65, 145, 190), -1)
    cv2.circle(image, (120, 90), 28, (50, 65, 70), -1)
    controller.onSourceImage(cv2.imencode(".png", image)[1].tobytes(), "image/png")
    application.processEvents()
    return image


def click(canvas, x, y):
    QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(x, y)))


def triangle(controller):
    controller._setTool("polygon")
    for x, y in [(60, 50), (180, 50), (110, 140)]:
        click(controller.canvas, x, y)


@pytest.mark.parametrize("completion", ["enter", "start", "button"])
def testDraftPreviewsAfterThreePointsAndCompletionAddsNoDuplicate(roiEditor, designerApplication, completion):
    window, c, applied = roiEditor
    image = source(c, designerApplication)
    original = c.collectParams()
    triangle(c)
    assert c.collectParams() == original
    assert c.regionList.count() == 1
    assert c.canvas.draftError() == ""
    assert c.finishButton.isEnabled()
    preview = c.collectPreviewParams()
    assert len(preview["regions"]) == 2
    assert preview["regions"][-1]["purpose"] == "exclude"
    assert len(preview["regions"][-1]["points"]) == 3
    outputs = RoiOperator().executeNode({"image": image}, preview, {})["outputs"]
    assert outputs["fullMask"][70, 100] == 0
    assert not window.applyChanges()
    assert not applied
    if completion == "enter":
        QTest.keyClick(c.canvas, Qt.Key_Return)
    elif completion == "start":
        click(c.canvas, 60, 50)
    else:
        QTest.mouseClick(c.finishButton, Qt.LeftButton)
    params = c.collectParams()
    assert c.canvas.draft is None
    assert len(params["regions"]) == c.regionList.count() == 2
    assert len(params["regions"][-1]["points"]) == 3
    assert window.applyChanges(), window._statusLabel.text()
    assert applied[-1] == params
    c.loadParams(params)
    assert c.collectParams() == params


def testBackspaceDoubleClickAndEscapeDoNotCommit(roiEditor, designerApplication):
    _, c, _ = roiEditor
    source(c, designerApplication)
    original = c.collectParams()
    triangle(c)
    click(c.canvas, 60, 120)
    assert len(c.canvas.draft["points"]) == 4
    QTest.keyClick(c.canvas, Qt.Key_Backspace)
    assert len(c.canvas.draft["points"]) == 3
    QTest.mouseDClick(c.canvas.viewport(), Qt.LeftButton, pos=c.canvas.mapFromScene(QPointF(110, 140)))
    assert len(c.canvas.draft["points"]) == 3
    assert c.regionList.count() == 1
    QTest.keyClick(c.canvas, Qt.Key_Escape)
    assert c.canvas.draft is None
    assert c.collectParams() == c.collectPreviewParams() == original


def testTooFewAndSelfCrossingPointsCannotFinish(roiEditor, designerApplication):
    _, c, _ = roiEditor
    source(c, designerApplication)
    original = c.collectParams()
    c._setTool("polygon")
    for point in [(60, 50), (180, 130), (60, 130), (180, 50)]:
        click(c.canvas, *point)
    assert c.canvas.draftError()
    assert not c.finishButton.isEnabled()
    QTest.keyClick(c.canvas, Qt.Key_Return)
    assert c.canvas.draft is not None
    assert c.collectPreviewParams() == original
    c.canvas.setMode("select")
    c._setTool("polygon")
    click(c.canvas, 60, 50)
    click(c.canvas, 180, 130)
    assert not c.finishButton.isEnabled()
    assert c.collectPreviewParams() == original


def testVertexDragAndUndoUseOriginalPixelCoordinates(roiEditor, designerApplication):
    _, c, _ = roiEditor
    source(c, designerApplication)
    triangle(c)
    c.canvas.finishDrawing()
    original = c.collectParams()
    c.canvas.setZoom(2)
    c.canvas.centerOn(110, 80)
    designerApplication.processEvents()
    drag(designerApplication, c.canvas, Qt.LeftButton,
         c.canvas.mapFromScene(QPointF(60, 50)), c.canvas.mapFromScene(QPointF(75, 60)))
    moved = c.collectParams()["regions"][-1]["points"]
    assert moved[0]["x"] == pytest.approx(75, abs=1)
    assert moved[0]["y"] == pytest.approx(60, abs=1)
    assert moved[1:] == original["regions"][-1]["points"][1:]
    c._undo()
    assert c.collectParams() == original
    c._undo()
    assert c.collectParams()["roiType"] == "bbox"


def testEnableDeletePurposeAndUndoRoundTrip(roiEditor, designerApplication):
    _, c, _ = roiEditor
    source(c, designerApplication)
    triangle(c)
    c.canvas.finishDrawing()
    original = c.collectParams()
    c.regionList.item(1).setCheckState(Qt.Unchecked)
    assert c.collectParams()["regions"][1]["enabled"] is False
    c._undo()
    assert c.collectParams() == original
    c.purposeCombo.setCurrentIndex(c.purposeCombo.findData("keep"))
    assert c.collectParams()["regions"][1]["purpose"] == "keep"
    c._undo()
    c._deleteRegion()
    assert len(c.collectParams()["regions"]) == 1
    c._undo()
    assert c.collectParams() == original
    c._selectRegion(0)
    c._deleteRegion()
    c._deleteRegion()
    assert c.collectParams()["regions"] == []
    assert c.validate() is None


def testSelectionOpacityAndViewChangesDoNotEditParameters(roiEditor, designerApplication):
    window, c, _ = roiEditor
    source(c, designerApplication)
    triangle(c)
    c.canvas.finishDrawing()
    before, generation = c.collectParams(), c._generation
    c._selectRegion(0)
    window.findChild(QSlider, "regionOpacity").setValue(45)
    assert c.canvas.opacity == .45
    assert c.collectParams() == before
    assert c._generation == generation


def testGlobalParametersUndoAndEmptySelection(roiEditor, designerApplication):
    _, c, _ = roiEditor
    source(c, designerApplication)
    before = c.collectParams()
    c.controls["padValue"].setValue(60)
    c._undo()
    assert c.collectParams() == before
    c._deleteRegion()
    c.controls["padValue"].setValue(70)
    assert c.collectParams()["padValue"] == 70
    c._undo()
    assert c.collectParams()["padValue"] == before["padValue"]


def testVertexTableEditsParametersWithoutRawJson(roiEditor, designerApplication):
    _, c, _ = roiEditor
    source(c, designerApplication)
    triangle(c)
    c.canvas.finishDrawing()
    table = c.controls["points"]
    assert table.rowCount() == 3
    assert table.horizontalHeaderItem(0).text() == "X"
    original = c.collectParams()
    table.item(0, 0).setData(Qt.EditRole, 70.)
    assert c.collectParams()["regions"][-1]["points"][0]["x"] == 70.
    c._undo()
    assert c.collectParams() == original


def testChangingTypeDoesNotUseAnotherRegionsVertices(roiEditor, designerApplication):
    _, c, _ = roiEditor
    source(c, designerApplication)
    triangle(c)
    c.canvas.finishDrawing()
    other = c.collectParams()["regions"][1]
    c._selectRegion(0)
    c.controls["roiType"].setCurrentIndex(c.controls["roiType"].findData("polygon"))
    regions = c.collectParams()["regions"]
    assert regions[0]["points"] == [dict(x=40, y=30), dict(x=220, y=30), dict(x=220, y=150), dict(x=40, y=150)]
    assert regions[1] == other


def testCancelledDraftDoesNotLeakPointsIntoSubsequentParameterEdit(roiEditor, designerApplication):
    _, c, _ = roiEditor
    source(c, designerApplication)
    before = c.collectParams()
    triangle(c)
    c.canvas.setMode("select")
    c.controls["padValue"].setValue(77)
    assert c.collectParams() == {**before, "padValue": 77}


def testCompositeBindingConflictIsVisibleAndDoesNotEraseDraft(roiEditor, designerApplication):
    window, c, _ = roiEditor
    source(c, designerApplication)
    c.parameterBindingsForDisplay = lambda: [dict(parameterPath=["x"], variableId="offset")]
    assert c.collectParamsForSave() is not None
    triangle(c)
    c._startPreview()
    assert c.canvas.draft is not None
    assert "区域配置" in window._statusLabel.text()
    c.canvas.finishDrawing()
    assert c.collectParamsForSave() is None
    c.parameterBindingsForDisplay = lambda: [dict(parameterPath=["regions"], variableId="regions")]
    assert c.collectParamsForSave() is not None


def testTemporaryResultsAndCompletedWindowScreenshot(roiEditor, designerApplication):
    window, c, _ = roiEditor
    image = source(c, designerApplication)
    triangle(c)
    params = c.collectPreviewParams()
    outputs = RoiOperator().executeNode({"image": image}, params, {})["outputs"]
    for port, view in c.outputViews.items():
        view.setImageBytes(cv2.imencode(".png", outputs[port])[1].tobytes())
    c._resultTabChanged()
    window.resize(1100, 760)
    designerApplication.processEvents()
    assert all(not view.pixmap().isNull() for view in c.outputViews.values())
    if os.environ.get("EMO_ROI_SCREEN_DIR"):
        target = Path(os.environ["EMO_ROI_SCREEN_DIR"])
        target.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(target / "roi-region-draft.png"))
        c.canvas.finishDrawing()
        for port, view in c.outputViews.items():
            view.setImageBytes(cv2.imencode(".png", outputs[port])[1].tobytes())
        designerApplication.processEvents()
        assert window.grab().save(str(target / "roi-region-complete.png"))
