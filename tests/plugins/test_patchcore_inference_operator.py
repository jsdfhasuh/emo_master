from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationRequested
from emo_master.plugins.builtins.crop.operator import CropOperator
from emo_master.plugins.builtins.patchcore_inference import bundle, numeric, operator
from emo_master.plugins.builtins.patchcore_inference.onnx_backend import PatchCoreSession, ResultError
from tests.plugins.patchcore_fixture import makeDeployment, wrapTransfer, writeJson, PREPROCESS, entry


@pytest.fixture
def op():
    value = operator.PatchCoreInferenceOperator()
    yield value
    value.disposeOperator()


@pytest.mark.parametrize("version,profile,external,nested", [
    (1, "dino_fp32_p5_v1", False, False),
    (1, "dino_fp32_features_fp64_distance_p5_v1", True, False),
    (2, "dino_fp32_features_fp64_distance_p5_v1", True, True),
])
@pytest.mark.parametrize("transfer", [False, True])
def testRealOrtDeploymentAndTransfer(tmp_path, op, version, profile, external, nested, transfer):
    path = makeDeployment(tmp_path / "\u6a21\u578b", version=version, profile=profile, external=external, nested=nested)
    if transfer:
        wrapTransfer(path)
    filesBefore = {file.relative_to(path).as_posix(): entry(file) for file in path.rglob("*") if file.is_file()}
    image = np.random.default_rng(3).integers(0, 256, (13, 29, 3), np.uint8)
    original = image.copy()
    result = op.executeNode({"image": image}, {"modelPath": str(path), "outputAnomalyMap": True}, {})
    assert result["status"] == "ok", result
    outputs = result["outputs"]
    assert (outputs["score"], outputs["threshold"], outputs["isAnomaly"], outputs["label"]) == (2.5, 2.5, True, "NG")
    for key in ("heatmap", "overlay"):
        assert outputs[key].shape == image.shape and outputs[key].dtype == np.uint8
    decoded = json.loads(json.dumps(outputs["anomalyMap"], allow_nan=False))
    raw = np.asarray(decoded["values"], dtype=np.float32).reshape(1, 1, 256, 256)
    expected = np.arange(256 * 256, dtype=np.float32).reshape(raw.shape) / 20000
    np.testing.assert_array_equal(raw, expected)
    heat = numeric.restore(expected, decoded["geometry"])
    color = cv2.applyColorMap((255*(heat-heat.min())/max(float(heat.max()-heat.min()), 1e-12)).astype(np.uint8), cv2.COLORMAP_JET)
    np.testing.assert_array_equal(outputs["heatmap"], color)
    np.testing.assert_array_equal(outputs["overlay"], cv2.addWeighted(image, .6, color, .4, 0))
    np.testing.assert_array_equal(image, original)
    assert op._session.session.get_providers() == ["CPUExecutionProvider"]
    options = op._session.session.get_session_options()
    assert options.intra_op_num_threads == 2 and options.inter_op_num_threads == 1
    assert str(options.graph_optimization_level).endswith("ORT_ENABLE_BASIC")
    assert str(options.execution_mode).endswith("ORT_SEQUENTIAL")
    assert result["diagnostics"]["thresholdSource"] == "deployment"
    assert {file.relative_to(path).as_posix(): entry(file) for file in path.rglob("*") if file.is_file()} == filesBefore


@pytest.mark.parametrize("heatmap,overlay", [(True, True), (False, True), (True, False), (False, False)])
def testOutputOptionsGrayAndCroppedFrame(tmp_path, op, heatmap, overlay):
    path = makeDeployment(tmp_path / "model", threshold=3.0)
    gray = np.arange(30*40, dtype=np.uint8).reshape(30, 40)
    crop = CropOperator().executeNode({"image": gray}, {"x": 2, "y": 3, "width": 11, "height": 7}, {})
    assert crop["status"] == "ok", crop
    inputs = {"image": crop["outputs"]["image"], "frame": crop["outputs"]["frame"]}
    result = op.executeNode(inputs, {"modelPath": str(path), "drawHeatmap": heatmap, "drawOverlay": overlay}, {})
    assert result["status"] == "ok", result
    assert result["outputs"]["frame"] == inputs["frame"]
    assert result["outputs"]["label"] == "OK" and result["outputs"]["isAnomaly"] is False
    assert "anomalyMap" not in result["outputs"]
    assert ("heatmap" in result["outputs"]) is heatmap
    assert ("overlay" in result["outputs"]) is overlay
    assert set(result["diagnostics"]["imageOutputMessages"]) == {p for p, on in (("heatmap", heatmap), ("overlay", overlay)) if not on}


@pytest.mark.parametrize("mutation", ["format", "profile", "algorithm", "qualification", "calibration", "threshold", "preprocess", "task", "report", "external", "digest", "schema", "identity", "target"])
def testBadPackageNeverReturnsOk(tmp_path, op, mutation):
    path = makeDeployment(tmp_path / "bad", scoreShape=(2,) if mutation == "schema" else (1,))
    manifest = json.loads((path / "deployment.json").read_text())
    if mutation in {"format", "profile", "algorithm", "qualification"}:
        manifest[mutation] = "unsupported"
    elif mutation == "calibration":
        manifest["deployment_calibration_state"] = "AWAITING_ONNX_RGB"
    elif mutation == "threshold":
        manifest["threshold"] = 0.5
    elif mutation == "preprocess":
        manifest["preprocess"]["antialias"] = True
    elif mutation == "task":
        manifest["task_sha256"] = "a" * 64
    elif mutation == "identity":
        manifest["task_contract"]["identity"] = {}
        manifest["task_sha256"] = bundle.contentHash(manifest["task_contract"])
    elif mutation == "target":
        manifest["target"]["optimization"] = "ALL"
    elif mutation == "report":
        (path / "verification.json").write_text("{}")
    elif mutation == "external":
        (path / "weights.bin").unlink()
    elif mutation == "digest":
        data = bytearray((path / "weights.bin").read_bytes())
        data[-1] ^= 1
        (path / "weights.bin").write_bytes(data)
    writeJson(path / "deployment.json", manifest)
    result = op.executeNode({"image": np.zeros((7, 9, 3), np.uint8)}, {"modelPath": str(path)}, {})
    assert result["status"] == "error", result
    assert result["error"]["code"] == "E_MODEL_LOAD_FAILED"
    assert "outputs" not in result and op._session is None


@pytest.mark.parametrize("score,mapValue", [(np.nan, 0), (np.inf, 0), (2.5, np.nan), (2.5, np.inf)])
def testNonfiniteActualOrtOutputsFail(tmp_path, op, score, mapValue):
    path = makeDeployment(tmp_path / "bad-output", score=score, rawMap=np.full((1, 1, 256, 256), mapValue, np.float32))
    result = op.executeNode({"image": np.zeros((4, 5), np.uint8)}, {"modelPath": str(path)}, {})
    assert result["error"]["code"] == "E_RESULT_INVALID", result
    assert "outputs" not in result


def testThresholdEqualAndAdjacentBinary64():
    score = np.float32(.2)
    assert numeric.decision(score, float(score))
    assert not numeric.decision(score, float(np.nextafter(np.float64(score), np.inf)))
    assert numeric.decision(score, float(np.nextafter(np.float64(score), -np.inf)))
    for invalid in (True, None, np.nan, np.inf):
        with pytest.raises(ValueError):
            numeric.decision(score, invalid)


@pytest.mark.parametrize("direction,expected", [(None, True), (np.inf, False), (-np.inf, True)])
@pytest.mark.parametrize("overlayMode", ["heatmap", "highlight"])
def testRealOrtDecisionUsesBinary64Threshold(tmp_path, op, direction, expected, overlayMode):
    score = np.float32(.2)
    threshold = float(score) if direction is None else float(np.nextafter(np.float64(score), direction))
    path = makeDeployment(tmp_path / "model", score=score, threshold=threshold)
    result = op.executeNode({"image": np.zeros((7, 9), np.uint8)}, {"modelPath": str(path), "overlayMode": overlayMode}, {})
    assert result["status"] == "ok", result
    assert result["outputs"]["score"] == float(score) and result["outputs"]["threshold"] == threshold
    assert result["outputs"]["isAnomaly"] is expected


def testRawMapJsonRetainsFloat32BitsWithoutVisualization(tmp_path, op):
    values = np.array([0., -0., np.nextafter(np.float32(0), np.float32(1)),
                       np.finfo(np.float32).max, -np.finfo(np.float32).max, np.float32(.2)], np.float32)
    raw = np.resize(values, (1, 1, 256, 256))
    path = makeDeployment(tmp_path / "model", rawMap=raw)
    result = op.executeNode({"image": np.zeros((7, 9), np.uint8)},
        {"modelPath": str(path), "drawHeatmap": False, "drawOverlay": False, "outputAnomalyMap": True}, {})
    assert result["status"] == "ok", result
    decoded = json.loads(json.dumps(result["outputs"]["anomalyMap"], allow_nan=False))
    assert np.asarray(decoded["values"], np.float32).tobytes() == raw.tobytes()


def testSessionReuseSwitchFailureAndDispose(tmp_path, op, monkeypatch):
    first = makeDeployment(tmp_path / "first")
    second = makeDeployment(tmp_path / "second", score=1.)
    inputs = {"image": np.zeros((11, 21, 3), np.uint8)}
    assert op.executeNode(inputs, {"modelPath": str(first)}, {})["status"] == "ok"
    session = op._session
    with monkeypatch.context() as patch:
        patch.setattr(bundle, "fileHash", lambda *_args: pytest.fail("must not hash per frame"))
        assert op.executeNode(inputs, {"modelPath": str(first)}, {})["outputs"]["score"] == 2.5
        assert op._session is session
    assert op.executeNode(inputs, {"modelPath": str(second)}, {})["outputs"]["score"] == 1.
    assert session.session is None
    session = op._session
    assert op.executeNode(inputs, {"modelPath": str(tmp_path / "missing")}, {})["error"]["code"] == "E_MODEL_NOT_FOUND"
    assert session.session is None and op._session is None
    assert op.executeNode(inputs, {"modelPath": "first"}, {"workspacePath": str(tmp_path)})["status"] == "ok"
    session = op._session
    op.disposeOperator()
    op.disposeOperator()
    assert session.session is None


def testCancellationIsNotTurnedIntoPredictionFailure(tmp_path, op):
    path = makeDeployment(tmp_path / "model")
    def cancel():
        raise CancellationRequested("stop")
    with pytest.raises(CancellationRequested):
        op.executeNode({"image": np.zeros((4, 5), np.uint8)}, {"modelPath": str(path)},
                       {"raiseIfCancellationRequested": cancel})
    assert op._session is None


@pytest.mark.parametrize("image", [None, np.zeros((2, 3), np.float32), np.zeros((0, 3), np.uint8),
                                    np.zeros((2, 3, 4), np.uint8), np.zeros((2,), np.uint8)])
def testInvalidImagesFailBeforeModelAccess(op, image):
    result = op.executeNode({"image": image}, {"modelPath": "not-a-model"}, {})
    assert result["error"]["code"] in {"E_INPUT_TYPE", "E_INPUT_SHAPE"}


def testTransferManifestBindingAndPathTraversal(tmp_path):
    path = wrapTransfer(makeDeployment(tmp_path / "model"))
    transfer = json.loads((path / "transfer.json").read_text())
    transfer["manifest_sha256"] = "0" * 64
    writeJson(path / "transfer.json", transfer)
    with pytest.raises(bundle.ModelError, match="manifest digest"):
        bundle.loadDeployment(path)
    for value in ("../outside", "C:/outside", "weights\\other", "/absolute", "a//b"):
        with pytest.raises(bundle.ModelError, match="relative"):
            bundle.relativeFile(path, value)


def testLazyBackendImports():
    code = ("import sys; sys.path.insert(0, 'src'); from emo_master.plugins.builtins.patchcore_inference.operator import PatchCoreInferenceOperator; "
            "assert not ({'onnx', 'torch', 'anomalib', 'torchvision', 'transformers'} & set(sys.modules))")
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("gray", [False, True])
def testInputColorConversionReachesRealBackend(tmp_path, op, monkeypatch, gray):
    path = makeDeployment(tmp_path / "model")
    image = np.random.default_rng(19).integers(0, 256, (19, 31) if gray else (19, 31, 3), np.uint8)
    original = image.copy()
    predict = PatchCoreSession.predict
    received = []

    def capture(session, rgb):
        received.append(rgb.copy())
        return predict(session, rgb)

    monkeypatch.setattr(PatchCoreSession, "predict", capture)
    assert op.executeNode({"image": image}, {"modelPath": str(path)}, {})["status"] == "ok"
    expected = np.repeat(image[:, :, None], 3, axis=2) if gray else image[:, :, ::-1]
    np.testing.assert_array_equal(received[0], expected)
    np.testing.assert_array_equal(image, original)


def testTransferMustBindAllRequiredFiles(tmp_path):
    path = wrapTransfer(makeDeployment(tmp_path / "model"))
    transfer = json.loads((path / "transfer.json").read_text())
    transfer["files"] = [row for row in transfer["files"] if row["path"] != "weights.bin"]
    writeJson(path / "transfer.json", transfer)
    with pytest.raises(bundle.ModelError, match="transfer inventory"):
        bundle.loadDeployment(path)


@pytest.mark.parametrize("version,field,value", [
    (1, "graph_files", []), (1, "source_model_sha256", "e" * 64), (1, "sample_sha256", []),
    (1, "scores", []), (1, "method", {"quantile": .5}),
    (2, "graph_files", []), (2, "samples", []), (2, "policy", {}),
    (2, "score_rows_sha256", "0" * 64), (2, "method", {"mode": "normal_quantile", "quantile": .5}),
])
def testCalibrationBindingsCheckedEvenWithUpdatedFileDigests(tmp_path, version, field, value):
    path = makeDeployment(tmp_path / "model", version=version)
    manifest = json.loads((path / "deployment.json").read_text())
    threshold = json.loads((path / "threshold.json").read_text())
    threshold[field] = value
    writeJson(path / "threshold.json", threshold)
    manifest["threshold_file"] = entry(path / "threshold.json")
    report = json.loads((path / "verification.json").read_text())
    report["threshold_sha256"] = manifest["threshold_file"]["sha256"]
    writeJson(path / "verification.json", report)
    manifest["verification_report"] = entry(path / "verification.json")
    writeJson(path / "deployment.json", manifest)
    with pytest.raises(bundle.ModelError, match="binding mismatch"):
        bundle.loadDeployment(path)


@pytest.mark.parametrize("kwargs", [{"scoreDtype": np.float64}, {"scoreShape": ()},
    {"rawMap": np.zeros((1, 1, 128, 128), np.float32)}, {"rawMap": np.zeros((1, 1, 256, 256), np.float64)}])
def testActualGraphRejectsWrongOutputTypeAndShape(tmp_path, op, kwargs):
    path = makeDeployment(tmp_path / "model", **kwargs)
    result = op.executeNode({"image": np.zeros((4, 5), np.uint8)}, {"modelPath": str(path)}, {})
    assert result["error"]["code"] == "E_MODEL_LOAD_FAILED", result
    assert "outputs" not in result


def testMissingOnnxIsExplicitBackendFailure(tmp_path, op, monkeypatch):
    path = makeDeployment(tmp_path / "model")
    monkeypatch.setitem(sys.modules, "onnx", None)
    result = op.executeNode({"image": np.zeros((4, 5), np.uint8)}, {"modelPath": str(path)}, {})
    assert result["error"]["code"] == "E_BACKEND_UNAVAILABLE", result
    assert "outputs" not in result


def testOrtNestedExternalInitializerLimitationDoesNotChangeOptimization(tmp_path, op):
    # ORT 1.23.2 BASIC cannot constant-fold this synthetic nested initializer.
    # Keep the initial failing probe; do not silently switch to DISABLE or chdir.
    import onnxruntime as ort
    path = makeDeployment(tmp_path / "nested", nested=True, nestedInitializer=True)
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    reference = ort.InferenceSession(str(path / "model.onnx"), options, providers=["CPUExecutionProvider"])
    assert reference.run(["pred_score"], {"image": np.zeros((1, 3, 256, 256), np.float32)})[0][0] == 2.5
    result = op.executeNode({"image": np.zeros((4, 5), np.uint8)}, {"modelPath": str(path)}, {})
    assert result["error"]["code"] == "E_MODEL_LOAD_FAILED"
    assert "RuntimeException" in result["error"]["message"]
    assert "outputs" not in result


def testFixedPreprocessingContract():
    assert numeric.PREPROCESS == PREPROCESS
    rgb = np.zeros((256, 256, 3), np.uint8)
    rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2] = 255, 128, 0
    tensor, geometry = numeric.preprocess(rgb)
    expected = (np.array([1, np.float32(128)/np.float32(255), 0], np.float32) - np.array([.485, .456, .406], np.float32)) / np.array([.229, .224, .225], np.float32)
    np.testing.assert_array_equal(tensor[0, :, 99, 55], expected)
    assert tensor.dtype == np.float32 and tensor.flags.c_contiguous
    assert geometry == {"original_size": [256, 256], "resized_size": [256, 256], "padding": [0, 0, 0, 0]}


@pytest.mark.parametrize("result", [[np.zeros((2,), np.float32), np.zeros((1, 1, 256, 256), np.float32)],
                                   [np.zeros((1,), np.float64), np.zeros((1, 1, 256, 256), np.float32)],
                                   [np.zeros((1,), np.float32)], [None, None]])
def testBackendRejectsMalformedOutput(tmp_path, monkeypatch, result):
    from types import SimpleNamespace
    session = PatchCoreSession(makeDeployment(tmp_path / "model"))
    try:
        session.session = SimpleNamespace(run=lambda *_args: deepcopy(result))
        with pytest.raises(ResultError):
            session.predict(np.zeros((2, 3, 3), np.uint8))
    finally:
        session.close()
