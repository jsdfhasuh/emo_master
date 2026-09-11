import math

from emo_master.plugins.builtins.number_compare.operator import NumberCompareOperator
from emo_master.plugins.builtins.number_value.operator import NumberValueOperator


def testNumberValueOutputsFiniteNumber() -> None:
    result = NumberValueOperator().executeNode({}, {"value": 12.5}, {})

    assert result["status"] == "ok"
    assert result["outputs"]["value"] == 12.5


def testNumberValueRejectsNonFiniteAndBoolean() -> None:
    operator = NumberValueOperator()
    results = [
        operator.executeNode({}, {"value": math.nan}, {}),
        operator.executeNode({}, {"value": True}, {}),
    ]

    assert [result["error"]["code"] for result in results] == [
        "E_PARAM_INVALID",
        "E_PARAM_INVALID",
    ]


def testNumberCompareSupportsAllOperatorsAndRightInputWins() -> None:
    operator = NumberCompareOperator()
    expected = {
        "eq": False,
        "ne": True,
        "lt": False,
        "lte": False,
        "gt": True,
        "gte": True,
    }

    for name, value in expected.items():
        result = operator.executeNode(
            {"left": 5, "right": 3},
            {"operator": name, "rightValue": 100},
            {},
        )
        assert result["outputs"]["result"] is value


def testNumberCompareUsesParameterFallbackAndRejectsMissingLeft() -> None:
    operator = NumberCompareOperator()
    fallback = operator.executeNode(
        {"left": 5}, {"operator": "gte", "rightValue": 5}, {}
    )
    missing = operator.executeNode({}, {}, {})

    assert fallback["outputs"]["result"] is True
    assert missing["error"]["code"] == "E_INPUT_MISSING"
