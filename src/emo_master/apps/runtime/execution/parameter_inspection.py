"""Select small, display-safe parameter facts at the operator call boundary."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from emo_master.core.contracts.log_safety import REDACTED, isSensitiveParameter, redactLogValue
from emo_master.core.contracts.run_inspection import MAX_IO_BYTES, encodedBytes
from emo_master.core.plugin.result_schema import parameterSchemaAtPath
from emo_master.core.project.snapshots import parameterValidationApplies

_MAX_ROWS = 24
_MAX_VALUE_ITEMS = 32
_EFFECTIVE_SOURCES = {"automatic", "input", "normalized", "device", "prepared"}


class ParameterInspection:
    """One node invocation, including any evidence returned by the operator."""

    def __init__(self, schema: Mapping, resultSchema: Mapping,
                 sources: Mapping[tuple[str, ...], str] | None = None):
        self.schema = schema
        self.paths = resultSchema.get("captureParams", ())
        self.effectiveDeclared = "effectiveParameters" in resultSchema.get("diagnostics", {})
        self.sources = dict(sources or {})
        self.rows: list[dict] = []
        self._bytes = 2

    def captureCall(self, params: Mapping, bindings: Sequence[Mapping]) -> None:
        for binding in bindings:
            self.sources.setdefault(tuple(binding["parameterPath"]), "variable")
        for path in self.paths:
            value, schema = params, self.schema
            for part in path:
                child = schema.get("properties", {}).get(part, {})
                if not isinstance(value, Mapping) or part not in value or not parameterValidationApplies(value, child):
                    break
                value, schema = value[part], child
            else:
                self._append(path, value, schema, "call", self.sources.get(tuple(path), "prepared"))

    def captureEffective(self, diagnostics: dict) -> dict:
        if not self.effectiveDeclared or not isinstance(diagnostics.get("effectiveParameters"), list):
            return diagnostics
        remaining = []
        for entry in diagnostics["effectiveParameters"]:
            if not isinstance(entry, dict) or set(entry) != {"path", "value", "source"}:
                remaining.append(entry)
                continue
            path, source = entry["path"], entry["source"]
            if (not isinstance(path, list) or not path or not all(isinstance(part, str) for part in path)
                    or not isinstance(source, str) or source not in _EFFECTIVE_SOURCES):
                remaining.append(entry)
                continue
            schema = parameterSchemaAtPath(self.schema, path)
            if schema is None:
                remaining.append(entry)
                continue
            if source == "prepared":
                source = self.sources.get(tuple(path), source)
            self._append(path, entry["value"], schema, "effective", source)
        result = {key: value for key, value in diagnostics.items() if key != "effectiveParameters"}
        if remaining:
            result["effectiveParameters"] = remaining
        return result

    def _append(self, path: list[str], value: object, schema: Mapping, stage: str, source: str) -> None:
        if len(self.rows) >= _MAX_ROWS:
            return
        for index, part in enumerate(path):
            field = parameterSchemaAtPath(self.schema, path[:index+1]) or {}
            if isSensitiveParameter(part, field):
                value = REDACTED
                break
        if not _smallValue(value):
            return
        safe = redactLogValue(value, schema)
        row = {"path": list(path), "value": safe, "stage": stage, "source": source}
        try:
            size = encodedBytes(row) + bool(self.rows)
        except UnicodeEncodeError:
            # Some direct callers can supply a string that the log cannot encode.
            return
        if self._bytes + size <= MAX_IO_BYTES:
            self.rows.append(row)
            self._bytes += size


def _smallValue(value: object) -> bool:
    remaining = MAX_IO_BYTES

    def fits(item: object, depth: int) -> bool:
        nonlocal remaining
        remaining -= len(item) + 2 if isinstance(item, str) else 1
        if remaining < 0:
            return False
        if item is None or isinstance(item, (bool, str)):
            return True
        if isinstance(item, int):
            return item.bit_length() <= 128
        if isinstance(item, float):
            return math.isfinite(item)
        if depth >= 4:
            return False
        if isinstance(item, Mapping):
            return len(item) <= _MAX_VALUE_ITEMS and all(
                isinstance(key, str) and fits(key, depth + 1) and fits(child, depth + 1)
                for key, child in item.items()
            )
        if isinstance(item, (list, tuple)):
            return len(item) <= _MAX_VALUE_ITEMS and all(fits(child, depth + 1) for child in item)
        return False

    return fits(value, 0)
