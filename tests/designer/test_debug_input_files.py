import cv2
import numpy as np
import pytest
from PIL import Image
from shiboken2 import isValid

from emo_master.apps.designer.services import debug_input_files
from emo_master.apps.designer.services.debug_input_files import inputFileFilter, readInputFile
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from emo_master.apps.designer.ui.workflow_debug_window import WorkflowDebugWindow
from emo_master.apps.runtime.operator_debug.assets import decode
from emo_master.plugins.builtins._image_frame import frameForInput
from tests.designer.test_operator_debug_window import editorFor, wait
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_workflow_debug_control import nestedProject


def writeImage(path, mode):
    image = Image.new(mode, (13, 7))
    if mode == "P":
        image.putpalette([17, 83, 191] * 256)
        image.info["transparency"] = 0
    elif mode == "RGBA":
        image.paste((17, 83, 191, 0), (0, 0, 13, 7))
    elif mode == "RGB":
        image.paste((17, 83, 191), (0, 0, 13, 7))
    else:
        image.paste(83, (0, 0, 13, 7))
    image.save(path)
    return path.read_bytes()


@pytest.mark.parametrize("mode,suffix", [
    ("L", ".png"), ("RGB", ".png"), ("RGBA", ".png"), ("P", ".png"),
    ("RGB", ".jpg"), ("RGB", ".JPEG"), ("L", ".jpg"),
])
def testImageImportKeepsDecodedPixelsAndOriginalFile(tmp_path, mode, suffix):
    path = tmp_path / ("input" + suffix)
    original = writeImage(path, mode)
    raw, mime, provenance = readInputFile(path, "image")
    pixels, _ = decode(raw, mime)
    expected = cv2.imdecode(np.frombuffer(original, np.uint8), cv2.IMREAD_UNCHANGED)
    if expected.ndim == 3 and expected.shape[2] == 4:
        expected = expected[:, :, :3]
    np.testing.assert_array_equal(pixels, expected)
    assert mime == "image/png" and pixels.dtype == np.uint8
    assert provenance == dict(kind="upload", filename=path.name)
    assert path.read_bytes() == original


def testJsonAndPortFiltersRemainTyped(tmp_path):
    path = tmp_path / "frame.json"
    path.write_bytes(b'{"width":13,"height":7}')
    raw, mime, _ = readInputFile(path, "bbox2d")
    assert decode(raw, mime)[0] == {"width": 13, "height": 7}
    assert inputFileFilter("bbox2d") == "PNG / JSON (*.png *.json)"
    assert all(suffix in inputFileFilter("image") for suffix in ("*.png", "*.jpg", "*.jpeg"))
    with pytest.raises(ValueError):
        readInputFile(path, "image")


def testGenericPortKeepsExistingPngImport(tmp_path):
    path = tmp_path / "input.png"
    original = writeImage(path, "RGB")
    raw, mime, _ = readInputFile(path, "any")
    assert raw == original and mime == "image/png"


def testRejectsHighBitDepthAndOversizedDecodedImage(tmp_path, monkeypatch):
    path = tmp_path / "input.png"
    Image.fromarray(np.full((7, 13), 4095, np.uint16)).save(path)
    with pytest.raises(ValueError, match="8-bit"):
        readInputFile(path, "image")
    Image.new("RGBA", (100, 100)).save(path)
    monkeypatch.setattr(debug_input_files, "VALUE_BYTES", 1000)
    with pytest.raises(ValueError, match="Decoded image"):
        readInputFile(path, "image")
    path.write_bytes(b"x" * 1001)
    with pytest.raises(ValueError, match="Input file"):
        readInputFile(path, "image")


@pytest.mark.parametrize("kind", ["operator", "workflow"])
@pytest.mark.parametrize("mode,suffix", [("RGBA", ".png"), ("RGB", ".jpg")])
def testDebugWindowUploadsImageAndKeepsPriorInputOnFailure(runtime, tmp_path, monkeypatch, kind, mode, suffix):
    from PySide2.QtWidgets import QFileDialog

    editor = None
    if kind == "operator":
        editor, *_ = editorFor(runtime, "vision.inference.yolo")
        dialog = OperatorDebugWindow(editor)
        editor._debugWindow = dialog
        ready = lambda: dialog.run.isEnabled()
        select = dialog.selectFile
    else:
        payload = nestedProject()
        payload["workflows"]["main"]["inputs"] = {"image": {"type": "image", "required": True}}
        dialog = WorkflowDebugWindow(RuntimeClient(runtime), payload, "main")
        ready = lambda: dialog.startButton.isEnabled()
        select = dialog.file
    dialog.show()
    try:
        wait(ready)
        path = tmp_path / ("input" + suffix)
        writeImage(path, mode)
        selected = [path]
        filters = []
        def pick(*args):
            filters.append(args[-1])
            return str(selected[0]), ""
        monkeypatch.setattr(QFileDialog, "getOpenFileName", pick)
        select("image")
        wait(lambda: dialog.rows["image"].wire() is not None and ready())
        wire = dialog.rows["image"].wire()
        asset = dialog.connection.download(wire["assetRef"])
        pixels, _ = decode(asset["content"], asset["asset"]["mime"])
        assert pixels.shape == (7, 13, 3)
        assert "*.jpg" in filters[-1]
        if kind == "operator":
            assert dialog.rows["frame"].wire() is None
            assert dialog.inputForm.labelForField(dialog.rows["image"]).text() == "图片（必填） : image"
            assert dialog.inputForm.labelForField(dialog.rows["frame"]).text() == "坐标框（可选） : bbox2d"
            frame = frameForInput({"image": pixels}, 13, 7)
            assert (frame.x, frame.y, frame.width, frame.height) == (0, 0, 13, 7)
        bad = tmp_path / "broken.png"
        bad.write_bytes(b"not an image")
        selected[0] = bad
        select("image")
        wait(lambda: "file:image" in dialog.actionFeedback.errors and ready())
        assert dialog.rows["image"].wire() == wire
        selected[0] = path
        select("image")
        wait(lambda: "file:image" not in dialog.actionFeedback.errors and ready())
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
        if editor is not None:
            editor.forceClose()
