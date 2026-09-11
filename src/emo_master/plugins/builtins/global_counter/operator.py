from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any


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
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 128},
    },
    "required": ["name"],
    "additionalProperties": False,
}


class GlobalCounterOperator:
    meta = OperatorMeta(
        operatorId="vision.state.counter",
        displayName="Global Counter",
        version="1.0.0",
        inputPorts={
            "increment": {"type": "boolean", "required": False, "nullable": False},
            "reset": {"type": "boolean", "required": False, "nullable": False},
        },
        outputPorts={
            "count": {"type": "integer", "required": True, "nullable": False}
        },
        paramSchema=_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        name = params.get("name")
        if not _validName(name):
            return {
                "code": "E_COUNTER_NAME_INVALID",
                "message": (
                    "counter name must contain 1 to 128 characters without "
                    "leading or trailing whitespace"
                ),
            }
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        startedAt = perf_counter()
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        for signalName in ("increment", "reset"):
            if signalName in inputs and not isinstance(inputs[signalName], bool):
                return _error(
                    "E_INPUT_TYPE",
                    f"input '{signalName}' must be a boolean",
                )

        accessor = runtimeContext.get("globalCounters")
        applyCounter = getattr(accessor, "apply", None)
        if not callable(applyCounter):
            return _error(
                "E_RUNTIME_STATE_UNAVAILABLE",
                "global counter state service is unavailable",
            )
        try:
            record = applyCounter(
                params["name"],
                increment=inputs.get("increment") is True,
                reset=inputs.get("reset") is True,
            )
            count = _recordValue(record)
        except Exception as err:
            return _error(
                str(getattr(err, "code", "E_RUNTIME_STATE_UNAVAILABLE")),
                str(err) or "global counter state service is unavailable",
            )
        if count is None:
            return _error(
                "E_RUNTIME_STATE_UNAVAILABLE",
                "global counter state service returned an invalid value",
            )
        return {
            "status": "ok",
            "outputs": {"count": count},
            "metrics": {
                "latencyMs": round((perf_counter() - startedAt) * 1000.0, 3),
            },
            "diagnostics": {
                "text": f"Global counter {params['name']}: {count}",
            },
        }


def _validName(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value.strip() == value
    )


def _recordValue(record: object) -> int | None:
    value = (
        record.get("value")
        if isinstance(record, dict)
        else getattr(record, "value", None)
    )
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
