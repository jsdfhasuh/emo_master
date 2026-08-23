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


class FlowIfOperator:
    meta = OperatorMeta(
        operatorId="vision.flow.if",
        displayName="If",
        version="1.0.0",
        inputPorts={"value": "object"},
        outputPorts={"true": "object", "false": "object"},
        paramSchema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["bool", "equals", "not_equals"],
                    "default": "bool",
                },
                "compareValue": {
                    "type": "string",
                    "default": "",
                },
            },
            "required": ["mode"],
        },
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        mode = params.get("mode", "bool")
        if not isinstance(mode, str) or mode not in {"bool", "equals", "not_equals"}:
            return {
                "code": "E_PARAM_INVALID",
                "message": "mode must be bool, equals or not_equals",
            }
        compareValue = params.get("compareValue", "")
        if not isinstance(compareValue, str):
            return {
                "code": "E_PARAM_INVALID",
                "message": "compareValue must be string",
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
        mode = str(params.get("mode", "bool"))
        compareValue = str(params.get("compareValue", ""))

        if mode == "bool":
            try:
                condition = bool(value)
            except Exception:
                if hasattr(value, "size"):
                    sizeValue = getattr(value, "size")
                    condition = bool(sizeValue)
                else:
                    return {
                        "status": "error",
                        "error": {
                            "code": "E_PARAM_INVALID",
                            "message": "bool mode cannot evaluate input value",
                        },
                    }
        elif mode == "equals":
            condition = str(value) == compareValue
        elif mode == "not_equals":
            condition = str(value) != compareValue
        else:
            return {
                "status": "error",
                "error": {
                    "code": "E_PARAM_INVALID",
                    "message": f"unsupported mode: {mode}",
                },
            }

        if condition:
            return {"status": "ok", "outputs": {"true": value}}
        return {"status": "ok", "outputs": {"false": value}}
