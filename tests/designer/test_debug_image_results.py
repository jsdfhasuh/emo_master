from copy import deepcopy

import numpy as np
import pytest
from PySide2.QtCore import Qt
from shiboken2 import isValid

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from emo_master.apps.designer.ui.workflow_debug_window import WorkflowDebugWindow
from emo_master.apps.runtime.operator_debug.assets import imageBytes
from tests.designer.test_operator_debug_window import editorFor, wait
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_workflow_debug_control import nestedProject


def uploadImage(dialog, pixel):
    raw = imageBytes(np.full((120, 180, 3), pixel, np.uint8))
    wire = dialog.connection.upload(raw, "image/png", {"kind": "fixture"})
    return {"assetId": wire["assetRef"], "mime": "image/png"}


@pytest.mark.parametrize("width", [620, 1000])
def testOperatorPrefersImagesExplainsMissingOutputAndKeepsHistoricalIdentity(runtime, tmp_path, record_property, width):
    editor, payload, before, calls = editorFor(runtime, "vision.inference.yolo")
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.resize(width, 660)
    dialog.show()
    try:
        wait(lambda: dialog.run.isEnabled())
        dialog.timer.stop()
        wait(lambda: not dialog.polling)
        crop, overlay = uploadImage(dialog, 60), uploadImage(dialog, 190)
        message = "此次执行未启用“绘制叠加图”，未生成 overlay。"
        dialog.records = [dict(executionId="new", status="SUCCEEDED", rawParams={"drawOverlay": False},
            outputs={"detections": [], "frame": {}}, outputAssets={"croppedImage": crop},
            diagnostics={"imageOutputMessages": {"overlay": message}}),
            dict(executionId="old", status="SUCCEEDED", rawParams={"drawOverlay": True},
                 outputs={"detections": []}, outputAssets={"overlay": overlay})]
        original = deepcopy(dialog.records)
        dialog.versions.addItems(["Current", "History"])
        dialog.tabs.setCurrentIndex(1)
        wait(lambda: dialog.pixmap is not None and not dialog.busy)
        assert dialog.selectedOutputPort() == "croppedImage"
        assert "裁剪图" in dialog.outputPorts.currentText()
        assert dialog.pixmap.width() == 180 and dialog.image.isVisible()
        assert dialog.pixmap.toImage().pixelColor(0, 0).red() == 60
        capture = tmp_path / "debug-image-result.png"
        assert dialog.grab().save(str(capture))
        record_property("screenshot", str(capture))
        dialog.outputPorts.setCurrentIndex(dialog.outputPorts.findData("overlay", Qt.UserRole + 1))
        assert dialog.outputMessage.isVisible() and dialog.outputMessage.text() == message
        assert dialog.pixmap is None and dialog.exportSelection() is None
        assert not dialog.artifacts.export.isEnabled()
        dialog.versions.setCurrentIndex(1)
        wait(lambda: dialog.pixmap is not None and not dialog.busy)
        assert dialog.selectedOutputPort() == "overlay" and not dialog.outputMessage.isVisible()
        assert dialog.pixmap.toImage().pixelColor(0, 0).red() == 190
        assert dialog.exportSelection()["identity"]["rawParams"] == {"drawOverlay": True}
        dialog.versions.setCurrentIndex(0)
        assert dialog.outputMessage.text() == message and dialog.pixmap is None
        assert dialog.records == original and payload == before and calls == []
        dialog.records = [dict(executionId="failed", status="FAILED", code="E_PARAM_INVALID", outputs={})]
        dialog.showResult()
        assert dialog.outputMessage.isVisible() and "失败" in dialog.outputMessage.text()
        assert not dialog.image.isVisible() and not dialog.outputTree.isVisible()
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
        editor.forceClose()


def testWorkflowSwitchesFromInputToNewOutputImage(runtime):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    try:
        wait(lambda: dialog.startButton.isEnabled())
        dialog.timer.stop()
        wait(lambda: not dialog.polling and not dialog.busy)
        source, result = uploadImage(dialog, 20), uploadImage(dialog, 200)
        dialog.display(dict(snapshotId="before", inputs={"value": 1}, inputsAssets={"image": source}, outputs={}))
        wait(lambda: dialog.pixmap is not None and not dialog.busy)
        assert dialog.ports.currentData()[0] == "inputs"
        dialog.display(dict(snapshotId="after", inputs={"value": 1}, inputsAssets={"image": source},
                            outputs={"count": 2}, outputsAssets={"overlay": result}))
        wait(lambda: dialog.pixmap is not None and not dialog.busy)
        assert dialog.ports.currentData()[0] == "outputs"
        assert dialog.ports.currentData()[2] == "overlay"
        assert "叠加图" in dialog.ports.currentText()
        assert dialog.pixmap.toImage().pixelColor(0, 0).red() == 200
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
