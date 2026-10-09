import pytest

from emo_master.core.workflow.loop_contracts import deriveLoopContract


def testRepeatPortsFollowBodyWorkflow() -> None:
    contract = deriveLoopContract(
        {
            "contractVersion": 2,
            "mode": "repeat",
            "repeatCount": 2,
            "maxIterations": 2,
        },
        {"image": "image"},
        {"edges": "image"},
    )

    assert contract.inputPorts == {"image": "image"}
    assert contract.outputPorts == {"edges": "image"}
    assert contract.issues == ()


def testRepeatZeroRejectsInputThatCannotSafelySatisfyItsOutputType() -> None:
    contract = deriveLoopContract(
        {
            "contractVersion": 2,
            "mode": "repeat",
            "repeatCount": 0,
            "maxIterations": 1,
        },
        {"value": "object"},
        {"value": "string"},
    )

    assert {issue.code for issue in contract.issues} == {
        "E_LOOP_ZERO_OUTPUT_UNSATISFIABLE"
    }


def testRepeatZeroAllowsInputThatSafelyWidensToItsOutputType() -> None:
    contract = deriveLoopContract(
        {
            "contractVersion": 2,
            "mode": "repeat",
            "repeatCount": 0,
            "maxIterations": 1,
        },
        {"value": "string"},
        {"value": "object"},
    )

    assert contract.issues == ()


def testForEachPortsAggregateEveryBodyOutput() -> None:
    contract = deriveLoopContract(
        {
            "contractVersion": 2,
            "mode": "foreach",
            "itemInputPort": "image",
            "indexInputPort": "index",
            "maxIterations": 10,
        },
        {"image": "image", "index": "integer", "threshold": "number"},
        {"edges": "image", "score": "number"},
    )

    assert contract.inputPorts == {
        "items": "list<image>",
        "threshold": "number",
    }
    assert contract.outputPorts == {
        "edges": "list<image>",
        "score": "list<number>",
    }
    assert contract.issues == ()


def testWhilePortsFollowCompatibleBodyState() -> None:
    contract = deriveLoopContract(
        {
            "contractVersion": 2,
            "mode": "while",
            "maxIterations": 10,
        },
        {"image": "image", "attempt": "integer"},
        {"image": "image", "attempt": "integer"},
        {"attempt": "integer"},
        {"continue": "boolean"},
    )

    assert contract.inputPorts == {"image": "image", "attempt": "integer"}
    assert contract.outputPorts == {"image": "image", "attempt": "integer"}
    assert contract.issues == ()


def testWhileRejectsBodyThatCannotFeedItsNextIteration() -> None:
    contract = deriveLoopContract(
        {
            "contractVersion": 2,
            "mode": "while",
            "maxIterations": 10,
        },
        {"image": "image"},
        {"results": "image"},
        {"image": "image"},
        {"continue": "boolean"},
    )

    assert {issue.code for issue in contract.issues} == {
        "E_LOOP_STATE_OUTPUT_MISSING",
        "E_LOOP_STATE_INPUT_MISSING",
    }


def testBooleanWhileUsesStatePortWithoutConditionWorkflow() -> None:
    config = {"contractVersion": 2, "mode": "while", "conditionMode": "boolean",
              "conditionPort": "hasNext", "maxIterations": 10,
              "conditionWorkflowId": "inactive-old-reference"}
    contract = deriveLoopContract(config, {"hasNext": "boolean"}, {"hasNext": "boolean"})
    assert contract.issues == ()
    assert contract.inputPorts == contract.outputPorts == {"hasNext": "boolean"}
    assert "conditionWorkflowId" not in contract.normalizedConfig
    assert config["conditionWorkflowId"] == "inactive-old-reference"


@pytest.mark.parametrize("port, inputs, outputs, code", [
    (None, {"hasNext": "boolean"}, {"hasNext": "boolean"}, "E_LOOP_CONDITION_PORT_UNKNOWN"),
    ("missing", {"hasNext": "boolean"}, {"hasNext": "boolean"}, "E_LOOP_CONDITION_PORT_UNKNOWN"),
    ("hasNext", {"hasNext": "integer"}, {"hasNext": "integer"}, "E_LOOP_CONDITION_PORT_TYPE"),
    ("hasNext", {"hasNext": "any"}, {"hasNext": "any"}, "E_LOOP_CONDITION_PORT_TYPE"),
    ("hasNext", {"hasNext": "boolean"}, {}, "E_LOOP_CONDITION_PORT_TYPE"),
])
def testBooleanWhileRejectsMissingOrNonBooleanConditionPort(port, inputs, outputs, code) -> None:
    contract = deriveLoopContract({"contractVersion": 2, "mode": "while",
        "conditionMode": "boolean", "conditionPort": port, "maxIterations": 10}, inputs, outputs)
    assert code in {issue.code for issue in contract.issues}


def testBooleanWhileAcceptsBooleanPortDescriptors() -> None:
    contract = deriveLoopContract({"contractVersion": 2, "mode": "while",
        "conditionMode": "boolean", "conditionPort": "go", "maxIterations": 10},
        {"go": {"type": "boolean"}}, {"go": {"type": "boolean"}})
    assert contract.issues == ()


def testWhileRejectsUnknownConditionMode() -> None:
    contract = deriveLoopContract({"contractVersion": 2, "mode": "while",
        "conditionMode": "expression", "maxIterations": 10}, {}, {})
    assert {issue.code for issue in contract.issues} == {"E_LOOP_CONDITION_MODE_INVALID"}


def testBooleanWhileRequiresTypedStateContract() -> None:
    contract = deriveLoopContract({"mode": "while", "conditionMode": "boolean",
        "conditionPort": "go", "maxIterations": 10}, {"go": "boolean"}, {"go": "boolean"})
    assert {issue.code for issue in contract.issues} == {"E_LOOP_CONDITION_MODE_UNSUPPORTED"}
