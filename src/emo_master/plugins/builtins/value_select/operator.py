"""Stateless value selection; never substitute an unselected or previous value."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


class ValueSelectOperator:
    meta = OperatorMeta(
        operatorId="vision.flow.select_value",
        displayName="条件选值",
        version="1.0.0",
        inputPorts={
            "condition": {"type": "boolean", "required": True, "nullable": False},
            "trueValue": {"type": "any", "required": False, "nullable": False},
            "falseValue": {"type": "any", "required": False, "nullable": False},
        },
        outputPorts={"value": {"type": "any", "required": True, "nullable": False}},
        paramSchema={"type": "object", "properties": {}, "additionalProperties": False},
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        if params:
            return {"code": "E_PARAM_INVALID", "message": "条件选值不接受参数，请连接输入端口。"}
        return None

    def executeNode(self, inputs: dict[str, object], params: dict[str, object],
                    runtimeContext: dict[str, object]) -> dict[str, Any]:
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        if "condition" not in inputs:
            return _error("E_INPUT_MISSING", "缺少条件输入 condition。")
        if type(inputs["condition"]) is not bool:
            return _error("E_INPUT_TYPE", "条件输入 condition 必须是布尔值。")
        selected = "trueValue" if inputs["condition"] else "falseValue"
        if selected not in inputs:
            return _error("E_INPUT_MISSING", f"当前条件选中的输入 {selected} 尚未提供，不使用另一分支替代。")
        if inputs[selected] is None:
            return _error("E_INPUT_TYPE", f"当前条件选中的输入 {selected} 不能为 null。")
        return {"status": "ok", "outputs": {"value": inputs[selected]}}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
