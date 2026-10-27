from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
from PIL import Image
import pytest

from emo_master.core.contracts.geometry2d import BBox2D, CoordinateSpace2D
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.plugins.builtins.yolo_classification import operator as module
from emo_master.plugins.builtins.yolo_classification.onnx_backend import OnnxClassificationSession
from emo_master.plugins.builtins.yolo_classification.operator import YoloClassificationOperator
from emo_master.plugins.builtins.yolo_inference.onnx_backend import OnnxModelError, OnnxResultError


def makeModel(path, probabilities=(0.1, 0.9), *, task="classify", names=None, inputDependent=False):
    """A real tiny ONNX graph for contract/branch tests, never a helmet model."""
    names = names or {0: "without_helmet", 1: "with_helmet"}
    if inputDependent:
        nodes = [
            helper.make_node("ReduceMean", ["images"], ["mean"], axes=[1, 2, 3], keepdims=0),
            helper.make_node("Sub", ["one", "mean"], ["negative"]),
            helper.make_node("Concat", ["negative", "mean"], ["scores"], axis=0),
            helper.make_node("Unsqueeze", ["scores", "axis"], ["output"]),
        ]
        initializers = [numpy_helper.from_array(np.array([1], dtype=np.float32), "one"),
                        numpy_helper.from_array(np.array([0], dtype=np.int64), "axis")]
    else:
        nodes = [helper.make_node("Constant", [], ["output"],
                                  value=numpy_helper.from_array(np.array([probabilities], dtype=np.float32)))]
        initializers = []
    graph = helper.make_graph(nodes, "classification-contract",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 8, 8])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, len(probabilities)])],
        initializer=initializers)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)], ir_version=8)
    helper.set_model_props(model, {"task": task, "names": repr(names)})
    onnx.checker.check_model(model)
    onnx.save(model, path)
    return path


def testRegistrationMatchesManifestWithoutOpeningModels(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("registry/constructor/validation must not load a model")
    monkeypatch.setattr(module, "OnnxClassificationSession", forbidden)
    scan = PluginRegistry("0.6.1").scan(Path("src/emo_master/plugins/builtins"))
    assert not scan.rejectedOperators
    descriptor = scan.activeOperators["vision.inference.yolo_classification"]
    for key, value in asdict(YoloClassificationOperator.meta).items():
        assert getattr(descriptor.manifest, key) == value
    operator = descriptor.operatorClass()
    assert operator.validateParams({"modelPath": "missing.onnx"}) is None


@pytest.mark.parametrize("probabilities,threshold,label,accepted", [
    ((0.9, 0.1), 0.8, "without_helmet", True),
    ((0.1, 0.9), 0.8, "with_helmet", True),
    ((0.51, 0.49), 0.8, "without_helmet", False),
    ((0.5, 0.5), 0.5, "without_helmet", True),
])
def testRealOnnxPreservesTopOneAndFrame(tmp_path, probabilities, threshold, label, accepted):
    model = makeModel(tmp_path / "model.onnx", probabilities)
    image = np.zeros((19, 7, 3), dtype=np.uint8)
    frame = BBox2D(0, 0, 7, 19, CoordinateSpace2D(imageWidth=7, imageHeight=19, sourceId="camera",
                                               transformToSource=(1, 0, 0, 1, 25, 38)))
    operator = YoloClassificationOperator()
    try:
        result = operator.executeNode({"image": image, "frame": frame.toPayload()},
                                      {"modelPath": str(model), "confidence": threshold}, {})
        assert result["status"] == "ok", result
        output = result["outputs"]
        assert output["label"] == label
        assert output["accepted"] is accepted
        assert output["acceptedLabel"] == (label if accepted else "")
        assert output["confidence"] == pytest.approx(max(probabilities))
        assert output["image"] is image
        assert BBox2D.fromPayload(output["frame"]) == frame
        assert output["result"]["threshold"] == threshold
        assert len(output["result"]["probabilities"]) == 2
    finally:
        operator.disposeOperator()


def testFullFramePreprocessingMatchesTrainingPillowPixels(tmp_path):
    session = OnnxClassificationSession(makeModel(tmp_path / "model.onnx"))
    image = np.random.default_rng(30).integers(0, 256, size=(43, 17, 3), dtype=np.uint8)
    before = image.copy()
    expected = np.asarray(Image.fromarray(image[:, :, ::-1]).resize((8, 8), Image.Resampling.BILINEAR),
                          dtype=np.float32).transpose(2, 0, 1)[None] / np.float32(255)
    np.testing.assert_array_equal(session.prepareInput(image), expected)
    np.testing.assert_array_equal(image, before)
    assert not np.array_equal(expected[0].transpose(1, 2, 0),
                              cv2.resize(image[:, :, ::-1], (8, 8)).astype(np.float32) / 255)
    assert session.prepareInput(image[:, :, 0]).shape == (1, 3, 8, 8)
    session.close()


@pytest.mark.parametrize("values", [(2, -1), (float("nan"), 0.5), (0.6, 0.6)])
def testRejectsInvalidProbabilities(tmp_path, values):
    session = OnnxClassificationSession(makeModel(tmp_path / "model.onnx", values))
    try:
        with pytest.raises(OnnxResultError):
            session.predict(np.zeros((8, 8, 3), dtype=np.uint8))
    finally:
        session.close()


@pytest.mark.parametrize("kwargs", [{"task": "detect"}, {"names": {1: "with_helmet", 2: "without_helmet"}},
                                     {"names": {0: "same", 1: "same"}}, {"names": {0: "only"}}])
def testRejectsWrongModelContracts(tmp_path, kwargs):
    with pytest.raises(OnnxModelError):
        OnnxClassificationSession(makeModel(tmp_path / "wrong.onnx", **kwargs))


@pytest.mark.parametrize("params", [{"modelPath": "weights.pt"}, {"modelPath": ""},
    {"confidence": float("nan")}, {"confidence": True}, {"confidence": -0.1},
    {"confidence": 1.1}, {"preprocess": "center_crop"}])
def testInvalidParameters(params):
    assert YoloClassificationOperator().validateParams({"modelPath": "weights.onnx", **params})


def testSessionIsReusedAndReleasedOnChangeAndDispose(tmp_path, monkeypatch):
    instances = []
    class Session:
        names, width, height = ["without_helmet", "with_helmet"], 8, 8
        def __init__(self, path):
            self.closed = False
            instances.append(self)
        def predict(self, image):
            return np.array([0.1, 0.9])
        def close(self):
            self.closed = True
    monkeypatch.setattr(module, "OnnxClassificationSession", Session)
    model = makeModel(tmp_path / "model.onnx")
    operator = YoloClassificationOperator()
    inputs, params = {"image": np.zeros((8, 8, 3), dtype=np.uint8)}, {"modelPath": str(model)}
    for _ in range(3):
        assert operator.executeNode(inputs, params, {"jobId": "one"})["status"] == "ok"
    assert len(instances) == 1
    assert operator.executeNode(inputs, params, {"jobId": "two"})["status"] == "ok"
    assert len(instances) == 2 and instances[0].closed
    operator.disposeOperator()
    assert instances[1].closed and operator._session is None


def testCancellationIsPropagatedAfterInference(tmp_path, monkeypatch):
    class Cancelled(RuntimeError):
        pass
    flag = []
    def check():
        if flag:
            raise Cancelled("stop")
    class Session:
        def __init__(self, path):
            pass
        def predict(self, image):
            flag.append(True)
            return np.array([0.1, 0.9])
        def close(self):
            pass
    monkeypatch.setattr(module, "OnnxClassificationSession", Session)
    model = makeModel(tmp_path / "model.onnx")
    operator = YoloClassificationOperator()
    with pytest.raises(Cancelled):
        operator.executeNode({"image": np.zeros((8, 8, 3), dtype=np.uint8)},
                             {"modelPath": str(model)}, {"raiseIfCancellationRequested": check})
    operator.disposeOperator()


def testMissingModelAndInvalidInputs(tmp_path):
    operator = YoloClassificationOperator()
    params = {"modelPath": str(tmp_path / "missing.onnx")}
    assert operator.executeNode({}, params, {})["error"]["code"] == "E_INPUT_MISSING"
    assert operator.executeNode({"image": "wrong"}, params, {})["error"]["code"] == "E_INPUT_TYPE"
    assert operator.executeNode({"image": np.zeros((1, 1, 4), dtype=np.uint8)}, params, {})["error"]["code"] == "E_INPUT_SHAPE"
    assert operator.executeNode({"image": np.zeros((1, 1, 3), dtype=np.uint8)}, params, {})["error"]["code"] == "E_MODEL_NOT_FOUND"
