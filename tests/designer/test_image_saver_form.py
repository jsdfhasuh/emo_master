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
            "outputPath": selected or "C:/outputs/default.png", "overwrite": False,
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
