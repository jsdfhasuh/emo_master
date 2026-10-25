from copy import deepcopy
from importlib import import_module
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from emo_master.core.project.snapshots import _parameters
from emo_master.plugins.builtins._operator_modes import INPUT_CHOICES, MODE_ENTRIES, currentModeSchema, requiredChoiceInput
from emo_master.plugins.builtins.blur.operator import BlurOperator
from emo_master.plugins.builtins.collection_sort.operator import CollectionSortOperator
from emo_master.plugins.builtins.collection_select.operator import CollectionSelectOperator
from emo_master.plugins.builtins._coordinate_operators import CoordinateCalculatorOperator
from emo_master.plugins.builtins._geometry_bridges import GeometryPointsOperator
from emo_master.plugins.builtins.roi.operator import RoiOperator
from emo_master.plugins.builtins.resize.operator import ResizeOperator
from tests.plugins.test_collection_operators import _detections, _blobs, _newCollections, _space
from emo_master.core.contracts.geometry2d import Point2D


def operator(operatorId):
    module, name = MODE_ENTRIES[operatorId].split(":")
    return getattr(import_module("emo_master.plugins.builtins." + module), name)()


@pytest.mark.parametrize("kind", list(INPUT_CHOICES["vision.collection.sort"][1]))
def testAutomaticSortSupportsEveryCollectionAndRetainsExplicitLegacyKeys(kind):
    collections = dict(blobs=_blobs(), detections=_detections(), **_newCollections())
    op = CollectionSortOperator()
    result = op.executeNode({kind: collections[kind]}, {"collectionType": kind}, {})
    assert result["status"] == "ok", result
    assert set(result["outputs"]) == {"sorted" + kind.capitalize()}
    if kind == "detections":
        assert result["outputs"]["sortedDetections"]["items"][0]["id"] == "d2"
        assert op.executeNode({kind: collections[kind]}, {"key": "area"}, {})["status"] == "error"
        assert op.validateParams({"collectionType": kind, "key": "area"}) is not None


@pytest.mark.parametrize("operatorId", list(INPUT_CHOICES))
def testExplicitInputTypeRejectsAnotherInputAndPreservesParams(operatorId):
    op = operator(operatorId)
    selector, choices = INPUT_CHOICES[operatorId]
    selected = next(iter(choices))
    other = next(name for name in choices if name != selected)
    params = {selector: selected}
    before = deepcopy(params)
    result = op.executeNode({other: {}}, params, {})
    assert result["status"] == "error"
    assert result["error"]["code"] == "E_INPUT_SHAPE"
    assert params == before


@pytest.mark.parametrize("cls,params,inputs", [
    (BlurOperator, {"mode": "mean", "sigmaColor": "unfinished", "diameter": 0},
     {"image": np.zeros((12, 12), np.uint8)}),
    (BlurOperator, {"mode": "bilateral", "kernelSize": 2, "sigmaX": "unfinished"},
     {"image": np.zeros((12, 12), np.uint8)}),
    (CollectionSelectOperator, {"collectionType": "detections", "mode": "first", "index": "unfinished", "count": 0},
     {"detections": _detections()}),
    (CoordinateCalculatorOperator, {"mode": "measure", "rotationDegrees": "unfinished", "pivotX": "draft"},
     {"points": [Point2D(1, 2, _space()).toPayload()]}),
    (CoordinateCalculatorOperator, {"mode": "transform", "pivot": "origin", "pivotX": "unfinished"},
     {"points": [Point2D(1, 2, _space()).toPayload()]}),
    (RoiOperator, {"roiType": "bbox", "points": "unfinished", "angleDegrees": "draft"},
     {"image": np.zeros((12, 12), np.uint8)}),
    (ResizeOperator, {"mode": "fit", "width": 6, "height": 6, "padValue": "unfinished"},
     {"image": np.zeros((12, 12), np.uint8)}),
])
def testInactiveDraftsSurviveNormalizationAndDoNotAffectExecution(cls, params, inputs):
    before = deepcopy(params)
    normalized = _parameters(params, cls.meta.paramSchema, "params")
    assert cls().validateParams(normalized) is None
    result = cls().executeNode(inputs, normalized, {})
    assert result["status"] == "ok", result
    assert params == before
    assert all(normalized[key] == value for key, value in before.items())


def testBlurActiveConstraintsStillApplyAndPixelsMatch():
    image = np.arange(144, dtype=np.uint8).reshape(12, 12)
    op = BlurOperator()
    result = op.executeNode({"image": image}, {"mode": "mean", "kernelSize": 3, "sigmaColor": 0}, {})
    np.testing.assert_array_equal(result["outputs"]["image"], cv2.blur(image, (3, 3)))
    assert op.validateParams({"mode": "bilateral", "sigmaColor": 0}) is not None
    assert CollectionSelectOperator().validateParams({"mode": "index", "index": -1}) is not None
    assert CoordinateCalculatorOperator().validateParams({"mode": "transform", "rotationDegrees": 720}) is not None
    assert GeometryPointsOperator().validateParams({"feature": "circleCenter", "inputType": "bbox"}) is not None


def testAllChangedManifestSchemasMatchCurrentMetadata():
    root = Path("src/emo_master/plugins/builtins")
    manifests = {data["operatorId"]: data for path in root.glob("*/manifest.json")
                 if (data := json.loads(path.read_text(encoding="utf-8")))}
    for operatorId in MODE_ENTRIES:
        assert manifests[operatorId]["paramSchema"] == currentModeSchema(operatorId)


def testBoundInactiveValuesFollowTheSameModeValidation():
    from emo_master.core.project.global_variables import validateBoundValues, VariableError
    bindings = [{"parameterPath": ["sigmaColor"], "variableId": "sigma"}]
    schema = BlurOperator.meta.paramSchema
    validateBoundValues({"mode": "mean", "sigmaColor": 0}, bindings, schema)
    with pytest.raises(VariableError):
        validateBoundValues({"mode": "bilateral", "sigmaColor": 0}, bindings, schema)


@pytest.mark.parametrize('params,connected,dynamic,expected', [
    ({}, {'detections', 'index'}, (), 'detections'),
    ({'collectionType': 'detections'}, set(), (), 'detections'),
    ({'collectionType': 'detections'}, {'blobs'}, (), None),
    ({}, {'blobs', 'detections'}, (), None),
    ({'collectionType': 'detections'}, {'blobs', 'detections'}, (), None),
    ({'collectionType': ['invalid']}, {'detections'}, (), None),
    ({'collectionType': 'unfinished'}, {'detections'}, ('collectionType',), 'detections'),
])
def testChoiceRequirementDoesNotGateOptionalControlsOrConflictingInputs(params, connected, dynamic, expected):
    assert requiredChoiceInput('vision.collection.select', params, connected, dynamic) == expected
    assert requiredChoiceInput('vision.math.integer_add', params, connected, dynamic) is None
