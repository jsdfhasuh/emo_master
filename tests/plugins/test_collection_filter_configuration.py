from copy import deepcopy
import json
from pathlib import Path

import pytest

from emo_master.core.contracts.geometry2d import BBox2D
from emo_master.plugins.builtins.collection_filter.configuration import (
    COLLECTION_TYPES,
    FILTER_GROUPS,
    effectiveFilterParams,
    prepareFilterParams,
)
from emo_master.plugins.builtins.collection_filter.operator import CollectionFilterOperator
from tests.plugins.test_collection_operators import _blobs, _detections, _newCollections, _space


_CASES = (
    ("blobs", {"minArea": 25, "blobLabels": [2], "contourMode": "present",
               "circularityEnabled": True, "minCircularity": 0.7}, "blobId", "large"),
    ("detections", {"minConfidence": 0.8, "classIds": [1], "detectionLabels": ["bad"],
                    "geometryTypes": ["bbox2d"]}, "detectionId", "d2"),
    ("contours", {"minArea": 10, "minPerimeter": 15, "holeMode": "outer"}, "contourId", "outer"),
    ("measurements", {"minArea": 40, "minPerimeter": 25, "sourceTypes": ["blob"],
                      "circularityEnabled": True, "minCircularity": 0.8}, "measurementId", "m-high"),
    ("lines", {"minLength": 8, "maxLength": 15}, "lineId", "long"),
    ("circles", {"minRadius": 3, "maxRadius": 8}, "circleId", "large-circle"),
    ("matches", {"minQuality": 0.8, "templateMethods": ["ccoeffNormed"]}, "matchId", "strong"),
)


def _collections():
    return {"blobs": _blobs(), "detections": _detections(), **_newCollections()}


def _defaults(params):
    properties = CollectionFilterOperator.meta.paramSchema["properties"]
    return {**{key: deepcopy(field["default"]) for key, field in properties.items() if "default" in field}, **params}


@pytest.mark.parametrize("port,legacy,idField,expectedId", _CASES)
def testLegacyAndPreparedGroupsKeepSameResultsForAllTypes(port, legacy, idField, expectedId):
    source = _collections()[port]
    operator = CollectionFilterOperator()
    inputs = {port: source.toPayload()}
    old = operator.executeNode(inputs, legacy, {})
    defaultMerged = operator.executeNode(inputs, _defaults(legacy), {})
    normalized = prepareFilterParams(legacy, port)
    normalizedBefore = deepcopy(normalized)
    grouped = operator.executeNode(inputs, _defaults(normalized), {})
    assert old["status"] == defaultMerged["status"] == grouped["status"] == "ok"
    assert old["outputs"] == defaultMerged["outputs"] == grouped["outputs"]
    kept = type(source).fromPayload(grouped["outputs"]["kept" + port.title()])
    assert [getattr(item, idField) for item in kept.items] == [expectedId]
    assert grouped["outputs"]["rejectedCount"] == len(source.items) - 1
    assert normalized == normalizedBefore


@pytest.mark.parametrize("port,legacy,idField,expectedId", _CASES)
def testTypeSpecificAndSpatialGroupsCombineWithAnd(port, legacy, idField, expectedId):
    source = _collections()[port]
    box = (12, 12, 2, 2) if port == "contours" else (0, 0, 5, 5) if port == "lines" else (0, 0, 25, 25)
    inputs = {port: source.toPayload(), "roi": BBox2D(*box, _space()).toPayload()}
    operator = CollectionFilterOperator()
    params = prepareFilterParams(legacy, port)
    params.update(spatialEnabled=True, spatialMode="centerInside")
    combined = operator.executeNode(inputs, params, {})
    spatialOnly = prepareFilterParams({}, port)
    spatialOnly.update(spatialEnabled=True, spatialMode="centerInside")
    assert operator.executeNode(inputs, spatialOnly, {})["outputs"]["keptCount"] == 1
    assert operator.executeNode(inputs, prepareFilterParams(legacy, port), {})["outputs"]["keptCount"] == 1
    assert combined["status"] == "ok"
    assert combined["outputs"]["keptCount"] == 0


def testIndependentDetectionGroupsCombineWithAnd():
    operator = CollectionFilterOperator()
    inputs = {"detections": _detections().toPayload()}
    params = {"collectionType": "detections", "confidenceEnabled": True,
              "minConfidence": 0.8, "classIdsEnabled": True, "classIds": [0]}
    assert operator.executeNode(inputs, params, {})["outputs"]["keptCount"] == 0
    for flag in ("confidenceEnabled", "classIdsEnabled"):
        assert operator.executeNode(inputs, {**params, flag: False}, {})["outputs"]["keptCount"] == 1


@pytest.mark.parametrize("flag,group", FILTER_GROUPS.items())
def testDisabledGroupInvalidValuesArePreservedButNotValidatedOrExecuted(flag, group):
    port = group["types"][0]
    source = _collections()[port]
    params = {"collectionType": port, flag: False,
              **{field: {"invalid": True} for field in group["fields"]}}
    before = deepcopy(params)
    operator = CollectionFilterOperator()
    assert operator.validateParams(params) is None
    result = operator.executeNode({port: source.toPayload(), "roi": "invalid"}, params, {})
    assert result["status"] == "ok"
    assert result["outputs"]["keptCount"] == len(source.items)
    prepared = prepareFilterParams(params, port, group["fields"])
    assert all(prepared[key] == value for key, value in before.items())
    assert params == before
    enabled = {**params, flag: True}
    assert operator.validateParams(enabled)["code"] == "E_PARAM_INVALID"


@pytest.mark.parametrize("port", COLLECTION_TYPES)
def testIrrelevantInvalidGroupsAreIgnoredWithOrWithoutSelectedType(port):
    params = {}
    for flag, group in FILTER_GROUPS.items():
        if port not in group["types"]:
            params[flag] = "not-a-boolean"
            params.update({field: {"invalid": True} for field in group["fields"]})
    before = deepcopy(params)
    operator = CollectionFilterOperator()
    source = _collections()[port]
    assert operator.validateParams({**params, "collectionType": port}) is None
    for selected in ("", port):
        result = operator.executeNode({port: source.toPayload()}, {**params, "collectionType": selected}, {})
        assert result["status"] == "ok"
        assert result["outputs"]["keptCount"] == len(source.items)
    assert params == before


@pytest.mark.parametrize("port", COLLECTION_TYPES)
def testActualCollectionIsParsedBeforeValidationAndMismatchesAreRejected(port):
    operator = CollectionFilterOperator()
    other = next(name for name in COLLECTION_TYPES if name != port)
    result = operator.executeNode({port: _collections()[port].toPayload()}, {"collectionType": other}, {})
    assert result["error"]["code"] == "E_INPUT_TYPE"
    assert port in result["error"]["message"] and other in result["error"]["message"]
    malformed = operator.executeNode({port: "not-a-collection"}, {"minArea": "invalid"}, {})
    assert malformed["error"]["code"] == "E_INPUT_TYPE"


@pytest.mark.parametrize("port", COLLECTION_TYPES)
def testPreparingNeutralLegacyParamsBeforeFormDefaultsLeavesEveryGroupOff(port):
    prepared = prepareFilterParams({}, port)
    assert prepared["collectionType"] == port
    assert all(prepared[flag] is False for flag in FILTER_GROUPS)
    values = _defaults(prepared)
    source = _collections()[port]
    result = CollectionFilterOperator().executeNode({port: source.toPayload()}, values, {})
    assert result["status"] == "ok"
    assert result["outputs"]["keptCount"] == len(source.items)


@pytest.mark.parametrize("flag,group", [(flag, group) for flag, group in FILTER_GROUPS.items() if flag != "circularityEnabled"])
def testRelevantParameterBindingEnablesMissingNewFlagsBeforeDefaults(flag, group):
    prepared = prepareFilterParams({}, group["types"][0], iter(group["fields"]))
    assert prepared[flag] is True
    assert _defaults(prepared)[flag] is True
    if flag != "spatialEnabled":
        other = next(name for name in COLLECTION_TYPES if name not in group["types"])
        assert prepareFilterParams({}, other, group["fields"])[flag] is True


@pytest.mark.parametrize("flag,group", FILTER_GROUPS.items())
def testNonNeutralLegacyFieldsInferNewGroupsAcrossTypeSwitches(flag, group):
    values = {group["fields"][0]: "non-neutral"}
    prepared = prepareFilterParams(values, group["types"][0])
    assert prepared[flag] is (flag != "circularityEnabled")
    assert prepared[group["fields"][0]] == "non-neutral"
    if flag != "spatialEnabled":
        other = next(name for name in COLLECTION_TYPES if name not in group["types"])
        assert prepareFilterParams(values, other)[flag] is (flag != "circularityEnabled")


def testSelectedTypeWinsAndDisabledStoredValuesSurvivePreparationAndTypeSwitch():
    params = {"collectionType": "blobs", "areaEnabled": False, "minArea": 500,
              "blobLabels": [2], "privateExtension": {"value": [1]}}
    prepared = prepareFilterParams(params, "detections", ("minArea",))
    assert prepared["collectionType"] == "blobs"
    assert prepared["areaEnabled"] is False
    assert prepared["minArea"] == 500
    assert prepared["blobLabelsEnabled"] is True
    switched = prepareFilterParams({**prepared, "collectionType": "detections"})
    effective = effectiveFilterParams(switched)
    assert switched["blobLabels"] == [2]
    assert effective["blobLabels"] == []
    assert effective["minArea"] == 0.0
    prepared["privateExtension"]["value"].append(2)
    assert params["privateExtension"] == {"value": [1]}


def testAbsentCircularityControlRetainsLegacyOptInBehavior():
    params = {"minCircularity": 0.99}
    source = _blobs()
    operator = CollectionFilterOperator()
    for values in (params, prepareFilterParams(params, "blobs", ("minCircularity",))):
        result = operator.executeNode({"blobs": source.toPayload()}, values, {})
        assert result["outputs"]["keptCount"] == len(source.items)
    assert operator.executeNode({"blobs": source.toPayload()}, {**params, "circularityEnabled": True}, {})["outputs"]["keptCount"] == 0


def testDetectionAreaIsRememberedForOtherTypesButNeverApplied():
    params = {"minArea": 1_000_000}
    prepared = prepareFilterParams(params, "detections", ("minArea",))
    assert prepared["areaEnabled"] is True
    assert prepared["minArea"] == 1_000_000
    assert effectiveFilterParams(prepared)["areaEnabled"] is False
    result = CollectionFilterOperator().executeNode({"detections": _detections().toPayload()}, params, {})
    assert result["outputs"]["keptCount"] == 2


@pytest.mark.parametrize("mode", ("centerInside", "bboxIntersects", "bboxContained"))
def testSpatialLegacyModesRemainActiveAndExplicitFalseDisablesRoiRequirement(mode):
    operator = CollectionFilterOperator()
    inputs = {"detections": _detections().toPayload()}
    prepared = _defaults(prepareFilterParams({"spatialMode": mode}, "detections"))
    assert prepared["spatialEnabled"] is True
    assert prepared["spatialMode"] == mode
    assert operator.executeNode(inputs, prepared, {})["error"]["code"] == "E_INPUT_MISSING"
    assert operator.executeNode(inputs, {"spatialMode": mode}, {})["error"]["code"] == "E_INPUT_MISSING"
    defaultMerged = _defaults({"spatialMode": mode})
    assert "spatialEnabled" not in defaultMerged
    assert operator.executeNode(inputs, defaultMerged, {})["error"]["code"] == "E_INPUT_MISSING"
    roiInputs = {**inputs, "roi": BBox2D(0, 0, 20, 20, _space()).toPayload()}
    legacyResult = operator.executeNode(roiInputs, {"spatialMode": mode}, {})
    defaultResult = operator.executeNode(roiInputs, defaultMerged, {})
    assert legacyResult["outputs"] == defaultResult["outputs"]
    assert defaultResult["outputs"]["keptCount"] == 1
    disabled = operator.executeNode(inputs, {"spatialEnabled": False, "spatialMode": mode}, {})
    assert disabled["outputs"]["keptCount"] == 2


def testInactiveSpatialNoneGetsUsefulEditorDefaultWithoutChangingSavedParams():
    operator = CollectionFilterOperator()
    params = {"spatialMode": "none"}
    prepared = prepareFilterParams(params)
    assert prepared["spatialEnabled"] is False
    assert prepared["spatialMode"] == "centerInside"
    assert params == {"spatialMode": "none"}
    assert prepareFilterParams({})["spatialMode"] == "centerInside"
    assert operator.validateParams(prepared) is None
    assert operator.validateParams({**prepared, "spatialEnabled": True}) is None
    assert operator.validateParams({"spatialEnabled": True, "spatialMode": "none"})["code"] == "E_PARAM_INVALID"
    assert prepareFilterParams({"spatialEnabled": True, "spatialMode": "none"})["spatialMode"] == "none"
    assert prepareFilterParams({"spatialEnabled": False, "spatialMode": "bboxContained"})["spatialMode"] == "bboxContained"


def testDynamicCollectionTypeDoesNotDropInferredFlagsForOtherTypes():
    params = {"collectionType": "detections", "minLength": 10, "classIds": [1]}
    prepared = prepareFilterParams(params, dynamicParameters=("collectionType", "minRadius"))
    assert prepared["lengthEnabled"] is True
    assert prepared["classIdsEnabled"] is True
    assert prepared["radiusEnabled"] is True
    assert effectiveFilterParams(prepared)["lengthEnabled"] is False
    switched = {**prepared, "collectionType": "lines"}
    assert effectiveFilterParams(switched)["minLength"] == 10


def testStandaloneValidationWithoutTypeKeepsLegacyChecksAndNoMissingFlagErrors():
    operator = CollectionFilterOperator()
    assert operator.validateParams({}) is None
    assert operator.validateParams({"minLength": 8, "classIds": [1], "spatialMode": "centerInside"}) is None
    assert operator.validateParams({"minLength": -1})["code"] == "E_PARAM_INVALID"
    assert operator.validateParams({"minLength": -1, "lengthEnabled": False}) is None
    assert operator.validateParams({"collectionType": "detections", "minLength": -1}) is None
    for selected in ("unsupported", None, []):
        assert operator.validateParams({"collectionType": selected})["code"] == "E_PARAM_INVALID"


@pytest.mark.parametrize("flag,group", FILTER_GROUPS.items())
def testApplicableEnabledFlagsMustBeBoolean(flag, group):
    assert CollectionFilterOperator().validateParams(
        {"collectionType": group["types"][0], flag: "false"}
    )["code"] == "E_PARAM_INVALID"


def testFullSchemaDefaultsValidateWithoutEditorPreparation():
    params = _defaults({})
    assert CollectionFilterOperator().validateParams(params) is None
    assert set(params).intersection(FILTER_GROUPS) == {"circularityEnabled"}
    assert params["circularityEnabled"] is False
    assert params["spatialMode"] == "none"


@pytest.mark.parametrize("port", COLLECTION_TYPES)
def testFullSchemaDefaultsPassThroughAllCollectionTypesWithoutRoi(port):
    operator = CollectionFilterOperator()
    source = _collections()[port]
    for selected in ("", port):
        params = _defaults({"collectionType": selected})
        assert operator.validateParams(params) is None
        result = operator.executeNode({port: source.toPayload()}, params, {})
        assert result["status"] == "ok"
        assert result["outputs"]["keptCount"] == len(source.items)
        assert result["outputs"]["rejectedCount"] == 0


def testManifestAndOperatorAgreeOnSchemaVersionAndFullPorts():
    path = Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins/collection_filter/manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    meta = CollectionFilterOperator.meta
    assert manifest["version"] == meta.version == "1.2.0"
    assert manifest["paramSchema"] == meta.paramSchema
    assert manifest["inputPorts"] == meta.inputPorts
    assert manifest["outputPorts"] == meta.outputPorts
    assert set(meta.inputPorts) == {*COLLECTION_TYPES, "roi"}
    assert set(meta.outputPorts) == {"keptCount", "rejectedCount"} | {
        prefix + port.title() for port in COLLECTION_TYPES for prefix in ("kept", "rejected")
    }


def testSchemaOrdersGroupsAndControlsVisibilityWithoutLosingLegacyDefaults():
    schema = CollectionFilterOperator.meta.paramSchema
    properties = schema["properties"]
    assert schema["xCollectionFilter"] is True
    assert properties["collectionType"]["enum"] == ["", *COLLECTION_TYPES]
    assert properties["collectionType"]["default"] == ""
    assert properties["collectionType"]["xOptionLabels"] == {"": "请选择筛选对象", **COLLECTION_TYPES}
    expectedOrder = ["collectionType"]
    for flag, group in FILTER_GROUPS.items():
        expectedOrder.extend((flag, *group["fields"]))
        assert isinstance(group["types"], tuple) and isinstance(group["fields"], tuple)
        if flag == "circularityEnabled":
            assert properties[flag]["default"] is False
        else:
            assert "default" not in properties[flag]
        assert properties[flag]["xVisibleWhen"] == {"collectionType": list(group["types"])}
        assert properties[flag]["xValidateWhen"] == {"collectionType": ["", *group["types"]]}
        for field in group["fields"]:
            assert properties[field]["xVisibleWhen"] == {"collectionType": list(group["types"]), flag: [True]}
            assert properties[field]["xValidateWhen"] == {"collectionType": ["", *group["types"]], flag: [True]}
            enumSchema = properties[field].get("items", properties[field])
            if "enum" in enumSchema:
                assert set(enumSchema["xOptionLabels"]) == set(enumSchema["enum"])
                assert all(any("\u4e00" <= char <= "\u9fff" for char in title) for title in enumSchema["xOptionLabels"].values())
    assert list(properties) == expectedOrder
    assert list(FILTER_GROUPS["spatialEnabled"]["types"]) == list(COLLECTION_TYPES)
    for name in ("blobLabels", "classIds"):
        assert properties[name]["xWidget"] == "integer-list"
