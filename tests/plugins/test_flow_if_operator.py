from emo_master.plugins.builtins.flow_if.operator import FlowIfOperator


def testFlowIfRoutesToTrueBranch() -> None:
    operator = FlowIfOperator()
    result = operator.executeNode(
        inputs={"value": True},
        params={"mode": "bool", "compareValue": ""},
        runtimeContext={},
    )
    assert result["status"] == "ok"
    outputs = result.get("outputs", {})
    assert outputs == {"true": True}


def testFlowIfRoutesToFalseBranch() -> None:
    operator = FlowIfOperator()
    result = operator.executeNode(
        inputs={"value": "A"},
        params={"mode": "equals", "compareValue": "B"},
        runtimeContext={},
    )
    assert result["status"] == "ok"
    outputs = result.get("outputs", {})
    assert outputs == {"false": "A"}


def testFlowIfSupportsNotEqualsMode() -> None:
    operator = FlowIfOperator()
    result = operator.executeNode(
        inputs={"value": "A"},
        params={"mode": "not_equals", "compareValue": "B"},
        runtimeContext={},
    )
    assert result["status"] == "ok"
    outputs = result.get("outputs", {})
    assert outputs == {"true": "A"}
