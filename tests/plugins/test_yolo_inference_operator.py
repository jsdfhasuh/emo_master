from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    CoordinateSpace2D,
    DetectionCollection,
)
from emo_master.plugins.builtins.yolo_inference import operator as yolo_module
from emo_master.plugins.builtins.yolo_inference.onnx_backend import OnnxInferenceResult
from emo_master.plugins.builtins.yolo_inference.operator import YoloInferenceOperator


class _FakeModel:
    def __init__(self, result: OnnxInferenceResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def predict(self, **kwargs: object) -> OnnxInferenceResult:
        self.calls.append(dict(kwargs))
        return self.result


@pytest.fixture(autouse=True)
def _clearYoloCache():
    YoloInferenceOperator.clearCache()
    yield
    YoloInferenceOperator.clearCache()


def _modelFile(tmpPath: Path) -> Path:
    path = tmpPath / "model.onnx"
    path.write_bytes(b"test-model")
    return path


def _result(
    boxes: object,
    scores: object,
    classes: object,
    names: dict[int, str] | None = None,
) -> OnnxInferenceResult:
    boxArray = np.asarray(boxes, dtype=np.float32)
    if boxArray.size == 0:
        boxArray = boxArray.reshape(0, 4)
    return OnnxInferenceResult(
        boxesXyxy=boxArray,
        scores=np.asarray(scores, dtype=np.float32),
        classIds=np.asarray(classes, dtype=np.int64),
        names=names or {0: "part", 1: "defect"},
        provider="CPUExecutionProvider",
        inputShape=(1, 3, 640, 640),
    )


def testYoloInferenceProducesTypedDetectionsAndOverlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fakeModel = _FakeModel(
        _result(
            [[1, 2, 6, 8], [-2, -1, 4, 5]],
            [0.9, 0.75],
            [0, 1],
        )
    )
    loads: list[str] = []

    def load(path: str) -> object:
        loads.append(path)
        return fakeModel

    monkeypatch.setattr(yolo_module, "_createModel", load)
    modelPath = _modelFile(tmp_path)
    image = np.zeros((10, 12, 3), dtype=np.uint8)
    frame = BBox2D(
        0,
        0,
        12,
        10,
        CoordinateSpace2D(
            imageWidth=12,
            imageHeight=10,
            sourceId="camera",
            transformToSource=(2, 0, 0, 2, 10, 20),
        ),
    )
    operator = YoloInferenceOperator()

    first = operator.executeNode(
        {"image": image, "frame": frame.toPayload()},
        {"modelPath": str(modelPath), "drawOverlay": True},
        {},
    )
    second = operator.executeNode({"image": image}, {"modelPath": str(modelPath)}, {})

    assert first["status"] == "ok"
    detections = DetectionCollection.fromPayload(first["outputs"]["detections"])
    assert [item.label for item in detections.items] == ["part", "defect"]
    assert detections.items[0].bbox.x == 1.0
    assert detections.items[0].bbox.width == 5.0
    assert detections.items[0].attributes["backend"] == "onnxruntime"
    assert detections.items[1].bbox.x == 0.0
    assert detections.items[1].bbox.y == 0.0
    assert detections.coordinateSpace.sourceId == "camera"
    assert detections.coordinateSpace.mapPointToSource(1, 2) == (12.0, 24.0)
    assert BBox2D.fromPayload(first["outputs"]["frame"]) == frame
    overlay = first["outputs"]["overlay"]
    assert isinstance(overlay, np.ndarray)
    assert overlay.shape == image.shape
    assert second["status"] == "ok"
    assert len(loads) == 1
    assert len(fakeModel.calls) == 2
    assert fakeModel.calls[0]["confidence"] == 0.25
    assert first["diagnostics"]["provider"] == "CPUExecutionProvider"


def testYoloInferenceSupportsEmptyResults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        yolo_module,
        "_createModel",
        lambda path: _FakeModel(_result([], [], [])),
    )
    result = YoloInferenceOperator().executeNode(
        {"image": np.zeros((4, 5, 3), dtype=np.uint8)},
        {"modelPath": str(_modelFile(tmp_path))},
        {},
    )

    detections = DetectionCollection.fromPayload(result["outputs"]["detections"])
    assert detections.items == ()


def testYoloInferenceMapsBackendAndModelErrors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator = YoloInferenceOperator()
    missing = operator.executeNode(
        {"image": np.zeros((2, 2, 3), dtype=np.uint8)},
        {"modelPath": str(tmp_path / "missing.onnx")},
        {},
    )

    def unavailable(path: str) -> object:
        _ = path
        raise yolo_module._BackendUnavailableError("backend missing")

    monkeypatch.setattr(yolo_module, "_createModel", unavailable)
    backend = operator.executeNode(
        {"image": np.zeros((2, 2, 3), dtype=np.uint8)},
        {"modelPath": str(_modelFile(tmp_path))},
        {},
    )

    assert missing["error"]["code"] == "E_MODEL_NOT_FOUND"
    assert backend["error"]["code"] == "E_BACKEND_UNAVAILABLE"


def testYoloInferenceRejectsMalformedBackendResult(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    malformed = _FakeModel(_result([[1, 2, 3]], [0.9], [0]))
    monkeypatch.setattr(yolo_module, "_createModel", lambda path: malformed)

    result = YoloInferenceOperator().executeNode(
        {"image": np.zeros((4, 5, 3), dtype=np.uint8)},
        {"modelPath": str(_modelFile(tmp_path))},
        {},
    )

    assert result["error"]["code"] == "E_RESULT_INVALID"


def testYoloInferenceValidatesParametersAndImageType(tmp_path: Path) -> None:
    operator = YoloInferenceOperator()
    invalidParam = operator.executeNode(
        {"image": np.zeros((2, 2, 3), dtype=np.uint8)},
        {"modelPath": str(_modelFile(tmp_path)), "confidence": 2.0},
        {},
    )
    invalidImage = operator.executeNode(
        {"image": np.zeros((2, 2, 3), dtype=np.float32)},
        {"modelPath": str(_modelFile(tmp_path))},
        {},
    )

    assert invalidParam["error"]["code"] == "E_PARAM_INVALID"
    assert invalidImage["error"]["code"] == "E_INPUT_TYPE"

    invalidModelType = operator.executeNode(
        {"image": np.zeros((2, 2, 3), dtype=np.uint8)},
        {"modelPath": str(tmp_path / "model.pt")},
        {},
    )
    invalidDevice = operator.executeNode(
        {"image": np.zeros((2, 2, 3), dtype=np.uint8)},
        {"modelPath": str(_modelFile(tmp_path)), "device": "cuda"},
        {},
    )
    assert invalidModelType["error"]["code"] == "E_PARAM_INVALID"
    assert invalidDevice["error"]["code"] == "E_PARAM_INVALID"
