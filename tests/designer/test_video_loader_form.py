import pytest

from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.plugins.builtins.video_loader.operator import VideoLoaderOperator


@pytest.mark.parametrize("selected", ["D:/videos/\u6d4b\u8bd5.mp4", ""])
def testVideoFilePickerAndFractionalInterval(retainedQtApplication, monkeypatch, selected):
    from PySide2.QtWidgets import QFileDialog, QPushButton

    form = SchemaParamForm()
    try:
        form.setSchema(VideoLoaderOperator.meta.paramSchema,
                       {"videoPath": "C:/video.avi", "intervalSec": .25})
        calls = []

        def choose(parent, title, initial, fileFilter):
            calls.append((initial, fileFilter))
            return selected, ""

        monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
        form._controls["videoPath"].findChild(QPushButton).click()
        assert calls[0][0] == "C:/video.avi"
        assert "*.mp4" in calls[0][1] and "*.avi" in calls[0][1]
        assert form.getValues() == {
            "videoPath": selected or "C:/video.avi", "intervalSec": .25, "colorMode": "color",
        }
    finally:
        form.close()
        form.deleteLater()
