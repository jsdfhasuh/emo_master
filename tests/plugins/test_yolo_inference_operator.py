from __future__ import annotations

import json
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
    assert set(second["outputs"]) == {"detections", "frame", "overlay"}
    np.testing.assert_array_equal(second["outputs"]["overlay"], overlay)
    assert np.any(overlay != image)
    assert not np.shares_memory(overlay, image)
    assert not np.any(image)
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
    np.testing.assert_array_equal(result["outputs"]["overlay"], np.zeros((4, 5, 3), dtype=np.uint8))


def testExplicitlyDisabledOverlayPreservesDetectionsAndReportsOmittedImages(tmp_path, monkeypatch):
    fake = _FakeModel(_result([[1, 1, 3, 3]], [.9], [0]))
    monkeypatch.setattr(yolo_module, "_createModel", lambda path: fake)
    operator = YoloInferenceOperator()
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    params = {"modelPath": str(_modelFile(tmp_path))}
    enabled = operator.executeNode({"image": image}, params, {})
    disabled = operator.executeNode({"image": image}, {**params, "drawOverlay": False}, {})
    assert disabled["status"] == "ok"
    assert set(disabled["outputs"]) == {"detections", "frame"}
    assert disabled["outputs"]["detections"] == enabled["outputs"]["detections"]
    messages = disabled["diagnostics"]["imageOutputMessages"]
    assert "未启用" in messages["overlay"] and "绘制叠加图" in messages["overlay"]
    assert "输出裁剪图" in messages["croppedImage"]
    assert "overlay" not in enabled["diagnostics"]["imageOutputMessages"]
    assert not np.any(image)


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


@pytest.mark.parametrize("targetIndex,expectedLeft", [(1, 6), (2, 3), (3, 0)])
@pytest.mark.parametrize("channels", [1, 3])
def testSelectedCropUsesDescendingConfidenceAndRawOwnedPixels(
    tmp_path, monkeypatch, targetIndex, expectedLeft, channels
):
    # Deliberately shuffled backend order; crop ranking must be explicit.
    fake = _FakeModel(_result([[0, 1, 2, 4], [6, 1, 8, 4], [3, 1, 5, 4]],
                              [.5, .95, .75], [0, 1, 0]))
    monkeypatch.setattr(yolo_module, "_createModel", lambda path: fake)
    source = np.arange(6 * 20 * channels, dtype=np.uint8).reshape(
        (6, 20) if channels == 1 else (6, 20, channels)
    )
    image = source[:, ::2]  # non-contiguous grayscale or BGR input
    before = image.copy()
    result = YoloInferenceOperator().executeNode(
        {"image": image},
        {"modelPath": str(_modelFile(tmp_path)), "outputCrop": True,
         "cropTargetIndex": targetIndex, "drawOverlay": True},
        {"nodeId": "yolo"},
    )
    assert result["status"] == "ok"
    outputs = result["outputs"]
    detections = DetectionCollection.fromPayload(outputs["detections"])
    assert [d.confidence for d in detections.items] == pytest.approx([.95, .75, .5])
    assert [d.bbox.x for d in detections.items] == [6, 3, 0]
    assert result["metrics"]["detectionCount"] == 3
    crop = outputs["croppedImage"]
    np.testing.assert_array_equal(crop, before[1:4, expectedLeft:expectedLeft + 2])
    np.testing.assert_array_equal(image, before)
    assert crop.flags.c_contiguous and not np.shares_memory(crop, image)
    assert not np.shares_memory(crop, outputs["overlay"])
    assert result["diagnostics"]["imageOutputMessages"] == {}
    assert outputs["overlay"].shape == (6, 10, 3)
    frame = BBox2D.fromPayload(outputs["croppedFrame"])
    assert (frame.width, frame.height) == (2, 3)
    assert frame.coordinateSpace.reference == "node:yolo"
    assert frame.coordinateSpace.mapPointToSource(0, 0) == (expectedLeft, 1)
    assert BBox2D.fromPayload(outputs["frame"]).coordinateSpace.imageWidth == 10


def testDetectionOrderIsStableForTiesWithoutEnablingCrop(tmp_path, monkeypatch):
    fake = _FakeModel(_result([[0, 0, 1, 1], [1, 0, 2, 1], [2, 0, 3, 1]],
                              [.8, .9, .9], [0, 1, 0]))
    monkeypatch.setattr(yolo_module, "_createModel", lambda path: fake)
    result = YoloInferenceOperator().executeNode(
        {"image": np.zeros((4, 4), dtype=np.uint8)},
        {"modelPath": str(_modelFile(tmp_path))}, {},
    )
    detections = DetectionCollection.fromPayload(result["outputs"]["detections"])
    assert [d.bbox.x for d in detections.items] == [1, 2, 0]
    assert set(result["outputs"]) == {"detections", "frame", "overlay"}


@pytest.mark.parametrize("transform", [
    {"transformToSource": (2, 0, 0, 3, 10, 20)},
    {"transformToSource": None, "homographyToSource": (2, 0, 10, 0, 3, 20, .01, .02, 1)},
])
def testCropRoundsClipsAndComposesExistingSourceTransform(tmp_path, monkeypatch, transform):
    fake = _FakeModel(_result([[1.8, -3.1, 6.2, 4.1]], [.9], [0]))
    monkeypatch.setattr(yolo_module, "_createModel", lambda path: fake)
    space = CoordinateSpace2D(sourceId="camera", imageWidth=8, imageHeight=6, **transform)
    frame = BBox2D(2, 1, 5, 4, space)  # padded input: only some pixels are valid
    image = np.arange(48, dtype=np.uint8).reshape(6, 8)
    result = YoloInferenceOperator().executeNode(
        {"image": image, "frame": frame.toPayload()},
        {"modelPath": str(_modelFile(tmp_path)), "outputCrop": True}, {},
    )
    assert result["status"] == "ok"
    np.testing.assert_array_equal(result["outputs"]["croppedImage"], image[0:5, 1:7])
    croppedFrame = BBox2D.fromPayload(result["outputs"]["croppedFrame"])
    assert (croppedFrame.x, croppedFrame.y, croppedFrame.width, croppedFrame.height) == (1, 1, 5, 4)
    assert (croppedFrame.coordinateSpace.imageWidth, croppedFrame.coordinateSpace.imageHeight) == (6, 5)
    assert croppedFrame.coordinateSpace.sourceId == "camera"
    assert croppedFrame.coordinateSpace.mapPointToSource(2, 3) == pytest.approx(space.mapPointToSource(3, 3))
    np.testing.assert_array_equal(image, np.arange(48, dtype=np.uint8).reshape(6, 8))


@pytest.mark.parametrize("boxes", [[], [[20, 20, 30, 30]]])
def testEmptyOrOutsideDetectionHasNoCropAndDoesNotReusePreviousFrame(tmp_path, monkeypatch, boxes):
    fake = _FakeModel(_result([[1, 1, 3, 3]], [.9], [0]))
    monkeypatch.setattr(yolo_module, "_createModel", lambda path: fake)
    operator = YoloInferenceOperator()
    params = {"modelPath": str(_modelFile(tmp_path)), "outputCrop": True}
    inputs = {"image": np.zeros((8, 8, 3), dtype=np.uint8)}
    assert operator.executeNode(inputs, params, {})["status"] == "ok"
    fake.result = _result(boxes, [.9] * len(boxes), [0] * len(boxes))
    result = operator.executeNode(inputs, params, {})
    assert result["error"]["code"] == "E_CROP_TARGET_NOT_FOUND"
    assert "0 个" in result["error"]["message"]
    assert "outputs" not in result


def testOutOfRangeCropNeverFallsBackToFirstDetection(tmp_path, monkeypatch):
    fake = _FakeModel(_result([[1, 1, 3, 3]], [.9], [0]))
    monkeypatch.setattr(yolo_module, "_createModel", lambda path: fake)
    result = YoloInferenceOperator().executeNode(
        {"image": np.zeros((8, 8), dtype=np.uint8)},
        {"modelPath": str(_modelFile(tmp_path)), "outputCrop": True, "cropTargetIndex": 2}, {},
    )
    assert result["error"]["code"] == "E_CROP_TARGET_NOT_FOUND"
    assert "序号为 2" in result["error"]["message"] and "1 个" in result["error"]["message"]
    assert "outputs" not in result


@pytest.mark.parametrize("boxes,index", [([], 1), ([[1, 1, 3, 3]], 2)])
def testMissingCropCanContinueWithoutStalePixelsOrLosingDetections(tmp_path, monkeypatch, boxes, index):
    fake = _FakeModel(_result([[1, 1, 3, 3]], [.9], [0]))
    monkeypatch.setattr(yolo_module, "_createModel", lambda path: fake)
    operator = YoloInferenceOperator()
    params = {"modelPath": str(_modelFile(tmp_path)), "outputCrop": True, "onMissingCrop": "skip"}
    inputs = {"image": np.zeros((8, 8, 3), dtype=np.uint8)}
    assert "croppedImage" in operator.executeNode(inputs, params, {})["outputs"]
    fake.result = _result(boxes, [.9] * len(boxes), [0] * len(boxes))
    result = operator.executeNode(inputs, {**params, "cropTargetIndex": index}, {})
    assert result["status"] == "ok"
    assert "error" not in result
    assert set(result["outputs"]) == {"detections", "frame", "overlay"}
    assert len(DetectionCollection.fromPayload(result["outputs"]["detections"]).items) == len(boxes)
    assert result["metrics"]["detectionCount"] == len(boxes)
    assert str(index) in result["diagnostics"]["imageOutputMessages"]["croppedImage"]
    assert "croppedFrame" in result["diagnostics"]["imageOutputMessages"]
    fake.result = _result([[2, 2, 5, 5]], [.9], [0])
    recovered = operator.executeNode(inputs, params, {})
    assert recovered["status"] == "ok" and recovered["outputs"]["croppedImage"].shape == (3, 3, 3)
    assert "croppedImage" not in recovered["diagnostics"]["imageOutputMessages"]


@pytest.mark.parametrize("policy", ["error", "skip"])
def testCropOutsideValidContentFailsWithoutFabricatedPixels(tmp_path, monkeypatch, policy):
    fake = _FakeModel(_result([[0, 0, 1, 1]], [.9], [0]))
    monkeypatch.setattr(yolo_module, "_createModel", lambda path: fake)
    frame = BBox2D(3, 3, 2, 2, CoordinateSpace2D(imageWidth=8, imageHeight=8))
    result = YoloInferenceOperator().executeNode(
        {"image": np.zeros((8, 8), dtype=np.uint8), "frame": frame.toPayload()},
        {"modelPath": str(_modelFile(tmp_path)), "outputCrop": True, "onMissingCrop": policy}, {},
    )
    assert result["error"]["code"] == "E_INPUT_SHAPE"
    assert "valid frame content" in result["error"]["message"]
    assert "outputs" not in result


@pytest.mark.parametrize("index", [0, -1, True, 1.5, "1", None])
def testCropIndexValidationOnlyAppliesWhenEnabled(index):
    params = {"modelPath": "model.onnx", "cropTargetIndex": index}
    operator = YoloInferenceOperator()
    assert operator.validateParams(params) is None
    assert operator.validateParams({**params, "outputCrop": True})["code"] == "E_PARAM_INVALID"


@pytest.mark.parametrize("policy", [None, True, "ignore", 1, []])
def testMissingCropPolicyValidationOnlyAppliesWhenEnabled(policy):
    params = {"modelPath": "model.onnx", "onMissingCrop": policy}
    operator = YoloInferenceOperator()
    assert operator.validateParams(params) is None
    assert operator.validateParams({**params, "outputCrop": True})["code"] == "E_PARAM_INVALID"


def testCropManifestMatchesRuntimeContractAndDefaults():
    manifest = json.loads(Path(yolo_module.__file__).with_name("manifest.json").read_text(encoding="utf-8"))
    meta = YoloInferenceOperator.meta
    for name in ("version", "inputPorts", "outputPorts", "paramSchema"):
        assert manifest[name] == getattr(meta, name)
    assert meta.paramSchema["properties"]["drawOverlay"]["default"] is True
    assert meta.paramSchema["properties"]["outputCrop"]["default"] is False
    assert meta.paramSchema["properties"]["cropTargetIndex"]["default"] == 1
    assert meta.paramSchema["properties"]["onMissingCrop"]["default"] == "error"
    assert YoloInferenceOperator().validateParams({"modelPath": "model.onnx", "outputCrop": "true"})["code"] == "E_PARAM_INVALID"
