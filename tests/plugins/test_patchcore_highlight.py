"""Synthetic localization responses with actual CPU ORT, not accuracy acceptance."""
import json

import cv2
import numpy as np
import pytest

from emo_master.plugins.builtins.crop.operator import CropOperator
from emo_master.plugins.builtins.patchcore_inference import numeric
from emo_master.plugins.builtins.patchcore_inference.operator import PatchCoreInferenceOperator
from emo_master.plugins.builtins.patchcore_inference.visualization import highlightOverlay
from tests.plugins.patchcore_fixture import makeDeployment


@pytest.fixture
def op():
    value = PatchCoreInferenceOperator()
    yield value
    value.disposeOperator()


@pytest.mark.parametrize("shape", [(256, 256), (113, 251), (251, 113)])
@pytest.mark.parametrize("gray", [False, True])
def testRealOrtHighlightsRestoredPositionsAndPreservesNumbers(tmp_path, op, shape, gray):
    h, w = shape
    source = np.random.default_rng(23).integers(30, 220, (h+10, w+10) if gray else (h+10, w+10, 3), np.uint8)
    crop = CropOperator().executeNode({"image": source}, {"x": 4, "y": 5, "width": w, "height": h}, {})["outputs"]
    image = crop["image"]
    before = image.copy()
    bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if gray else image
    _, geometry = numeric.preprocess(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    left, _, top, _ = geometry["padding"]
    rh, rw = geometry["resized_size"]
    raw = np.full((1, 1, 256, 256), 1000, np.float32)
    raw[0, 0, top:top+rh, left:left+rw] = 2
    raw[0, 0, top+rh//4:top+3*rh//4, left+rw//2:left+3*rw//4] = 6
    rawBefore = raw.copy()
    model = makeDeployment(tmp_path / "model", rawMap=raw)
    params = {"modelPath": str(model), "outputAnomalyMap": True}
    inputs = {"image": image, "frame": crop["frame"]}
    legacy = op.executeNode(inputs, params, {})
    session = op._session
    result = op.executeNode(inputs, {**params, "overlayMode": "highlight"}, {})
    assert legacy["status"] == result["status"] == "ok", result
    assert op._session is session
    outputs = result["outputs"]
    for name in ("score", "threshold", "isAnomaly", "label", "frame", "anomalyMap"):
        assert outputs[name] == legacy["outputs"][name]
    assert outputs["score"] == outputs["threshold"] == 2.5 and outputs["label"] == "NG"
    assert outputs["frame"] == crop["frame"]
    np.testing.assert_array_equal(outputs["heatmap"], legacy["outputs"]["heatmap"])
    decoded = json.loads(json.dumps(outputs["anomalyMap"], allow_nan=False))
    assert np.asarray(decoded["values"], np.float32).tobytes() == rawBefore.tobytes()

    heat = numeric.restore(raw, geometry)
    selected = ((heat.astype(np.float64) - float(heat.min())) / float(heat.max() - heat.min()) >= .7).astype(np.uint8)
    interior = cv2.erode(selected, np.ones((7, 7), np.uint8)) != 0
    exterior = cv2.dilate(selected, np.ones((7, 7), np.uint8)) == 0
    overlay = outputs["overlay"]
    assert overlay.dtype == np.uint8 and overlay.shape == bgr.shape
    assert interior.any() and exterior.any()
    np.testing.assert_array_equal(overlay[exterior], bgr[exterior])
    np.testing.assert_array_equal(overlay[interior], cv2.addWeighted(
        bgr[interior], .6, np.full_like(bgr[interior], (0, 0, 255)), .4, 0))
    assert np.all(overlay == (0, 255, 255), axis=2).any()
    assert result["diagnostics"]["localization"]["status"] == "highlighted"
    np.testing.assert_array_equal(image, before)
    np.testing.assert_array_equal(raw, rawBefore)


@pytest.mark.parametrize("score", [1.0, 2.5])
@pytest.mark.parametrize("heatmap,overlay", [(True, True), (False, True), (True, False), (False, False)])
def testHighlightOutputOptionsAndOkBehavior(tmp_path, op, score, heatmap, overlay):
    path = makeDeployment(tmp_path / "model", score=score)
    image = np.arange(73*151, dtype=np.uint8).reshape(73, 151)
    result = op.executeNode({"image": image}, {"modelPath": str(path), "overlayMode": "highlight",
        "drawHeatmap": heatmap, "drawOverlay": overlay}, {})
    assert result["status"] == "ok", result
    outputs = result["outputs"]
    assert outputs["isAnomaly"] is (score >= 2.5)
    assert ("heatmap" in outputs) is heatmap and ("overlay" in outputs) is overlay
    assert "anomalyMap" not in outputs
    assert set(result["diagnostics"]["imageOutputMessages"]) == {
        port for port, enabled in (("heatmap", heatmap), ("overlay", overlay)) if not enabled}
    if overlay and score < 2.5:
        np.testing.assert_array_equal(outputs["overlay"], cv2.cvtColor(image, cv2.COLOR_GRAY2BGR))
        assert not np.shares_memory(outputs["overlay"], image)
        assert result["diagnostics"]["localization"]["status"] == "not_anomalous"
    if not overlay:
        assert "localization" not in result["diagnostics"]


@pytest.mark.parametrize("shape", [(256, 256), (113, 251), (251, 113)])
def testUniformNgMapDoesNotInventALocation(tmp_path, op, shape):
    raw = np.full((1, 1, 256, 256), .2, np.float32)
    path = makeDeployment(tmp_path / "model", rawMap=raw)
    image = np.full((*shape, 3), 160, np.uint8)
    result = op.executeNode({"image": image}, {"modelPath": str(path), "overlayMode": "highlight"}, {})
    assert result["status"] == "ok", result
    assert result["outputs"]["label"] == "NG" and result["outputs"]["isAnomaly"] is True
    assert result["diagnostics"]["localization"]["status"] == "uniform_response"
    np.testing.assert_array_equal(result["outputs"]["overlay"], image)
    assert not np.shares_memory(result["outputs"]["overlay"], image)


def testRelativeThresholdChangesDisplayOnlyAndUsesUnquantizedMap(tmp_path, op):
    raw = np.full((1, 1, 256, 256), 2., np.float32)
    raw[0, 0, 30:80, 30:80] = 4.8001
    raw[0, 0, 100:150, 30:80] = 4.7999
    raw[0, 0, 180:230, 180:230] = 6
    path = makeDeployment(tmp_path / "model", rawMap=raw)
    image = np.full((256, 256, 3), 180, np.uint8)
    params = {"modelPath": str(path), "overlayMode": "highlight", "outputAnomalyMap": True}
    results = [op.executeNode({"image": image}, {**params, "highlightThreshold": t}, {})
               for t in (.5, .7, .9, 1)]
    assert all(result["status"] == "ok" for result in results), results
    for result in results:
        for key in ("score", "threshold", "isAnomaly", "label", "frame", "anomalyMap"):
            assert result["outputs"][key] == results[0]["outputs"][key]
        np.testing.assert_array_equal(result["outputs"]["heatmap"], results[0]["outputs"]["heatmap"])
        np.testing.assert_array_equal(result["outputs"]["overlay"][200, 200], (108, 108, 210))
    assert np.any(results[0]["outputs"]["overlay"][120, 50] != image[120, 50])
    np.testing.assert_array_equal(results[1]["outputs"]["overlay"][50, 50], (108, 108, 210))
    np.testing.assert_array_equal(results[1]["outputs"]["overlay"][120, 50], image[120, 50])
    np.testing.assert_array_equal(results[2]["outputs"]["overlay"][50, 50], image[50, 50])


def testMultipleRegionsHolesAndSmallEdgeResponsesRemainVisible():
    image = np.full((256, 256, 3), 180, np.uint8)
    heat = np.zeros((256, 256), np.float32)
    heat[32:112, 32:112] = 1
    heat[55:89, 55:89] = 0
    heat[145:205, 145:205] = 1
    heat[:12, :12] = 1
    heat[225, 225] = 1
    before = heat.copy()
    overlay, localized = highlightOverlay(image, heat, .7)
    assert localized
    np.testing.assert_array_equal(overlay[70, 70], image[70, 70])
    for y, x in ((42, 42), (175, 175), (5, 5)):
        np.testing.assert_array_equal(overlay[y, x], (108, 108, 210))
    np.testing.assert_array_equal(overlay[225, 225], (0, 255, 255))
    np.testing.assert_array_equal(overlay[245, 245], image[245, 245])
    np.testing.assert_array_equal(heat, before)


@pytest.mark.parametrize("value", [0, -.1, 1.01, np.nan, np.inf, True, ".7", None])
def testInvalidActiveHighlightThresholdFailsBeforeModelAccess(op, value):
    result = op.executeNode({"image": np.zeros((2, 3, 3), np.uint8)},
        {"modelPath": "not-a-model", "overlayMode": "highlight", "highlightThreshold": value}, {})
    assert result["error"]["code"] == "E_PARAM_INVALID", result
    assert op._session is None


@pytest.mark.parametrize("params", [
    {"overlayMode": "heatmap", "highlightThreshold": "unfinished"},
    {"drawOverlay": False, "overlayMode": "unfinished", "highlightThreshold": "unfinished"},
])
def testInactiveDisplaySettingsDoNotBlockInference(tmp_path, op, params):
    path = makeDeployment(tmp_path / "model")
    result = op.executeNode({"image": np.zeros((11, 15, 3), np.uint8)}, {"modelPath": str(path), **params}, {})
    assert result["status"] == "ok", result


@pytest.mark.parametrize("value", ["other", None, [], {}])
def testInvalidActiveOverlayMode(op, value):
    assert op.validateParams({"modelPath": "not-a-model", "overlayMode": value})["code"] == "E_PARAM_INVALID"
