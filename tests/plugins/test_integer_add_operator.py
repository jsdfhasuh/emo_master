import json
from pathlib import Path

import pytest

from emo_master.plugins.builtins.integer_add.operator import IntegerAddOperator


@pytest.mark.parametrize("inputs,params,expected,fallbacks", [
    ({}, {}, 0, [("leftValue", 0, "automatic"), ("rightValue", 0, "automatic")]),
    ({}, {"leftValue": -3, "rightValue": 2}, -1, [("leftValue", -3, "prepared"), ("rightValue", 2, "prepared")]),
    ({"left": 7}, {"rightValue": 1}, 8, [("rightValue", 1, "prepared")]),
    ({"left": 0, "right": 2}, {"leftValue": 99, "rightValue": 99}, 2, []),
    ({"left": 2**53 + 1, "right": 1}, {}, 2**53 + 2, []),
    ({"left": -(2**63), "right": 0}, {}, -(2**63), []),
])
def testIntegerAdditionIsStatelessExactAndInputsOverrideDefaults(inputs, params, expected, fallbacks):
    operator = IntegerAddOperator()
    for _ in range(2):
        result = operator.executeNode(inputs, params, {})
        expectedResult = dict(status="ok", outputs=dict(value=expected))
        if fallbacks:
            expectedResult["diagnostics"] = {"effectiveParameters": [
                dict(path=[key], value=value, source=source) for key, value, source in fallbacks
            ]}
        assert result == expectedResult
        assert type(result["outputs"]["value"]) is int


@pytest.mark.parametrize("value", [True, 1.0, "1", None, 2**63, -(2**63) - 1])
def testInvalidIntegerInputsAreNotCoerced(value):
    result = IntegerAddOperator().executeNode({"left": value}, {}, {})
    assert result["error"]["code"] == "E_INPUT_TYPE"


@pytest.mark.parametrize("params", [{"leftValue": True}, {"rightValue": 1.5}, {"rightValue": 2**63}, {"delta": 1}])
def testInvalidIntegerParameters(params):
    assert IntegerAddOperator().executeNode({}, params, {})["error"]["code"] == "E_PARAM_INVALID"


@pytest.mark.parametrize("left,right", [(2**63 - 1, 1), (-(2**63), -1)])
def testOverflowIsAnErrorRatherThanFloatConversionOrWraparound(left, right):
    assert IntegerAddOperator().executeNode({"left": left, "right": right}, {}, {})["error"]["code"] == "E_RESULT_INVALID"
    result = IntegerAddOperator().executeNode({"left": left}, {"rightValue": right}, {})
    assert result["error"]["code"] == "E_RESULT_INVALID"
    assert result["diagnostics"]["effectiveParameters"] == [
        {"path": ["rightValue"], "value": right, "source": "prepared"}
    ]


def testIntegerAddManifestMatchesRuntimeContract():
    import emo_master.plugins.builtins.integer_add.operator as module
    manifest = json.loads(Path(module.__file__).with_name("manifest.json").read_text(encoding="utf-8"))
    for key in ("operatorId", "version", "inputPorts", "outputPorts", "paramSchema"):
        assert manifest[key] == getattr(IntegerAddOperator.meta, key)
