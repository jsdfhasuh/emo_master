from dataclasses import asdict
import json
from pathlib import Path

import pytest

from emo_master.core.contracts.geometry2d import CoordinateSpace2D, Point2D
from emo_master.plugins.builtins._numeric_bridges import (
    PointComponentsOperator, NumberAbsoluteOperator, NumberExactIntegerOperator, NumberPairOperator,
)


@pytest.mark.parametrize("folder,cls", [
    ("point_components", PointComponentsOperator), ("number_absolute", NumberAbsoluteOperator),
    ("number_exact_integer", NumberExactIntegerOperator), ("number_pair", NumberPairOperator),
])
def testManifestAndParameters(folder, cls):
    path = Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins" / folder / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for key, value in asdict(cls.meta).items():
        assert manifest[key] == value
    operator = cls()
    assert operator.validateParams({}) is None
    result = operator.executeNode({}, {"rounding": "round"}, {})
    assert result["error"]["code"] == "E_PARAM_INVALID" and "outputs" not in result
    result = operator.executeNode({}, {}, {})
    assert result["error"]["code"] == "E_INPUT_MISSING" and "outputs" not in result


def testPointComponentsDoesNotTransformScaleOrSelectFirst():
    operator = PointComponentsOperator()
    point = Point2D(-120.25, 9.75, CoordinateSpace2D(transformToSource=(2, 0, 0, 3, 10, 20))).toPayload()
    assert operator.executeNode({"points": [point]}, {}, {})["outputs"] == dict(x=-120.25, y=9.75)
    for invalid in ([], [point, point], None, {}, [dict(x=1, y=2)]):
        result = operator.executeNode({"points": invalid}, {}, {})
        assert result["status"] == "error" and "outputs" not in result


@pytest.mark.parametrize("value", [0, -0.0, 120.0, -120, 65535, 10**100])
def testExactIntegerAndAbsoluteKeepIntegers(value):
    integer = NumberExactIntegerOperator().executeNode({"value": value}, {}, {})["outputs"]["value"]
    assert type(integer) is int and integer == value
    absolute = NumberAbsoluteOperator().executeNode({"value": integer}, {}, {})["outputs"]["value"]
    assert type(absolute) is int and absolute == abs(value)


@pytest.mark.parametrize("value", [True, None, "1", float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("cls", [NumberExactIntegerOperator, NumberAbsoluteOperator, NumberPairOperator])
def testInvalidNumbersProduceNoStaleOutput(cls, value):
    operator = cls()
    good = dict(first=1, second=2) if cls is NumberPairOperator else dict(value=1)
    assert operator.executeNode(good, {}, {})["status"] == "ok"
    bad = dict(first=value, second=2) if cls is NumberPairOperator else dict(value=value)
    result = operator.executeNode(bad, {}, {})
    assert result["status"] == "error" and "outputs" not in result


@pytest.mark.parametrize("value", [.5, -.5, 1.0000000000000002, .9999999999999999])
def testNoRoundingOrEpsilonTolerance(value):
    result = NumberExactIntegerOperator().executeNode({"value": value}, {}, {})
    assert result["status"] == "error" and "outputs" not in result


def testPairPreservesOrderTypesAndHasNoPersistentList():
    operator = NumberPairOperator()
    old = operator.executeNode(dict(first=120, second=2), {}, {})["outputs"]["values"]
    assert old == [120, 2] and all(type(v) is int for v in old)
    new = operator.executeNode(dict(first=-.5, second=1.0), {}, {})["outputs"]["values"]
    assert new == [-.5, 1.0] and all(type(v) is float for v in new)
    assert old == [120, 2] and new is not old
    assert operator.executeNode(dict(first=1), {}, {})["status"] == "error"
