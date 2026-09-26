from __future__ import annotations

import base64
from types import SimpleNamespace

import numpy as np
import pytest

from emo_master.plugins.builtins.yolo_inference.onnx_backend import (
    OnnxModelError,
    OnnxYoloSession,
    _decodeOutput,
    _validateInputShape,
)


_TINY_YOLO_ONNX = (
    "CAg6qAEKUxIHb3V0cHV0MCIIQ29uc3RhbnQqPgoFdmFsdWUqMggBCAUIAhABSigAAIBAZmaG"
    "QAAAgEAzM4NAAACAQAAAgEAAAIBAAACAQGZmZj/NzEw/oAEEEhB0aW55X3lvbG9fZGV0ZWN0"
    "WiAKBmltYWdlcxIWChQIARIQCgIIAQoCCAMKAggICgIICGIdCgdvdXRwdXQwEhIKEAgBEgwK"
    "AggBCgIIBQoCCAJCBAoAEA1yFQoFbmFtZXMSDHswOiAnc2NyZXcnfXIOCgR0YXNrEgZkZXRl"
    "Y3RyEAoHZW5kMmVuZBIFRmFsc2VyFgoEYXJncxIOeydubXMnOiBGYWxzZX0="
)


def testOnnxSessionRunsTinyModelAndMapsLetterboxToGrayImage(tmp_path) -> None:
    modelPath = tmp_path / "tiny.onnx"
    modelPath.write_bytes(base64.b64decode(_TINY_YOLO_ONNX))

    session = OnnxYoloSession(modelPath)
    result = session.predict(
        np.zeros((4, 8), dtype=np.uint8),
        confidence=0.25,
        iou=0.45,
        imageSize=640,
        maxDetections=300,
        classes=(),
        agnosticNms=False,
    )

    assert result.provider == "CPUExecutionProvider"
    assert result.inputShape == (1, 3, 8, 8)
    assert result.names == {0: "screw"}
    assert result.scores.tolist() == pytest.approx([0.9])
    assert result.classIds.tolist() == [0]
    np.testing.assert_allclose(result.boxesXyxy, [[2.0, 0.0, 6.0, 4.0]])


def testDecodeOutputSupportsPredictionMajorShapeAndClassAwareNms() -> None:
    raw = np.asarray(
        [
            [
                [4.0, 4.0, 4.0, 4.0, 0.9, 0.1],
                [4.0, 4.0, 4.0, 4.0, 0.1, 0.85],
            ]
        ],
        dtype=np.float32,
    )

    boxes, scores, classIds = _decodeOutput(
        raw,
        confidence=0.25,
        iou=0.45,
        maxDetections=10,
        classes=(),
        agnosticNms=False,
        expectedClassCount=2,
    )
    assert boxes.shape == (2, 4)
    assert scores.tolist() == pytest.approx([0.9, 0.85])
    assert classIds.tolist() == [0, 1]

    _, agnosticScores, agnosticClasses = _decodeOutput(
        raw,
        confidence=0.25,
        iou=0.45,
        maxDetections=10,
        classes=(),
        agnosticNms=True,
        expectedClassCount=2,
    )
    assert agnosticScores.tolist() == pytest.approx([0.9])
    assert agnosticClasses.tolist() == [0]


def testDecodeOutputAppliesClassFilterAndMaximumDetectionLimit() -> None:
    raw = np.asarray(
        [
            [
                [2.0, 2.0, 1.0, 1.0, 0.1, 0.8],
                [5.0, 5.0, 1.0, 1.0, 0.2, 0.9],
                [8.0, 8.0, 1.0, 1.0, 0.7, 0.3],
            ]
        ],
        dtype=np.float32,
    )

    _, scores, classIds = _decodeOutput(
        raw,
        confidence=0.25,
        iou=0.45,
        maxDetections=1,
        classes=(1,),
        agnosticNms=False,
        expectedClassCount=2,
    )
    assert scores.tolist() == pytest.approx([0.9])
    assert classIds.tolist() == [1]


def testDynamicOnnxInputUsesRequestedImageSize(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeSession:
        def __init__(self, modelPath: str, providers: list[str]) -> None:
            captured["modelPath"] = modelPath
            captured["providers"] = providers

        def get_providers(self):
            return ["CPUExecutionProvider"]

        def get_inputs(self):
            return [SimpleNamespace(name="images", shape=[None, 3, "height", "width"], type="tensor(float)")]

        def get_outputs(self):
            return [SimpleNamespace(name="output0", shape=[None, 5, None], type="tensor(float)")]

        def get_modelmeta(self):
            return SimpleNamespace(
                custom_metadata_map={
                    "task": "detect",
                    "names": "{0: 'screw'}",
                    "end2end": "False",
                }
            )

        def run(self, outputNames, inputs):
            captured["outputNames"] = outputNames
            captured["tensorShape"] = tuple(inputs["images"].shape)
            return [np.asarray([[[5.0], [5.0], [2.0], [2.0], [0.9]]], dtype=np.float32)]

    import onnxruntime

    monkeypatch.setattr(onnxruntime, "InferenceSession", FakeSession)
    modelPath = tmp_path / "dynamic.onnx"
    modelPath.write_bytes(b"fake")
    session = OnnxYoloSession(modelPath)
    result = session.predict(
        np.zeros((5, 10, 3), dtype=np.uint8),
        confidence=0.25,
        iou=0.45,
        imageSize=10,
        maxDetections=1,
        classes=(),
        agnosticNms=False,
    )

    assert captured["providers"] == ["CPUExecutionProvider"]
    assert captured["tensorShape"] == (1, 3, 10, 10)
    assert result.inputShape == (1, 3, 10, 10)


@pytest.mark.parametrize(
    "shape",
    [
        [2, 3, 640, 640],
        [1, 1, 640, 640],
        [1, 3, 640],
        [1, 3, 0, 640],
    ],
)
def testOnnxInputShapeRejectsUnsupportedLayouts(shape: object) -> None:
    with pytest.raises(OnnxModelError):
        _validateInputShape(shape)
