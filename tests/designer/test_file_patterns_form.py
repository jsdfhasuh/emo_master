from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QPoint, QRect, QSettings
from PySide2.QtWidgets import QCheckBox, QPushButton, QScrollArea

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.schema_utils import mergeParameterTitles
from emo_master.apps.designer.ui.file_patterns_control import FilePatternsControl
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.file_list.operator import FileListOperator
from tests.designer.test_main_window_project_save_load import RuntimeClientStub
from tests.designer.test_schema_form_wheel_safety import wheel
from tests.runtime.test_image_batch_while import _registry
from tests.runtime.video_files_fixture import fileVideoProject


def makeForm(patterns=None):
    form = SchemaParamForm()
    values = {"folderPath": "D:/视频"}
    if patterns is not None:
        values["patterns"] = patterns
    form.setSchema(FileListOperator.meta.paramSchema, values)
    return form, form._controls["patterns"]


def choose(control, mode):
    control.modeCombo.setCurrentIndex(control.modeCombo.findData(mode))


@pytest.mark.parametrize("patterns,mode", [
    (["*"], "all"), (["*.mp4"], "video"), (["*.AVI", "*.MP4", "*.AVI"], "video"),
    ([" *.PNG ", "*.jpg"], "image"), (["*.csv"], "custom"),
    (["*.mp4", "*.jpg"], "custom"), (["camera_*.mp4", "骑手, 片段?.avi"], "custom"),
    (["*.*"], "custom"), (["*", "*.mp4"], "custom"),
])
def testExistingFiltersOpenWithoutChangingSavedArray(patterns, mode):
    original = deepcopy(patterns)
    form, control = makeForm(patterns)
    assert isinstance(control, FilePatternsControl)
    assert control.modeCombo.currentData() == mode
    assert form.validationMessage() == ""
    assert form.getValues()["patterns"] == original == patterns
    if mode in control.FORMATS:
        assert [ext for ext in control.FORMATS[mode] if control.formatChecks[ext].isChecked()] == [
            ext for ext in control.FORMATS[mode] if f"*.{ext}" in [p.strip().lower() for p in patterns]]
    if mode == "custom":
        assert control.customEdit.toPlainText() == "\n".join(patterns)
    assert form._layout.labelForField(control).text() == "文件类型"
    assert "参数键：patterns" in control.toolTip()
    detached = form.getValues()["patterns"]
    detached.append("*.txt")
    assert form.getValues()["patterns"] == original


def testDefaultAndPresetSwitchingRetainSelections():
    form, control = makeForm()
    changes = []
    form.valuesChanged.connect(lambda: changes.append(True))
    assert control.modeCombo.currentData() == "all"
    assert form.getValues()["patterns"] == ["*"]
    choose(control, "video")
    assert form.getValues()["patterns"] == [f"*.{ext}" for ext in control.FORMATS["video"]]
    control.clearButton.click()
    control.formatChecks["mp4"].click()
    choose(control, "image")
    control.clearButton.click()
    control.formatChecks["png"].click()
    control.formatChecks["jpg"].click()
    assert form.getValues()["patterns"] == ["*.png", "*.jpg"]
    choose(control, "video")
    assert form.getValues()["patterns"] == ["*.mp4"]
    choose(control, "image")
    assert form.getValues()["patterns"] == ["*.png", "*.jpg"]
    control.selectAllButton.click()
    assert form.getValues()["patterns"] == [f"*.{ext}" for ext in control.FORMATS["image"]]
    choose(control, "all")
    assert form.getValues()["patterns"] == ["*"]
    assert changes


def testCheckboxesFilterRealFilenamesWithoutChangingRuntimeContract(tmp_path):
    for filename in ("clip10.mp4", "clip2.AVI", "clip1.png", "notes.txt"):
        (tmp_path / filename).touch()
    form, control = makeForm(["*.mp4"])
    control.formatChecks["avi"].click()
    params = dict(form.getValues(), folderPath=str(tmp_path))
    assert params["patterns"] == ["*.mp4", "*.avi"]
    result = FileListOperator().executeNode({}, params, {})
    assert result["status"] == "ok"
    assert result["outputs"] == {"paths": [str(tmp_path / name) for name in ("clip2.AVI", "clip10.mp4")], "count": 2}


def testCustomRulesNeedNoJsonAndSurviveModeSwitches():
    form, control = makeForm(["*.mp4"])
    choose(control, "custom")
    assert control.customEdit.toPlainText() == "*.mp4"
    control.customEdit.setPlainText("camera_*.mp4\n骑手, 片段?.avi\n\n*.csv")
    expected = ["camera_*.mp4", "骑手, 片段?.avi", "*.csv"]
    assert form.getValues()["patterns"] == expected
    choose(control, "all")
    choose(control, "custom")
    assert form.getValues()["patterns"] == expected
    assert form.validationMessage() == ""
    form.setSchema(FileListOperator.meta.paramSchema, form.getValues())
    assert form._controls["patterns"].modeCombo.currentData() == "custom"
    assert form.getValues()["patterns"] == expected


@pytest.mark.parametrize("mode", ["video", "image", "custom"])
def testEmptySelectionIsNotSilentlyAllFilesAndCanBeCorrected(mode):
    form, control = makeForm(["*.mp4"])
    choose(control, mode)
    if mode == "custom":
        control.customEdit.setPlainText(" \n\t")
    else:
        control.clearButton.click()
    message = form.validationMessage()
    assert "请至少" in message
    assert "JSON" not in message
    with pytest.raises(ValueError, match="请至少"):
        form.getValues()
    assert control.accessibleDescription() == message
    choose(control, "all")
    assert control.accessibleDescription() == ""
    assert form.validationMessage() == ""
    assert form.getValues()["patterns"] == ["*"]


@pytest.mark.parametrize("text", ["D:/videos/*.mp4", "folder\\*.mp4"])
def testCustomPathErrorRetainsDraft(text):
    form, control = makeForm(["*.csv"])
    control.customEdit.setPlainText(text)
    assert "不要填写文件夹路径" in form.validationMessage()
    assert control.customEdit.toPlainText() == text
    control.customEdit.setPlainText("*.mp4")
    assert form.validationMessage() == ""


def testFileWidgetOverlayPreservesSavedContract():
    saved = {"type": "object", "required": ["patterns"], "properties": {"patterns": {
        "type": "array", "items": {"type": "string"}, "default": ["*.csv"], "maxItems": 5,
    }}}
    before = deepcopy(saved)
    result = mergeParameterTitles(saved, FileListOperator.meta.paramSchema)
    field = result["properties"]["patterns"]
    assert field["xWidget"] == "file-patterns"
    assert field["default"] == ["*.csv"] and field["maxItems"] == 5
    assert field["items"] == {"type": "string"}
    assert result["required"] == ["patterns"]
    assert saved == before


@pytest.mark.parametrize("focused", [False, True])
def testWheelDoesNotChangeFileType(designerApplication, focused):
    form, control = makeForm(["*.mp4"])
    form.show()
    designerApplication.processEvents()
    if focused:
        control.modeCombo.setFocus()
    wheel(control.modeCombo)
    designerApplication.processEvents()
    assert control.modeCombo.currentData() == "video"
    assert form.getValues()["patterns"] == ["*.mp4"]


def assertLayoutAndCapture(editor, control, width, mode, app):
    editor.resize(width, 720)
    for _ in range(4):
        app.processEvents()
    assert editor.width() == width
    scroll = control.parentWidget().parentWidget().parentWidget()
    assert isinstance(scroll, QScrollArea)
    assert scroll.horizontalScrollBar().maximum() == 0
    viewport = scroll.viewport()
    rect = QRect(control.mapTo(viewport, QPoint()), control.size())
    assert viewport.rect().contains(rect)
    for child in control.findChildren(QCheckBox) + control.findChildren(QPushButton):
        if child.isVisible():
            assert control.rect().contains(QRect(child.mapTo(control, QPoint()), child.size()))
            assert child.width() >= child.minimumSizeHint().width()
    form = editor._schemaForm
    # Keep the three fields together rather than stretching gaps down the window.
    assert form._controls["folderPath"].y() < 30
    assert control.y() - form._controls["folderPath"].geometry().bottom() < 30
    assert form._controls["recursive"].y() - control.geometry().bottom() < 30
    directory = os.environ.get("EMO_FILE_PATTERNS_EVIDENCE_DIR")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        assert editor.grab().save(str(path / f"file-list-{mode}-{width}.png"))


@pytest.mark.parametrize("width", [620, 460])
def testLegacyProjectEditorApplySaveAndReopen(tmp_path, monkeypatch, ownedDesignerWindow, designerApplication, width):
    registry = _registry()
    document = fileVideoProject(registry)
    node = next(node for node in document.workflows["main"].nodes if node.nodeId == "files")
    node.paramSchema["properties"]["patterns"].pop("xWidget")
    node.paramSchema["properties"]["patterns"]["title"] = "文件名筛选"
    node.params["patterns"] = ["*.MP4"]
    oldSchema = deepcopy(node.paramSchema)
    path = tmp_path / "files.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    beforeBytes = path.read_bytes()
    client = RuntimeClientStub()
    catalog = [SimpleNamespace(
        operatorId=FileListOperator.meta.operatorId, displayName=FileListOperator.meta.displayName,
        paramSchema=FileListOperator.meta.paramSchema,
    )]
    # Project reload refreshes the catalog asynchronously; keep the stub current too.
    monkeypatch.setattr(client, "listOperators", lambda: catalog)
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    assert window.loadProjectSelection(str(path))
    monkeypatch.setattr(window, "_requireOperatorCatalog", lambda: True)
    window.operatorCatalogController.applyOperators(catalog, lambda _: "其他")
    beforeGraph = deepcopy(window.flowModel.toProjectGraph())
    window.openNodeParamDialog("files")
    editor = window.nodeParamDialog
    control = editor._schemaForm._controls["patterns"]
    assert isinstance(control, FilePatternsControl)
    assert not editor.isDirty() and not window.pageCoordinator.session.dirty
    assert editor.collectParams()["patterns"] == ["*.MP4"]
    assert window.flowModel.toProjectGraph() == beforeGraph
    assert path.read_bytes() == beforeBytes
    assertLayoutAndCapture(editor, control, width, "video", designerApplication)
    for mode in ("all", "image", "custom"):
        choose(control, mode)
        assertLayoutAndCapture(editor, control, width, mode, designerApplication)
    choose(control, "video")
    control.formatChecks["avi"].click()
    assert editor.isDirty()
    assert editor.applyChanges()
    assert window.flowModel.nodes["files"].params["patterns"] == ["*.mp4", "*.avi"]
    assert window.saveProjectToDirectory(str(path))
    reloaded = loadProjectDocument(path)
    assert reloaded.schemaVersion == "2.7"
    WorkflowCompiler(registry).compile(reloaded)
    saved = next(node for node in reloaded.workflows["main"].nodes if node.nodeId == "files")
    assert saved.paramSchema == oldSchema
    assert saved.params["patterns"] == ["*.mp4", "*.avi"]
    assert window.loadProjectSelection(str(path))
    window.openNodeParamDialog("files")
    reopened = window.nodeParamDialog
    assert not reopened.isDirty()
    assert reopened.collectParams()["patterns"] == ["*.mp4", "*.avi"]
    reopenedControl = reopened._schemaForm._controls["patterns"]
    assert reopenedControl.modeCombo.currentData() == "video"
    assert reopenedControl.formatChecks["mp4"].isChecked() and reopenedControl.formatChecks["avi"].isChecked()
