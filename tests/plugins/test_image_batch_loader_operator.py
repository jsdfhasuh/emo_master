from pathlib import Path

import cv2
import numpy as np
import pytest

from emo_master.core.contracts.geometry2d import BBox2D
from emo_master.plugins.builtins.image_batch_loader.operator import ImageBatchLoaderOperator


def _image(path, value=20):
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", np.full((8, 12, 3), value, dtype=np.uint8))
    assert ok
    encoded.tofile(path)


def _read(operator, root, inputs=None, context=None, **params):
    return operator.executeNode(
        inputs or {}, {"folderPath": str(root), **params}, context or {}
    )


def testReadsOneImagePerCallInNaturalOrderAndStops(tmp_path):
    for index in (10, 2, 1):
        _image(tmp_path / f"image{index}.PNG", index)
    (tmp_path / "notes.txt").write_text("not an image")
    (tmp_path / "directory.png").mkdir()
    operator = ImageBatchLoaderOperator()
    for index, value in enumerate((1, 2, 10)):
        result = _read(operator, tmp_path)
        assert result["status"] == "ok"
        outputs = result["outputs"]
        assert outputs["index"] == index
        assert outputs["total"] == 3
        assert outputs["hasNext"] is (index < 2)
        assert np.all(outputs["image"] == value)
        assert Path(outputs["imagePath"]).name == f"image{value}.PNG"
        frame = BBox2D.fromPayload(outputs["frame"])
        assert (frame.width, frame.height) == (12, 8)
        assert frame.coordinateSpace.sourceId == outputs["imagePath"]
    for _ in range(2):
        assert _read(operator, tmp_path)["error"]["code"] == "E_INPUT_EXHAUSTED"


def testUnicodePathsRecursionAndGrayscale(tmp_path):
    root = tmp_path / "\u56fe\u7247"
    _image(root / "\u56fe1.png")
    _image(root / "\u5b50\u6587\u4ef6\u5939" / "\u56fe2.png")
    operator = ImageBatchLoaderOperator()
    assert _read(operator, root)["outputs"]["total"] == 1
    result = _read(operator, root, recursive=True, colorMode="grayscale")
    assert result["outputs"]["total"] == 2
    assert result["outputs"]["index"] == 0
    assert result["outputs"]["image"].shape == (8, 12)


@pytest.mark.parametrize("change", ["reset", "job", "folder", "dispose"])
def testSequenceCanRestart(tmp_path, change):
    _image(tmp_path / "1.png")
    _image(tmp_path / "2.png")
    operator = ImageBatchLoaderOperator()
    context = {"jobId": "first"}
    assert _read(operator, tmp_path, context=context)["outputs"]["index"] == 0
    inputs = {}
    if change == "reset":
        inputs["reset"] = True
    elif change == "job":
        context["jobId"] = "second"
    elif change == "folder":
        tmp_path = tmp_path / "other"
        _image(tmp_path / "1.png")
    else:
        operator.disposeOperator()
    assert _read(operator, tmp_path, inputs, context)["outputs"]["index"] == 0


def testSnapshotDoesNotRescanUntilReset(tmp_path):
    _image(tmp_path / "1.png")
    operator = ImageBatchLoaderOperator()
    assert _read(operator, tmp_path)["outputs"]["total"] == 1
    _image(tmp_path / "2.png")
    assert _read(operator, tmp_path)["error"]["code"] == "E_INPUT_EXHAUSTED"
    assert _read(operator, tmp_path, {"reset": True})["outputs"]["total"] == 2


def testBadImageDoesNotAdvanceAndCanBeRetried(tmp_path):
    path = tmp_path / "1.png"
    path.write_bytes(b"not an image")
    _image(tmp_path / "2.png")
    operator = ImageBatchLoaderOperator()
    assert _read(operator, tmp_path)["error"]["code"] == "E_INPUT_DECODE"
    _image(path)
    assert _read(operator, tmp_path)["outputs"]["index"] == 0
    (tmp_path / "2.png").unlink()
    assert _read(operator, tmp_path)["error"]["code"] == "E_INPUT_MISSING"


@pytest.mark.parametrize("kind", ["empty", "missing", "file"])
def testMissingImagesAreExplicitErrors(tmp_path, kind):
    path = tmp_path
    if kind == "missing":
        path = tmp_path / "missing"
    elif kind == "file":
        path = tmp_path / "1.png"
        _image(path)
    assert _read(ImageBatchLoaderOperator(), path)["error"]["code"] == "E_INPUT_MISSING"


@pytest.mark.parametrize("params", [
    {}, {"folderPath": " "}, {"folderPath": 123},
    {"folderPath": "images", "colorMode": "bad"},
    {"folderPath": "images", "recursive": "false"},
])
def testParameterValidation(params):
    operator = ImageBatchLoaderOperator()
    assert operator.validateParams(params)["code"] == "E_PARAM_INVALID"
    assert operator.executeNode({}, params, {})["error"]["code"] == "E_PARAM_INVALID"


def testResetMustBeBoolean(tmp_path):
    result = _read(ImageBatchLoaderOperator(), tmp_path, {"reset": 1})
    assert result["error"]["code"] == "E_INPUT_TYPE"


def testCancellationDoesNotConsumeAnImage(tmp_path):
    _image(tmp_path / "1.png")
    operator = ImageBatchLoaderOperator()

    def cancelled():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        _read(operator, tmp_path, context={"raiseIfCancellationRequested": cancelled})
    assert _read(operator, tmp_path)["outputs"]["index"] == 0


def testInstancesHaveIndependentCursors(tmp_path):
    _image(tmp_path / "1.png")
    _image(tmp_path / "2.png")
    first, second = ImageBatchLoaderOperator(), ImageBatchLoaderOperator()
    assert _read(first, tmp_path)["outputs"]["index"] == 0
    assert _read(first, tmp_path)["outputs"]["index"] == 1
    assert _read(second, tmp_path)["outputs"]["index"] == 0
