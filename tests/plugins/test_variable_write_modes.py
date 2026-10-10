import json
from pathlib import Path

import pytest

from emo_master.core.project.global_variables import definitions, variablePorts
from emo_master.plugins.builtins.variable_write.operator import WriteVariableOperator
from tests.runtime.test_global_variables import service, variable


def testManifestMatchesMetadata():
    path = Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins/variable_write/manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for key in ("operatorId", "version", "inputPorts", "outputPorts", "paramSchema"):
        assert manifest[key] == getattr(WriteVariableOperator.meta, key)


@pytest.mark.parametrize("operation", ["increment", "reset"])
@pytest.mark.parametrize("inputs", [{}, {"after": None}, {"after": 2, "value": 10}])
def testSideEffectRequiresDependencyAndNoUnusedValue(tmp_path, operation, inputs):
    state = service(tmp_path)
    state.set("v", 7)
    result = WriteVariableOperator().executeNode(inputs, {"variableId": "v", "operation": operation}, {"globalVariables": state})
    assert result["status"] == "error"
    assert state.get("v") == 7


def testAtomicIncrementThenResetDoesNotChangeEmittedInteger(tmp_path):
    state = service(tmp_path)
    operator = WriteVariableOperator()
    context = {"globalVariables": state}
    increment = {"variableId": "v", "operation": "increment"}
    assert operator.executeNode({"after": 0}, increment, context)["outputs"]["value"] == 1
    emitted = operator.executeNode({"after": 0}, increment, context)["outputs"]["value"]
    assert emitted == 2
    assert operator.executeNode({"after": emitted}, {"variableId": "v", "operation": "reset"}, context)["outputs"]["value"] == 0
    assert state.get("v") == 0 and emitted == 2


@pytest.mark.parametrize("delta", [True, 1.0, "1", 2**63, -(2**63)-1])
def testInvalidDeltaDoesNotWrite(tmp_path, delta):
    state = service(tmp_path)
    result = WriteVariableOperator().executeNode({"after": 1}, {"variableId": "v", "operation": "increment", "delta": delta}, {"globalVariables": state})
    assert result["error"]["code"] == "E_VARIABLE_TYPE"
    assert state.get("v") == 0


@pytest.mark.parametrize("operation", ["set", "increment", "reset"])
def testPreviewRejectsEveryWriteMode(tmp_path, operation):
    state = service(tmp_path)
    result = WriteVariableOperator().executeNode({"value": 3} if operation == "set" else {"after": 1},
        {"variableId": "v", "operation": operation}, {"globalVariables": state.snapshot()})
    assert result["error"]["code"] == "E_VARIABLE_PREVIEW_READ_ONLY"
    assert state.get("v") == 0


def testOverflowRollsBackAndResetUsesDeclaredInitialValue(tmp_path):
    state = service(tmp_path, {"v": variable(7)})
    state.set("v", 2**63 - 1)
    operator = WriteVariableOperator()
    result = operator.executeNode({"after": 1}, {"variableId": "v", "operation": "increment"}, {"globalVariables": state})
    assert result["error"]["code"] == "E_VARIABLE_TYPE"
    assert state.get("v") == 2**63 - 1
    result = operator.executeNode({"after": 1}, {"variableId": "v", "operation": "reset"}, {"globalVariables": state})
    assert result["outputs"]["value"] == 7


def testModeSpecificPortsRetainOldSetInterface():
    values = definitions({"v": variable()})
    for operation, expected in ((None, "value"), ("set", "value"), ("increment", "after"), ("reset", "after")):
        params = {"variableId": "v", **({"operation": operation} if operation else {})}
        inputs, outputs = variablePorts(WriteVariableOperator.meta.operatorId, params, values)
        assert list(inputs) == [expected] and inputs[expected]["required"]
        assert outputs["value"]["type"] == "integer"
