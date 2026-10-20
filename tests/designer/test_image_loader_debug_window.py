from copy import deepcopy

import cv2
import numpy as np
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from shiboken2 import isValid

from emo_master.apps.designer.operator_editors import EditorContext, EditorKey
from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from tests.designer.test_operator_debug_window import wait
from tests.runtime.operator_debug_fixture import project
from tests.runtime.test_operator_debug_rpc import runtime as runtime


def testImageReadShowsCurrentDraftPixelsAndRuntimeMissingFile(runtime, tmp_path, record_property):
    operatorId = "vision.io.image_loader"
    schema = runtime.pluginScanResult.activeOperators[operatorId].manifest.paramSchema
    payload = project(operatorId)
    before = deepcopy(payload)
    applied = []
    path = tmp_path / "runtime-image.png"
    pixels = np.full((240, 400, 3), 235, np.uint8)
    pixels[30:210, 30:180] = (45, 100, 210)
    pixels[30:130, 210:370] = (160, 120, 35)
    pixels[155:210, 210:370] = (40, 165, 75)
    assert cv2.imwrite(str(path), pixels)
    context = EditorContext(key=EditorKey("draft", "main", "node"), operatorId=operatorId,
        version="1.1.0", previewMode="none", paramSchema=schema, runtimeClient=RuntimeClient(runtime),
        applyParams=lambda *_: applied.append(True), appendLog=lambda *_: None,
        getPreviewProject=lambda _: payload, variableDefinitions=lambda: {})
    editor = OperatorWorkspaceWindow(key=context.key, title="Image Loader", context=context,
        schema=schema, values={"imagePath": str(path), "colorMode": "color"})
    editor.show()
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(lambda: dialog.run.isEnabled())
        assert not dialog.records
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
        assert dialog.records[0]["status"] == "SUCCEEDED"
        dialog.outputPorts.setCurrentIndex(dialog.outputPorts.findText("image"))
        wait(lambda: dialog.pixmap is not None and not dialog.busy)
        assert (dialog.pixmap.width(), dialog.pixmap.height()) == (400, 240)
        assert not dialog.pixmap.toImage().isGrayscale()
        capture = tmp_path / "image-read-color.png"
        assert dialog.grab().save(str(capture))
        record_property("colorScreenshot", str(capture))

        editor._schemaForm.setSchema(schema, {"imagePath": str(path), "colorMode": "grayscale"})
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 2 and dialog.run.isEnabled())
        assert [row["rawParams"]["colorMode"] for row in dialog.records] == ["grayscale", "color"]
        dialog.outputPorts.setCurrentIndex(dialog.outputPorts.findText("image"))
        wait(lambda: dialog.pixmap is not None and not dialog.busy)
        assert dialog.pixmap.toImage().isGrayscale()

        missing = tmp_path / "not-on-runtime.png"
        editor._schemaForm.setSchema(schema, {"imagePath": str(missing), "colorMode": "color"})
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: dialog.records[0]["status"] == "FAILED" and dialog.run.isEnabled())
        assert dialog.records[0]["code"] == "E_INPUT_MISSING"
        assert "Runtime" in dialog.resultLabel.text() and str(missing) in dialog.resultLabel.text()
        assert dialog.pixmap is None and dialog.outputPorts.count() == 0
        assert payload == before and not applied and editor.isDirty()
        assert runtime.loadedDocument is None and not runtime.jobRepository.all()
        capture = tmp_path / "image-read-missing.png"
        assert dialog.grab().save(str(capture))
        record_property("missingScreenshot", str(capture))
        QTest.mouseClick(dialog.end, Qt.LeftButton)
        wait(lambda: not isValid(dialog))
        assert not runtime.operatorDebugManager.ownsResources()
    finally:
        editor.forceClose()
