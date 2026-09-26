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
    checkTree(value)
    return value


def checkTree(value):
    pending = [(value, 0)]
    nodes = 0
    stringBytes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 12 or nodes > 16384:
            raise ValueError("value exceeds depth/node budget")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("non-finite JSON number")
        if isinstance(item, str):
            if len(item) > 256 * 1024:
                raise ValueError("source exceeds byte budget")
            stringBytes += len(item.encode("utf-8"))
            if stringBytes > 256 * 1024:
                raise ValueError("source exceeds byte budget")
        if type(item) is int and item.bit_length() > 4096:
            raise ValueError("integer exceeds numeric parsing budget")
        if isinstance(item, (dict, list)):
            if len(item) > 4096:
                raise ValueError("value exceeds collection budget")
            pending.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
            if isinstance(item, dict):
                if not all(isinstance(key, str) for key in item):
                    raise ValueError("JSON keys must be strings")
                pending.extend((key, depth + 1) for key in item)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("unsupported value")


def freezeValue(value) -> str:
    try:
        checkTree(value)
        fragments = []
        length = 0
        for fragment in json.JSONEncoder(allow_nan=False, separators=(",", ":")).iterencode(value):
            length += len(fragment.encode())
            if length > 256 * 1024:
                raise ValueError("source exceeds byte budget")
            fragments.append(fragment)
        text = "".join(fragments)
    except (TypeError, RecursionError, OverflowError) as error:
        raise ValueError("unsupported value") from error
    return text
