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


_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"value": {"type": "number", "default": 0.0}},
}


class NumberValueOperator:
    meta = OperatorMeta(
        operatorId="vision.value.number",
        displayName="Number",
        version="1.0.0",
        inputPorts={},
        outputPorts={
            "value": {"type": "number", "required": True, "nullable": False}
        },
        paramSchema=_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        if not _isFiniteNumber(params.get("value", 0.0)):
            return _paramError("value must be a finite number")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = inputs, runtimeContext
        startedAt = perf_counter()
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        value = float(cast(int | float, params.get("value", 0.0)))
        return {
            "status": "ok",
            "outputs": {"value": value},
            "metrics": {"latencyMs": round((perf_counter() - startedAt) * 1000.0, 3)},
            "diagnostics": {"text": f"Number value: {value}"},
        }


def _isFiniteNumber(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}
