"""Bounded JSON values shared by preparation, capture and consumers."""
import json
import math


def readValue(text: str):
    if len(text.encode("utf-8")) > 256 * 1024:
        raise ValueError("source exceeds byte budget")

    def constant(value):
        raise ValueError(f"non-finite JSON constant: {value}")

    try:
        value = json.loads(text, parse_constant=constant)
    except (RecursionError, OverflowError) as error:
        raise ValueError("JSON exceeds parsing limits") from error
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 12 or nodes > 16384:
            raise ValueError("value exceeds depth/node budget")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("non-finite JSON number")
        if isinstance(item, (dict, list)):
            if len(item) > 4096:
                raise ValueError("value exceeds collection budget")
            pending.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
    return value


def freezeValue(value) -> str:
    try:
        text = json.dumps(value, allow_nan=False, separators=(",", ":"))
        readValue(text)
    except (TypeError, RecursionError, OverflowError) as error:
        raise ValueError("unsupported value") from error
    return text
