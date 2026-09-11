from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
import os
import threading
import time
from typing import TYPE_CHECKING, Callable

from emo_master.core.contracts.operator_logging import OperatorLoggerProtocol

if TYPE_CHECKING:
    from emo_master.apps.runtime.workflow.context import RunContext


_LEVEL_VALUES = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "authorization",
    "credential",
    "apikey",
    "privatekey",
)


@dataclass
class _Bucket:
    tokens: float
    updatedAt: float
    lastSummaryAt: float


class OperatorLogManager:
    """Job-scoped logger factory and rate limiter owned by WorkflowRunner."""

    def __init__(
        self,
        publisher: Callable[..., object] | None,
        *,
        minimumLevel: str | None = None,
        ratePerSecond: float = 20.0,
        burst: int = 50,
        maxEvents: int = 50000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        configuredLevel = minimumLevel or os.environ.get(
            "EMO_RUNTIME_OPERATOR_LOG_LEVEL", "INFO"
        )
        self.publisher = publisher
        self.minimumLevel = _normalizeLevel(configuredLevel, fallback="INFO")
        self.ratePerSecond = max(0.1, float(ratePerSecond))
        self.burst = max(1, int(burst))
        self.maxEvents = max(1, int(maxEvents))
        self.clock = clock
        self._lock = threading.RLock()
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._suppressed: dict[tuple[str, str, str], dict[str, int]] = {}
        self._hardLimitSuppressed: dict[str, int] = {}
        self._hardLimitSummaryPublished = False
        self._hardLimitSummaryContext: tuple[RunContext, str, str] | None = None
        self._finalized = False
        self._eventCount = 0

    def createLogger(
        self,
        context: RunContext,
        operatorId: str,
        phase: str,
    ) -> "OperatorLogger":
        return OperatorLogger(self, context, str(operatorId), str(phase))

    def isEnabledFor(self, level: str) -> bool:
        try:
            normalized = _normalizeLevel(level)
            return (
                self.publisher is not None
                and _LEVEL_VALUES[normalized] >= _LEVEL_VALUES[self.minimumLevel]
            )
        except BaseException:
            return False

    def emit(
        self,
        context: RunContext,
        operatorId: str,
        phase: str,
        level: str,
        message: str,
        code: str,
        payload: Mapping[str, object] | None,
    ) -> str:
        normalized = _normalizeLevel(level)
        if not self.isEnabledFor(normalized):
            return "disabled"
        now = self.clock()
        bucketKey = (context.workflowId, context.callerNodeId)
        suppressionKey = (*bucketKey, phase)
        summary: dict[str, int] | None = None
        with self._lock:
            if self._eventCount >= self.maxEvents:
                self._hardLimitSuppressed[normalized] = (
                    self._hardLimitSuppressed.get(normalized, 0) + 1
                )
                self._hardLimitSummaryContext = (context, operatorId, phase)
                return "suppressed"
            bucket = self._buckets.get(bucketKey)
            if bucket is None:
                bucket = _Bucket(float(self.burst), now, now)
                self._buckets[bucketKey] = bucket
            elapsed = max(0.0, now - bucket.updatedAt)
            bucket.tokens = min(
                float(self.burst), bucket.tokens + elapsed * self.ratePerSecond
            )
            bucket.updatedAt = now
            if normalized in {"DEBUG", "INFO"}:
                if bucket.tokens < 1.0:
                    self._recordSuppressed(suppressionKey, normalized)
                    return "suppressed"
                bucket.tokens -= 1.0
            if now - bucket.lastSummaryAt >= 5.0:
                summary = self._suppressed.pop(suppressionKey, None)
                bucket.lastSummaryAt = now
            self._eventCount += 1
        if summary:
            self._publishSuppressed(context, operatorId, phase, summary)
        safeMessage, messageTruncated = _truncateUtf8(str(message), 4096)
        safeCode, _ = _truncateUtf8(str(code), 256)
        safePayload, payloadTruncated = _safePayload(payload)
        eventPayload: dict[str, object] = {
            "operatorId": operatorId,
            "phase": phase,
            "data": safePayload,
        }
        if messageTruncated or payloadTruncated:
            eventPayload["truncated"] = True
        try:
            assert self.publisher is not None
            self.publisher(
                "node.log",
                context,
                safeMessage,
                level=normalized,
                code=safeCode,
                payload=eventPayload,
            )
        except BaseException:
            return "failed"
        return "emitted"

    def flushSuppressed(
        self,
        context: RunContext,
        operatorId: str,
        phase: str,
    ) -> None:
        key = (context.workflowId, context.callerNodeId, phase)
        with self._lock:
            summary = self._suppressed.pop(key, None)
            if (
                self._hardLimitSuppressed
                and not self._hardLimitSummaryPublished
            ):
                summary = dict(summary or {})
                for level, count in self._hardLimitSuppressed.items():
                    summary[level] = summary.get(level, 0) + count
                self._hardLimitSuppressed.clear()
                self._hardLimitSummaryContext = None
                self._hardLimitSummaryPublished = True
        if summary:
            self._publishSuppressed(context, operatorId, phase, summary)

    def finalize(self) -> None:
        """Publish hard-limit drops accumulated after the first node summary."""

        with self._lock:
            if self._finalized:
                return
            self._finalized = True
            summary = dict(self._hardLimitSuppressed)
            self._hardLimitSuppressed.clear()
            summaryContext = self._hardLimitSummaryContext
            self._hardLimitSummaryContext = None
        if summary and summaryContext is not None:
            context, operatorId, phase = summaryContext
            self._publishSuppressed(context, operatorId, phase, summary)

    def _recordSuppressed(self, key: tuple[str, str, str], level: str) -> None:
        counts = self._suppressed.setdefault(key, {})
        counts[level] = counts.get(level, 0) + 1

    def _publishSuppressed(
        self,
        context: RunContext,
        operatorId: str,
        phase: str,
        counts: Mapping[str, int],
    ) -> None:
        if self.publisher is None:
            return
        try:
            self.publisher(
                "node.log",
                context,
                "operator logs were dropped by runtime limits",
                level="WARN",
                code="W_OPERATOR_LOG_DROPPED",
                payload={
                    "operatorId": operatorId,
                    "phase": phase,
                    "data": {"dropped": dict(counts)},
                },
            )
        except BaseException:
            pass


class OperatorLogger(OperatorLoggerProtocol):
    def __init__(
        self,
        manager: OperatorLogManager,
        context: RunContext,
        operatorId: str,
        phase: str,
    ) -> None:
        self.manager = manager
        self.context = context
        self.operatorId = operatorId
        self.phase = phase
        self._lock = threading.Lock()
        self._closed = False
        self._publishFailures = 0
        self._suppressed = 0

    def isEnabledFor(self, level: str) -> bool:
        try:
            with self._lock:
                return not self._closed and self.manager.isEnabledFor(level)
        except BaseException:
            return False

    def log(
        self,
        level: str,
        message: str,
        *,
        code: str = "",
        payload: Mapping[str, object] | None = None,
    ) -> None:
        try:
            # Keep close() and emit mutually exclusive.  An operator thread that
            # reaches this lock after node completion observes _closed and its
            # late record is discarded instead of appearing after node.completed.
            with self._lock:
                if self._closed:
                    return
                outcome = self.manager.emit(
                    self.context,
                    self.operatorId,
                    self.phase,
                    level,
                    message,
                    code,
                    payload,
                )
                if outcome == "failed":
                    self._publishFailures += 1
                elif outcome == "suppressed":
                    self._suppressed += 1
        except BaseException:
            # Logging is observational and must never become an operator failure.
            try:
                with self._lock:
                    self._publishFailures += 1
            except BaseException:
                pass

    def debug(self, message: str, *, code: str = "", payload=None) -> None:
        self.log("DEBUG", message, code=code, payload=payload)

    def info(self, message: str, *, code: str = "", payload=None) -> None:
        self.log("INFO", message, code=code, payload=payload)

    def warning(self, message: str, *, code: str = "", payload=None) -> None:
        self.log("WARN", message, code=code, payload=payload)

    def error(self, message: str, *, code: str = "", payload=None) -> None:
        self.log("ERROR", message, code=code, payload=payload)

    def close(self) -> dict[str, object]:
        with self._lock:
            if self._closed:
                values: dict[str, object] = {}
                if self._publishFailures:
                    values["publishFailures"] = self._publishFailures
                if self._suppressed:
                    values["suppressed"] = self._suppressed
                return values
            self._closed = True
        self.manager.flushSuppressed(self.context, self.operatorId, self.phase)
        return self.diagnostics()

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            values: dict[str, object] = {}
            if self._publishFailures:
                values["publishFailures"] = self._publishFailures
            if self._suppressed:
                values["suppressed"] = self._suppressed
            return values


class BufferedOperatorLogger(OperatorLoggerProtocol):
    """Bounded in-memory logger for previews that have no Job event stream."""

    def __init__(self, maximumRecords: int = 200, minimumLevel: str = "DEBUG") -> None:
        self.maximumRecords = max(1, int(maximumRecords))
        self.minimumLevel = _normalizeLevel(minimumLevel)
        self._lock = threading.RLock()
        self._records: list[dict[str, object]] = []

    def isEnabledFor(self, level: str) -> bool:
        try:
            normalized = _normalizeLevel(level)
            return _LEVEL_VALUES[normalized] >= _LEVEL_VALUES[self.minimumLevel]
        except BaseException:
            return False

    def log(
        self,
        level: str,
        message: str,
        *,
        code: str = "",
        payload: Mapping[str, object] | None = None,
    ) -> None:
        try:
            normalized = _normalizeLevel(level)
            if not self.isEnabledFor(normalized):
                return
            safeMessage, truncated = _truncateUtf8(str(message), 4096)
            safePayload, payloadTruncated = _safePayload(payload)
            record: dict[str, object] = {
                "level": normalized,
                "message": safeMessage,
                "code": _truncateUtf8(str(code), 256)[0],
                "payload": safePayload,
            }
            if truncated or payloadTruncated:
                record["truncated"] = True
            with self._lock:
                self._records.append(record)
                if len(self._records) > self.maximumRecords:
                    del self._records[: len(self._records) - self.maximumRecords]
        except BaseException:
            return

    def debug(self, message: str, *, code: str = "", payload=None) -> None:
        self.log("DEBUG", message, code=code, payload=payload)

    def info(self, message: str, *, code: str = "", payload=None) -> None:
        self.log("INFO", message, code=code, payload=payload)

    def warning(self, message: str, *, code: str = "", payload=None) -> None:
        self.log("WARN", message, code=code, payload=payload)

    def error(self, message: str, *, code: str = "", payload=None) -> None:
        self.log("ERROR", message, code=code, payload=payload)

    def records(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(dict(record) for record in self._records)

    def summary(self, maximumRecords: int = 5) -> str:
        with self._lock:
            records = self._records[-max(1, maximumRecords) :]
        if not records:
            return ""
        return " | ".join(
            f"{record['level']}: {record['message']}" for record in records
        )


def _normalizeLevel(value: object, fallback: str = "INFO") -> str:
    normalized = str(value).strip().upper()
    if normalized == "WARNING":
        normalized = "WARN"
    return normalized if normalized in _LEVEL_VALUES else fallback


def _safePayload(
    payload: Mapping[str, object] | None,
) -> tuple[dict[str, object], bool]:
    safe = _safeValue(dict(payload or {}), depth=0, seen=set())
    if not isinstance(safe, dict):
        safe = {"value": safe}
    encoded = json.dumps(
        safe,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) <= 65536:
        return safe, False
    return {
        "truncated": True,
        "originalBytes": len(encoded),
    }, True


def _safeValue(value: object, *, depth: int, seen: set[int]) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "<non-finite>"
    if isinstance(value, str):
        return _truncateUtf8(value, 4096)[0]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<bytes:{len(value)}>"
    if depth >= 8:
        return "<max-depth>"
    identity = id(value)
    if isinstance(value, Mapping):
        if identity in seen:
            return "<cycle>"
        seen.add(identity)
        try:
            result: dict[str, object] = {}
            for rawKey, item in value.items():
                key = _truncateUtf8(str(rawKey), 256)[0]
                normalizedKey = "".join(character for character in key.casefold() if character.isalnum())
                if any(part in normalizedKey for part in _SENSITIVE_KEY_PARTS):
                    result[key] = "<redacted>"
                else:
                    result[key] = _safeValue(item, depth=depth + 1, seen=seen)
            return result
        finally:
            seen.discard(identity)
    if isinstance(value, (list, tuple, set, frozenset)):
        if identity in seen:
            return "<cycle>"
        seen.add(identity)
        try:
            return [
                _safeValue(item, depth=depth + 1, seen=seen)
                for item in list(value)[:1000]
            ]
        finally:
            seen.discard(identity)
    shape = getattr(value, "shape", None)
    dtype = getattr(value, "dtype", None)
    if shape is not None or dtype is not None:
        return f"<{type(value).__name__} shape={shape} dtype={dtype}>"
    return f"<{type(value).__name__}>"


def _truncateUtf8(value: str, maximumBytes: int) -> tuple[str, bool]:
    maximumBytes = max(0, int(maximumBytes))
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= maximumBytes:
        return value, False
    marker = "…".encode("utf-8")
    if maximumBytes < len(marker):
        return "", True
    shortened = encoded[: maximumBytes - len(marker)]
    while shortened:
        try:
            return shortened.decode("utf-8") + marker.decode("utf-8"), True
        except UnicodeDecodeError:
            shortened = shortened[:-1]
    return marker.decode("utf-8"), True
