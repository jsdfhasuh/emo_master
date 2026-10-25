"""Native Qt controls, actual Designer open entry, and real debug-worker results."""
from copy import deepcopy
import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide2.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QFileDialog, QPushButton
from shiboken2 import isValid

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.patchcore_inference.operator import PatchCoreInferenceOperator
from tests.designer.qt_wait import waitForCatalog
from tests.designer.test_operator_debug_window import wait
from tests.plugins.patchcore_fixture import makeDeployment
from tests.runtime.patchcore_fixture import project
from tests.runtime.test_builtin_vision_operator_workflows import _registry
from tests.runtime.test_operator_debug_rpc import runtime as runtime


def screenshot(widget, name):
    directory = os.environ.get("EMO_PATCHCORE_SCREEN_DIR")
    if directory:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        assert widget.grab().save(str(target / (name + ".png")))


@pytest.mark.parametrize("selected", ["D:/models/\u6d4b\u8bd5\u90e8\u7f72", ""])
def testFolderPickerAndDefaults(monkeypatch, selected):
    form = SchemaParamForm()
    try:
        schema = PatchCoreInferenceOperator.meta.paramSchema
        form.setSchema(schema, {"modelPath": "D:/models/current"})
        calls = []

        def choose(*args):
            calls.append(args)
            return selected

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", choose)
        form._controls["modelPath"].findChild(QPushButton).click()
        assert calls[0][2] == "D:/models/current"
        assert form.getValues() == dict(modelPath=selected or "D:/models/current", drawHeatmap=True,
                                       drawOverlay=True, overlayMode="heatmap", highlightThreshold=.7, outputAnomalyMap=False)
        assert set(form._controls) == {"modelPath", "drawHeatmap", "drawOverlay", "overlayMode", "highlightThreshold", "outputAnomalyMap"}
        assert form._controls["highlightThreshold"].isHidden()
    finally:
        form.close()
        form.deleteLater()


def testHighlightControlsKeepInactiveDraftAndValidateWhenActive():
    form = SchemaParamForm()
    try:
        schema = PatchCoreInferenceOperator.meta.paramSchema
        form.setSchema(schema, {"modelPath": "D:/models/current", "highlightThreshold": "unfinished"})
        mode, threshold = form._controls["overlayMode"], form._controls["highlightThreshold"]
        assert threshold.isHidden() and not form.validationMessage()
        mode.setCurrentIndex(mode.findData("highlight"))
        assert not threshold.isHidden() and form.validationMessage()
        threshold.setText("0")
        assert form.validationMessage()
        threshold.setText("0.8")
        assert not form.validationMessage()
        form._controls["drawOverlay"].setChecked(False)
        assert mode.isHidden() and threshold.isHidden()
        saved = form.getValues()
        form.setSchema(schema, saved)
        form._controls["drawOverlay"].setChecked(True)
        assert form.getValues()["overlayMode"] == "highlight" and form.getValues()["highlightThreshold"] == .8
        assert not form._controls["highlightThreshold"].isHidden()
    finally:
        form.close()
        form.deleteLater()


@pytest.mark.parametrize("width,legacy", [(460, False), (620, False), (460, True)])
def testActualDesignerOpenParametersSaveReopenAndCompile(runtime, tmp_path, ownedDesignerWindow, designerApplication, width, legacy):
    model = makeDeployment(tmp_path / "\u6a21\u578b")
    path = tmp_path / "patchcore.emoproj"
    document = project(model)
    if legacy:
        # Compatibility input: a 2.7 project with the original four-field node.
        node = next(node for node in document.workflows["main"].nodes if node.nodeId == "patchcore")
        node.paramSchema = deepcopy(node.paramSchema)
        for name in ("overlayMode", "highlightThreshold"):
            node.paramSchema["properties"].pop(name)
        node.paramSchema.pop("xModeOperator")
        document.dependencies.operators[0]["version"] = "1.0.0"
    saveProject(path, document.model_dump(mode="json"))
    window = ownedDesignerWindow(RuntimeClient(runtime),
        settingsStore=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    waitForCatalog(window)
    definition = window._operatorDefinition("vision.inference.patchcore")
    assert definition["displayName"] == "PatchCore Inference" and definition["category"] == "\u63a8\u7406"
    assert definition in window.operatorCatalogController.getOperatorsByCategory("\u63a8\u7406")
    assert window.loadProjectDirectory(str(path), confirmed=True)
    window.resize(1280, 800)
    window.show()
    original = deepcopy(window.flowModel.nodes["patchcore"])
    window.openNodeParamDialog("patchcore")
    editor = window.nodeParamDialog
    try:
        editor.resize(width, 720)
        designerApplication.processEvents()
        form = editor._schemaForm
        assert editor.width() == width
        assert editor.collectParams() == dict(modelPath=str(model), drawHeatmap=True, drawOverlay=True,
                                             overlayMode="heatmap", highlightThreshold=.7, outputAnomalyMap=False)
        assert not editor.isDirty()
        assert window.flowModel.nodes["patchcore"] == original
        mode = form._controls["overlayMode"]
        mode.setCurrentIndex(mode.findData("highlight"))
        form._controls["highlightThreshold"].setValue(.8)
        designerApplication.processEvents()
        for name, control in form._controls.items():
            box = form._fieldContainers.get(name, control)
            assert control.parentWidget().rect().contains(control.geometry())
            label = form._layout.labelForField(box)
            assert not label.geometry().intersects(box.geometry())
        assert not editor._applyButton.visibleRegion().isEmpty()
        assert not editor._closeButton.visibleRegion().isEmpty()
        screenshot(editor, f"patchcore-highlight-parameters-{width}-legacy-{legacy}")
        form._controls["drawHeatmap"].setChecked(False)
        form._controls["outputAnomalyMap"].setChecked(True)
        assert editor.applyChanges()
    finally:
        editor.forceClose()
    assert window.saveProjectToDirectory(str(path))
    saved = loadProjectDocument(path)
    assert saved.schemaVersion == "2.7" and saved.devices.definitions == {}
    assert saved.presentation is not None and saved.resources is not None and saved.production is not None
    WorkflowCompiler(operatorRegistry=_registry()).compile(saved)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    assert window.flowModel.nodes["patchcore"].params == dict(modelPath=str(model), drawHeatmap=False, drawOverlay=True,
                                                           overlayMode="highlight", highlightThreshold=.8, outputAnomalyMap=True)
    window.focusGraphContent()
    wait(lambda: all(window.flowView.viewport().rect().contains(
        window.flowView.mapFromScene(item.sceneBoundingRect()).boundingRect())
        for item in window.flowScene._nodeItems.values()))
    screenshot(window, f"patchcore-project-{width}")
    assert runtime.loadedDocument is None and not runtime.jobRepository.all()


@pytest.mark.parametrize("overlayMode", ["heatmap", "highlight"])
def testDebugUiShowsImagesRawMapAndHistoricalScore(runtime, tmp_path, ownedDesignerWindow, monkeypatch, overlayMode):
    y, x = np.mgrid[:256, :256].astype(np.float32)
    raw = (2 + 4 * np.exp(-.5 * (((x - 178) / 12)**2 + ((y - 127) / 20)**2)))[None, None]
    model = makeDeployment(tmp_path / "model", rawMap=raw)
    path = tmp_path / "debug.emoproj"
    document = project(model, outputAnomalyMap=True, overlayMode=overlayMode)
    saveProject(path, document.model_dump(mode="json"))
    window = ownedDesignerWindow(RuntimeClient(runtime))
    waitForCatalog(window)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    window.openNodeParamDialog("patchcore")
    editor = window.nodeParamDialog
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(lambda: dialog.run.isEnabled())
        imagePath = tmp_path / "input.png"
        image = np.full((240, 360, 3), 55, np.uint8)
        cv2.rectangle(image, (20, 20), (339, 219), (170, 172, 174), -1)
        for center in ((45, 45), (314, 45), (45, 194), (314, 194)):
            cv2.circle(image, center, 9, (85, 87, 89), -1)
        cv2.line(image, (235, 85), (265, 150), (35, 35, 35), 3)
        success, encoded = cv2.imencode(".png", image)
        assert success
        imagePath.write_bytes(encoded.tobytes())
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(imagePath), ""))
        dialog.selectFile("image")
        wait(lambda: "image" in dialog.inputValues() and dialog.run.isEnabled())
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
        assert dialog.records[0]["status"] == "SUCCEEDED", dialog.records[0]
        dialog.outputPorts.setCurrentIndex(dialog.outputPorts.findText("overlay"))
        wait(lambda: dialog.pixmap is not None)
        assert not dialog.pixmap.isNull() and dialog.pixmap.width() == 360 and dialog.pixmap.height() == 240
        if overlayMode == "highlight":
            assert dialog.records[0]["diagnostics"]["localization"]["status"] == "highlighted"
        screenshot(dialog, "patchcore-debug-overlay-" + overlayMode)
        directory = os.environ.get("EMO_PATCHCORE_SCREEN_DIR")
        if directory:
            target = Path(directory)
            assert dialog.pixmap.save(str(target / ("synthetic-overlay-" + overlayMode + ".png")))
            (target / "synthetic-input.png").write_bytes(encoded.tobytes())
        dialog.outputPorts.setCurrentIndex(dialog.outputPorts.findText("anomalyMap"))
        wait(lambda: dialog.outputTree.topLevelItemCount() == 1)
        root = dialog.outputTree.topLevelItem(0)
        fields = {root.child(i).text(0): root.child(i).text(1) for i in range(root.childCount())}
        assert fields["dtype"] == "float32" and fields["values"] == "list [256]"
        screenshot(dialog, "patchcore-debug-map-" + overlayMode)
        editor._schemaForm._controls["drawOverlay"].setChecked(False)
        wait(lambda: dialog.run.isEnabled())
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 2 and dialog.run.isEnabled())
        assert "overlay" not in dialog.records[0]["outputAssets"]
        assert "overlay" in dialog.records[0]["diagnostics"]["imageOutputMessages"]
        dialog.versions.setCurrentIndex(1)
        dialog.outputPorts.setCurrentIndex(dialog.outputPorts.findText("score"))
        selected = dialog.exportSelection()
        assert selected["wire"] == {"inline": 2.5} and selected["identity"]["selection"] == "history"
        assert not runtime.jobRepository.all() and runtime.loadedDocument is None
    finally:
        editor.forceClose()
        def retired():
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            return (not runtime.operatorDebugManager.ownsResources() and not isValid(dialog)
                    and not isValid(editor) and window.operatorEditorManager.count() == 0)
        wait(retired)
