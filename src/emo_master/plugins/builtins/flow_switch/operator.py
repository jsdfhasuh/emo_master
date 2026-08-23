from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, str]
    outputPorts: dict[str, str]
    paramSchema: dict[str, object]


class FlowSwitchOperator:
    meta = OperatorMeta(
        operatorId="vision.flow.switch",
        displayName="Switch",
        version="1.0.0",
        inputPorts={"value": "object"},
        outputPorts={
            "case0": "object",
            "case1": "object",
            "case2": "object",
            "case3": "object",
            "default": "object",
        },
        paramSchema={
            "type": "object",
            "properties": {
                "case0Value": {"type": "string", "default": ""},
                "case1Value": {"type": "string", "default": ""},
                "case2Value": {"type": "string", "default": ""},
                "case3Value": {"type": "string", "default": ""},
            },
        },
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        for caseName in ("case0Value", "case1Value", "case2Value", "case3Value"):
            caseValue = params.get(caseName, "")
            if not isinstance(caseValue, str):
                return {
                    "code": "E_PARAM_INVALID",
                    "message": f"{caseName} must be string",
                }
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        if "value" not in inputs:
            return {
                "status": "error",
                "error": {
                    "code": "E_INPUT_MISSING",
                    "message": "input 'value' is required",
                },
            }

        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}

        value = inputs["value"]
        valueText = str(value)
        for caseIndex in range(4):
            casePort = f"case{caseIndex}"
            caseValueKey = f"{casePort}Value"
            if caseValueKey not in params:
                continue
            caseValue = str(params.get(caseValueKey, ""))
            if valueText == caseValue:
                return {"status": "ok", "outputs": {casePort: value}}

        return {"status": "ok", "outputs": {"default": value}}
