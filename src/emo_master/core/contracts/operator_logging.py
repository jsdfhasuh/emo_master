from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast, runtime_checkable


@runtime_checkable
class OperatorLoggerProtocol(Protocol):
    """Restricted structured logger made available through operator contexts."""

    def isEnabledFor(self, level: str) -> bool:
        ...

    def log(
        self,
        level: str,
        message: str,
        *,
        code: str = "",
        payload: Mapping[str, object] | None = None,
    ) -> None:
        ...

    def debug(
        self,
        message: str,
        *,
        code: str = "",
        payload: Mapping[str, object] | None = None,
    ) -> None:
        ...

    def info(
        self,
        message: str,
        *,
        code: str = "",
        payload: Mapping[str, object] | None = None,
    ) -> None:
        ...

    def warning(
        self,
        message: str,
        *,
        code: str = "",
        payload: Mapping[str, object] | None = None,
    ) -> None:
        ...

    def error(
        self,
        message: str,
        *,
        code: str = "",
        payload: Mapping[str, object] | None = None,
    ) -> None:
        ...


class NullOperatorLogger:
    """Compatibility logger used by old runtimes, tests, and preview execution."""

    def isEnabledFor(self, level: str) -> bool:
        _ = level
        return False

    def log(
        self,
        level: str,
        message: str,
        *,
        code: str = "",
        payload: Mapping[str, object] | None = None,
    ) -> None:
        _ = level, message, code, payload

    def debug(self, message: str, *, code: str = "", payload=None) -> None:
        self.log("DEBUG", message, code=code, payload=payload)

    def info(self, message: str, *, code: str = "", payload=None) -> None:
        self.log("INFO", message, code=code, payload=payload)

    def warning(self, message: str, *, code: str = "", payload=None) -> None:
        self.log("WARN", message, code=code, payload=payload)

    def error(self, message: str, *, code: str = "", payload=None) -> None:
        self.log("ERROR", message, code=code, payload=payload)


_NULL_LOGGER = NullOperatorLogger()


def getOperatorLogger(context: Mapping[str, object] | None) -> OperatorLoggerProtocol:
    """Return the injected logger without making operators depend on Runtime internals."""

    logger = context.get("logger") if isinstance(context, Mapping) else None
    if isinstance(logger, OperatorLoggerProtocol):
        return cast(OperatorLoggerProtocol, logger)
    return _NULL_LOGGER
