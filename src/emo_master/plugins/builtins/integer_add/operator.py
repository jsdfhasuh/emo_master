"""Pure integer arithmetic for port-carried loop state, without shared storage."""
from emo_master.core.project.global_variables import VariableError, validateValue
from emo_master.plugins.builtins._numeric_bridges import OperatorMeta


SCHEMA = {
    "type": "object",
    "properties": {
        name: {"title": title, "type": "integer", "default": 0,
               "minimum": -(2**63), "maximum": 2**63 - 1,
               "description": "未连接对应输入时使用；连接输入后以本次输入为准。"}
        for name, title in (("leftValue", "左侧默认值"), ("rightValue", "右侧默认值"))
    },
    "additionalProperties": False,
}


class IntegerAddOperator:
    meta = OperatorMeta(
        "vision.math.integer_add", "整数加法", "1.0.0",
        {name: dict(type="integer", required=False, nullable=False) for name in ("left", "right")},
        {"value": dict(type="integer", required=True, nullable=False)}, SCHEMA,
    )

    def validateParams(self, params):
        try:
            if set(params) - {"leftValue", "rightValue"}:
                raise ValueError("仅支持 leftValue、rightValue 参数。")
            for name in ("leftValue", "rightValue"):
                validateValue(params.get(name, 0), "integer")
        except ValueError as error:
            return dict(code="E_PARAM_INVALID", message=str(error))
        return None

    def executeNode(self, inputs, params, runtimeContext):
        error = self.validateParams(params)
        if error:
            return dict(status="error", error=error)
        try:
            left = inputs.get("left", params.get("leftValue", 0))
            right = inputs.get("right", params.get("rightValue", 0))
            validateValue(left, "integer")
            validateValue(right, "integer")
        except VariableError as error:
            return dict(status="error", error=dict(code="E_INPUT_TYPE", message=str(error)))
        value = left + right
        try:
            validateValue(value, "integer")
        except VariableError:
            return dict(status="error", error=dict(code="E_RESULT_INVALID", message="整数相加结果超出 64 位有符号整数范围。"))
        return dict(status="ok", outputs=dict(value=value))
