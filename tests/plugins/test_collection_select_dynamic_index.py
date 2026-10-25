import json
from pathlib import Path

import pytest

from emo_master.core.contracts.geometry2d import DetectionCollection
from emo_master.plugins.builtins.collection_select.operator import CollectionSelectOperator
from tests.plugins.test_collection_operators import _detections, _blobs, _newCollections


@pytest.mark.parametrize("port", ["detections", "blobs", "contours", "measurements", "lines", "circles", "matches"])
def testDynamicIndexKeepsAllCollectionContractsAndOverridesLiteral(port):
    source = dict(detections=_detections(), blobs=_blobs(), **_newCollections())[port]
    operator = CollectionSelectOperator()
    original = source.toPayload()
    for index in (1, 0, 1):
        actual = operator.executeNode({port: source, "index": index}, {"mode": "index", "index": 99}, {})
        expected = operator.executeNode({port: source}, {"mode": "index", "index": index}, {})
        assert actual["status"] == "ok" and actual["outputs"] == expected["outputs"]
    assert source.toPayload() == original


@pytest.mark.parametrize("index", [-1, True, 1.0, "1", None])
@pytest.mark.parametrize("empty", [False, True])
def testDynamicIndexValidationAlsoAppliesToEmptyCollections(index, empty):
    source = _detections()
    if empty:
        source = DetectionCollection((), source.coordinateSpace)
    result = CollectionSelectOperator().executeNode({"detections": source, "index": index}, {"mode": "index"}, {})
    assert result["error"]["code"] == "E_INPUT_TYPE"


@pytest.mark.parametrize("mode", ["first", "last", "topK"])
def testDynamicIndexIsNotSilentlyIgnoredInOtherModes(mode):
    result = CollectionSelectOperator().executeNode({"detections": _detections(), "index": 0}, {"mode": mode}, {})
    assert result["error"]["code"] == "E_INPUT_SHAPE"


@pytest.mark.parametrize("behavior", ["empty", "error"])
def testDynamicOutOfRangeUsesExistingPolicy(behavior):
    result = CollectionSelectOperator().executeNode({"detections": _detections(), "index": 99},
        {"mode": "index", "onOutOfRange": behavior}, {})
    if behavior == "empty":
        assert result["outputs"]["selectedCount"] == 0
    else:
        assert result["error"]["code"] == "E_PARAM_INVALID"


@pytest.mark.parametrize("port", ["detections", "blobs", "contours", "measurements", "lines", "circles", "matches"])
@pytest.mark.parametrize("dynamic", [False, True])
@pytest.mark.parametrize("behavior", ["empty", "error"])
def testEmptyCollectionHonorsIndexPolicy(port, dynamic, behavior):
    source = dict(detections=_detections(), blobs=_blobs(), **_newCollections())[port].toPayload()
    source["items"] = []
    inputs = {port: source, **({"index": 0} if dynamic else {})}
    result = CollectionSelectOperator().executeNode(inputs,
        {"mode": "index", "index": 0, "onOutOfRange": behavior}, {})
    if behavior == "error":
        assert result["status"] == "error"
        assert result["error"]["code"] == "E_PARAM_INVALID"
        assert "size 0" in result["error"]["message"]
    else:
        assert result["status"] == "ok" and result["outputs"]["selectedCount"] == 0


@pytest.mark.parametrize("mode", ["first", "last", "topK"])
def testEmptyNonIndexSelectionStillSucceeds(mode):
    source = DetectionCollection((), _detections().coordinateSpace).toPayload()
    result = CollectionSelectOperator().executeNode({"detections": source},
        {"mode": mode, "onOutOfRange": "error"}, {})
    assert result["status"] == "ok" and result["outputs"]["selectedCount"] == 0


def testSelectManifestMatchesFullRuntimePortContract():
    import emo_master.plugins.builtins.collection_select.operator as module
    manifest = json.loads(Path(module.__file__).with_name("manifest.json").read_text(encoding="utf-8"))
    for key in ("operatorId", "version", "inputPorts", "outputPorts", "paramSchema"):
        assert manifest[key] == getattr(CollectionSelectOperator.meta, key)
    # Adding an optional control port must not change default collection wiring.
    assert list(manifest["inputPorts"])[0] == "blobs"
