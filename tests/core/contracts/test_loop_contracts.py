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
