import pytest

from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator


@pytest.mark.parametrize("selected", ["D:/outputs/default.png", ""])
def testSavePickerPreservesLegacyParameters(retainedQtApplication, monkeypatch, selected):
    from PySide2.QtWidgets import QFileDialog, QPushButton

    form = SchemaParamForm()
    try:
        form.setSchema(ImageSaverOperator.meta.paramSchema,
                       {"outputPath": "C:/outputs/default.png", "overwrite": False})
        calls = []

        def choose(parent, title, initial, filters):
            calls.append((initial, filters))
            return selected, ""

        monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
        form._controls["outputPath"].findChild(QPushButton).click()
        assert calls and calls[0][0] == "C:/outputs/default.png"
        assert "*.png" in calls[0][1]
        assert form.getValues() == {
            "outputPath": selected or "C:/outputs/default.png", "overwrite": False, "namingMode": "fixed",
        }
    finally:
        form.close()
        form.deleteLater()


def testDesignerShowsStringFileNameInput(ownedFlowScene):
    scene = ownedFlowScene()
    inputs = {name: spec if isinstance(spec, str) else spec["type"]
              for name, spec in ImageSaverOperator.meta.inputPorts.items()}
    scene.addFlowNode(FlowNodeViewModel(
        "save", "Image Saver", 0, 0, inputs, ImageSaverOperator.meta.outputPorts,
        ImageSaverOperator.meta.operatorId,
    ))
    assert ("save", "input", "image") in scene._portItems
    assert ("save", "input", "fileName") in scene._portItems
    assert "string" in scene._portItems[("save", "input", "fileName")].toolTip()


def testUniqueNamingSelectorPreservesFixedOverwritePreference(retainedQtApplication):
    form = SchemaParamForm()
    try:
        form.setSchema(ImageSaverOperator.meta.paramSchema,
                       {'outputPath': 'C:/outputs/result.png', 'namingMode': 'unique', 'overwrite': False})
        selector = form._controls['namingMode']
        assert selector.currentText() == '自动唯一命名'
        assert not form._controls['overwrite'].isEnabled()
        selector.setCurrentIndex(selector.findData('fixed'))
        assert form._controls['overwrite'].isEnabled()
        assert form.getValues()['overwrite'] is False
        selector.setCurrentIndex(selector.findData('unique'))
        assert form.getValues()['namingMode'] == 'unique'
        assert form.getValues()['overwrite'] is False
    finally:
        form.close()
        form.deleteLater()
