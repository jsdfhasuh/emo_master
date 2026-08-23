from emo_master.plugins.builtins.flow_switch.operator import FlowSwitchOperator


def testFlowSwitchRoutesToMatchingCase() -> None:
    operator = FlowSwitchOperator()
    result = operator.executeNode(
        inputs={"value": "B"},
        params={
            "case0Value": "A",
            "case1Value": "B",
            "case2Value": "C",
            "case3Value": "D",
        },
        runtimeContext={},
    )
    assert result["status"] == "ok"
    outputs = result.get("outputs", {})
    assert outputs == {"case1": "B"}


def testFlowSwitchRoutesToDefaultWhenNoCaseMatches() -> None:
    operator = FlowSwitchOperator()
    result = operator.executeNode(
        inputs={"value": "Z"},
        params={
            "case0Value": "A",
            "case1Value": "B",
            "case2Value": "C",
            "case3Value": "D",
        },
        runtimeContext={},
    )
    assert result["status"] == "ok"
    outputs = result.get("outputs", {})
    assert outputs == {"default": "Z"}


def testFlowSwitchIgnoresUnconfiguredEmptyCase() -> None:
    operator = FlowSwitchOperator()
    result = operator.executeNode(
        inputs={"value": ""},
        params={},
        runtimeContext={},
    )
    assert result["status"] == "ok"
    outputs = result.get("outputs", {})
    assert outputs == {"default": ""}
