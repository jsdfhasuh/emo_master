import pytest

from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.plugins.builtins.image_batch_loader.operator import ImageBatchLoaderOperator


@pytest.mark.parametrize("selected", ["D:/images", ""])
def testBatchFolderPickerAndDefaults(retainedQtApplication, monkeypatch, selected):
    from PySide2.QtWidgets import QFileDialog, QLabel, QPushButton

    form = SchemaParamForm()
    try:
        form.setSchema(ImageBatchLoaderOperator.meta.paramSchema, {"folderPath": "C:/images"})
        calls = []

        def choose(parent, title, initial):
            calls.append(initial)
            return selected

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", choose)
        control = form._controls["folderPath"]
        button = control.findChild(QPushButton)
        assert button.toolTip() == "\u9009\u62e9\u6587\u4ef6\u5939"
        button.click()
        assert calls == ["C:/images"]
        assert form.getValues() == {
            "folderPath": selected or "C:/images", "colorMode": "color", "recursive": False
        }
        assert any(label.text() == "\u56fe\u7247\u6587\u4ef6\u5939 *" for label in form.findChildren(QLabel))
    finally:
        form.close()
        form.deleteLater()
