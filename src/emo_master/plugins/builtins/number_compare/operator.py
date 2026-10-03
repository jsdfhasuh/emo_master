from __future__ import annotations

import math
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


_OPERATORS = frozenset({"eq", "ne", "lt", "lte", "gt", "gte"})
_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "operator": {
            "type": "string",
            "enum": ["eq", "ne", "lt", "lte", "gt", "gte"],
            "default": "gte",
        },
        "rightValue": {"type": "number", "default": 0.0},
    },
}


class NumberCompareOperator:
    meta = OperatorMeta(
        operatorId="vision.compare.number",
        displayName="Compare Number",
        version="1.0.0",
        inputPorts={
            "left": {"type": "number", "required": True, "nullable": False},
            "right": {"type": "number", "required": False, "nullable": False},
        },
        outputPorts={
            "result": {"type": "boolean", "required": True, "nullable": False}
        },
        paramSchema=_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        operator = params.get("operator", "gte")
        if not isinstance(operator, str) or operator not in _OPERATORS:
            return _paramError("operator must be one of eq, ne, lt, lte, gt, gte")
        if not _isFiniteNumber(params.get("rightValue", 0.0)):
            return _paramError("rightValue must be a finite number")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        startedAt = perf_counter()
        if "left" not in inputs:
            return _error("E_INPUT_MISSING", "input 'left' is required")
        if not _isFiniteNumber(inputs["left"]):
            return _error("E_INPUT_TYPE", "input 'left' must be a finite number")
        if "right" in inputs and not _isFiniteNumber(inputs["right"]):
            return _error("E_INPUT_TYPE", "input 'right' must be a finite number")
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}

        left = float(cast(int | float, inputs["left"]))
        rightRaw = inputs.get("right", params.get("rightValue", 0.0))
        right = float(cast(int | float, rightRaw))
        operator = cast(str, params.get("operator", "gte"))
        result = {
            "eq": left == right,
            "ne": left != right,
            "lt": left < right,
            "lte": left <= right,
            "gt": left > right,
            "gte": left >= right,
        }[operator]
        return {
            "status": "ok",
            "outputs": {"result": result},
            "metrics": {
                "latencyMs": round((perf_counter() - startedAt) * 1000.0, 3),
                "left": left,
                "right": right,
            },
            "diagnostics": {"text": f"Compared {left} {operator} {right}: {result}"},
        }


def _isFiniteNumber(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
