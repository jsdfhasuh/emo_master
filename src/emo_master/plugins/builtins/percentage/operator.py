import math

from emo_master.plugins.builtins.image_loader.operator import OperatorMeta


class PercentageOperator:
    meta = OperatorMeta(
        "vision.math.percentage", "\u767e\u5206\u6bd4", "1.0.0",
        {"numerator": {"type": "number", "required": True, "nullable": False},
         "denominator": {"type": "number", "required": False, "nullable": False}},
        {"value": {"type": "number", "required": True, "nullable": True},
         "defined": {"type": "boolean", "required": True, "nullable": False}},
        {"type": "object", "properties": {}, "additionalProperties": False},
    )

    def validateParams(self, params):
        return None

    def executeNode(self, inputs, params, runtimeContext):
        numerator, denominator = inputs.get("numerator"), inputs.get("denominator", 1)
        for name, value in (("numerator", numerator), ("denominator", denominator)):
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                return {"status": "error", "error": {
                    "code": "E_INPUT_TYPE", "message": f"{name} must be a finite nonnegative number"}}
        value = (numerator / denominator) * 100 if denominator else None
        if value is not None and not math.isfinite(value):
            return {"status": "error", "error": {"code": "E_RESULT_INVALID", "message": "percentage overflow"}}
        return {"status": "ok", "outputs": {"value": value, "defined": bool(denominator)}}
