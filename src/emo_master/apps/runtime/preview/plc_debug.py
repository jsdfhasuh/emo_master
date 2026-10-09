"""Bounded PLC debug owners, separate from workflow contracts and camera preview."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import math
import threading
import time
from typing import Callable, Protocol
from uuid import uuid4

from emo_master.core.contracts.communication import plcDeviceAddressSpan, plcWordsPerValue
from emo_master.plugins.builtins._slmp import (
    SlmpConnectionError, SlmpEndpoint, SlmpProtocolError, SlmpResponseError,
    SlmpTimeoutError, decodePlcWords, encodePlcValues,
)

PLC_OPERATORS = frozenset({"communication.plc.slmp_read", "communication.plc.slmp_write"})
LEASE_SECONDS = 30.0
MAX_SESSIONS = 8
MAX_WRITE_REQUESTS = 512


class _Transport(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def readWords(self, device: str, start: int, count: int) -> list[int]: ...
    def writeWords(self, device: str, start: int, values: list[int]) -> None: ...
    def readBits(self, start: int, count: int) -> list[bool]: ...
    def writeBits(self, start: int, values: list[bool]) -> None: ...


@dataclass(frozen=True)
class PlcDebugReply:
    ok: bool = False
    code: str = ""
    message: str = ""
    session_id: str = ""
    runtime_instance_id: str = ""
    state: str = "closed"
    write_enabled: bool = False
    result_json: str = "{}"
    elapsed_ms: float = 0.0
    timestamp_ms: int = 0
    ttl_ms: int = 0
    write_lock_generation: int = 0


def parseParams(raw: str) -> dict[str, object]:
    if len(raw.encode("utf-8")) > 65536:
        raise ValueError("PLC debug parameters exceed 64 KiB")
    try:
        value = json.loads(raw or "{}", parse_constant=_invalidConstant, parse_float=_finiteFloat)
    except RecursionError as error:
        raise ValueError("PLC debug JSON nesting is too deep") from error
    if not isinstance(value, dict):
        raise ValueError("PLC debug parameters must be an object")
    return value


def _invalidConstant(value: str):
    raise ValueError(f"non-finite JSON number: {value}")


def _finiteFloat(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def _integer(params: dict[str, object], name: str, default: int, minimum: int, maximum: int) -> int:
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def endpointFromParams(params: dict[str, object]) -> SlmpEndpoint:
    host = params.get("host", "127.0.0.1")
    if not isinstance(host, str) or not host.strip() or len(host) > 253:
        raise ValueError("host must be a non-empty hostname or IP address")
    return SlmpEndpoint(
        host=host.strip(), port=_integer(params, "port", 10001, 1, 65535),
        connectTimeoutSec=_integer(params, "connectTimeoutMs", 2000, 1, 60000) / 1000,
        responseTimeoutSec=_integer(params, "responseTimeoutMs", 2000, 1, 60000) / 1000,
        networkNo=_integer(params, "networkNo", 0, 0, 255),
        pcNo=_integer(params, "pcNo", 255, 0, 255),
        moduleIoNo=_integer(params, "moduleIoNo", 1023, 0, 65535),
        moduleStationNo=_integer(params, "moduleStationNo", 0, 0, 255),
        monitoringTimer=_integer(params, "monitoringTimer", 0, 0, 65535),
    )


def _range(params: dict[str, object], *, writing: bool = False):
    deviceValue, dataType = params.get("device", "D"), params.get("dataType", "uint16")
    if not isinstance(deviceValue, str):
        raise ValueError("device must be D or M")
    device = deviceValue.strip().upper()
    allowed = {"uint16", "int16", "uint32", "int32", "float32", "bit"}
    if not isinstance(dataType, str) or dataType not in allowed or device not in {"D", "M"}:
        raise ValueError("invalid PLC device or data type")
    if dataType == "bit" and device != "M":
        raise ValueError("bit access requires M")
    if device == "M" and dataType in {"uint32", "int32", "float32"}:
        raise ValueError("32-bit values require D")
    start = _integer(params, "startAddress", 0, 0, 0xFFFFFF)
    values: list[object] = []
    if writing:
        rawValues = params.get("values")
        if not isinstance(rawValues, list) or not 1 <= len(rawValues) <= 960:
            raise ValueError("values must contain 1..960 values")
        values = rawValues
        count = len(values)
    else:
        count = _integer(params, "count", 1, 1, 960)
    if dataType == "bit":
        if writing and any(not isinstance(value, bool) for value in values):
            raise ValueError("M bit values must be booleans")
        words, span = [], count
    else:
        wordCount = count * plcWordsPerValue(dataType)
        if wordCount > 960:
            raise ValueError("requested values exceed the 960-word SLMP limit")
        words = encodePlcValues(values, dataType) if writing else []
        span = plcDeviceAddressSpan(device, wordCount)
    if start + span - 1 > 0xFFFFFF:
        raise ValueError("PLC range exceeds the 24-bit address space")
    return device, start, dataType, count, values, words


@dataclass
class _Session:
    sessionId: str
    projectId: str
    endpoint: SlmpEndpoint
    client: _Transport
    expiresAt: float
    operation: threading.Lock = field(default_factory=threading.Lock)
    stateLock: threading.RLock = field(default_factory=threading.RLock)
    closed: threading.Event = field(default_factory=threading.Event)
    state: str = "connecting"
    writeEnabled: bool = False
    writeGeneration: int = 0
    writes: dict[str, tuple[str, PlcDebugReply]] = field(default_factory=dict)
    workflowId: str = ""
    nodeId: str = ""


class PlcDebugManager:
    def __init__(self, runtimeInstanceId: str, *, clock: Callable[[], float] = time.monotonic,
                 clientFactory: Callable[[SlmpEndpoint], _Transport] | None = None) -> None:
        if clientFactory is None:
            from emo_master.plugins.builtins._slmp import Slmp3ESession
            clientFactory = Slmp3ESession
        self.runtimeInstanceId = runtimeInstanceId
        self._clock, self._clientFactory = clock, clientFactory
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._expireLoop, name="plc-debug-leases", daemon=True)
        self._thread.start()

    def failure(self, code: str, message: str) -> PlcDebugReply:
        return PlcDebugReply(code=code, message=message, runtime_instance_id=self.runtimeInstanceId)

    def reserve(self, projectId: str, params: dict[str, object], *, workflowId: str = "", nodeId: str = "") -> _Session:
        endpoint = endpointFromParams(params)
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError("Runtime is closing")
            if len(self._sessions) >= MAX_SESSIONS:
                raise RuntimeError("PLC debug session limit reached")
            key = endpoint.host.lower(), endpoint.port
            if any((s.endpoint.host.lower(), s.endpoint.port) == key for s in self._sessions.values()):
                raise RuntimeError("PLC endpoint already has a debug session")
            session = _Session(uuid4().hex, projectId, endpoint, self._clientFactory(endpoint),
                               self._clock() + max(LEASE_SECONDS, endpoint.connectTimeoutSec + 2.0),
                               workflowId=workflowId, nodeId=nodeId)
            self._sessions[session.sessionId] = session
            return session

    def _reply(self, session: _Session, *, ok: bool = True, code: str = "", message: str = "",
               result: dict | None = None, started: float | None = None) -> PlcDebugReply:
        with session.stateLock:
            return PlcDebugReply(
                ok=ok, code=code, message=message, session_id=session.sessionId,
                runtime_instance_id=self.runtimeInstanceId, state=session.state,
                write_enabled=session.writeEnabled and not session.closed.is_set(),
                result_json=json.dumps(result or {}, ensure_ascii=False, allow_nan=False),
                elapsed_ms=(time.monotonic() - started) * 1000 if started is not None else 0,
                timestamp_ms=int(time.time() * 1000),
                ttl_ms=max(0, int((session.expiresAt - self._clock()) * 1000)),
                write_lock_generation=session.writeGeneration,
            )

    def connect(self, session: _Session, context=None) -> PlcDebugReply:
        started = time.monotonic()
        armed = self._bindCancellation(session, context)
        try:
            with session.operation:
                self._requireActive(session, context)
                session.client.open()
                self._requireActive(session, context)
                with session.stateLock:
                    self._requireActive(session, context)
                    session.state = "connected"
                    session.expiresAt = self._clock() + LEASE_SECONDS
                return self._reply(session, started=started, result={
                    "endpoint": f"{session.endpoint.host}:{session.endpoint.port}"})
        except Exception as error:
            self._invalidate(session, "error")
            return self._reply(session, ok=False, code=self._errorCode(error), message=str(error), started=started)
        finally:
            armed.clear()
            if session.closed.is_set():
                self.close(session.sessionId)

    def execute(self, sessionId: str, runtimeInstanceId: str, command: str, params: dict[str, object],
                requestId: str, context=None) -> PlcDebugReply:
        if runtimeInstanceId != self.runtimeInstanceId:
            return self.failure("E_PLC_CONTEXT_INVALID", "Runtime instance changed")
        with self._lock:
            session = self._sessions.get(sessionId)
        if session is None:
            return self.failure("E_PLC_SESSION_CLOSED", "PLC debug session is closed or expired")
        if not requestId or len(requestId) > 128:
            return self._reply(session, ok=False, code="E_PARAM_INVALID", message="request_id is required (max 128 characters)")
        if command not in {"read", "write", "set_write_enabled", "renew"}:
            return self._reply(session, ok=False, code="E_PARAM_INVALID", message="unsupported PLC debug command")
        # Permission versions fence even an older unlock that arrives after page leave.
        if command in {"set_write_enabled", "renew"}:
            try:
                with session.stateLock:
                    self._requireActive(session, context)
                    if command == "renew":
                        session.expiresAt = self._clock() + LEASE_SECONDS
                    elif not isinstance(params.get("enabled"), bool):
                        raise ValueError("enabled must be boolean")
                    elif not params["enabled"]:
                        session.writeGeneration += 1
                        session.writeEnabled = False
                    else:
                        generation = _integer(params, "lockGeneration", -1, 0, 0x7FFFFFFFFFFFFFFF)
                        if generation != session.writeGeneration:
                            return self._reply(session, ok=False, code="E_PLC_WRITE_LOCK_STALE",
                                               message="PLC write permission changed; refresh the session before unlocking")
                        session.writeEnabled = True
                return self._reply(session)
            except (ValueError, RuntimeError) as error:
                return self._reply(session, ok=False, code=self._errorCode(error), message=str(error))
        armed = self._bindCancellation(session, context)
        started = time.monotonic()
        attemptingWrite = False
        confirmedWrite = False
        acquired = False
        fingerprint = json.dumps(params, sort_keys=True, allow_nan=False)
        try:
            # Cancellation interrupts the socket owner, not just the waiting RPC.
            while not session.operation.acquire(timeout=0.05):
                self._requireActive(session, context)
            acquired = True
            if command == "write" and requestId in session.writes:
                return self._duplicateWrite(session, requestId, fingerprint)
            self._requireActive(session, context)
            device, start, dataType, count, values, words = _range(params, writing=command == "write")
            if command == "write":
                if len(session.writes) >= MAX_WRITE_REQUESTS:
                    return self._reply(session, ok=False, code="E_PLC_REQUEST_LIMIT", message="write request limit reached; reconnect before writing again",
                                       result={"receipt": {"outcome": "rejected"}, "readback": None, "readbackError": ""})
                with session.stateLock:
                    if not session.writeEnabled:
                        reply = self._reply(session, ok=False, code="E_PLC_WRITE_LOCKED", message="PLC writing is locked",
                                            result={"receipt": {"outcome": "rejected"}, "readback": None, "readbackError": ""})
                        session.writes[requestId] = fingerprint, reply
                        return reply
                    generation = _integer(params, "lockGeneration", -1, 0, 0x7FFFFFFFFFFFFFFF)
                    if generation != session.writeGeneration:
                        reply = self._reply(session, ok=False, code="E_PLC_WRITE_LOCK_STALE",
                                            message="PLC write permission changed after this request was captured",
                                            result={"receipt": {"outcome": "rejected"}, "readback": None, "readbackError": ""})
                        session.writes[requestId] = fingerprint, reply
                        return reply
                    self._requireActive(session, context)
                    attemptingWrite = True
                    pending = self._reply(session, ok=False, code="E_PLC_WRITE_UNKNOWN", message="write acknowledgement is pending",
                                          result={"receipt": {"outcome": "unknown"}, "readback": None, "readbackError": ""})
                    session.writes[requestId] = fingerprint, pending
                if dataType == "bit":
                    session.client.writeBits(start, values)
                else:
                    session.client.writeWords(device, start, words)
                confirmedWrite = True
                result = {"receipt": {"outcome": "confirmed", "device": device,
                                       "startAddress": start, "dataType": dataType,
                                       "valueCount": count}, "readback": None, "readbackError": ""}
                try:
                    self._requireActive(session, context)
                    result["readback"] = self._read(session, device, start, dataType, count)
                except Exception as error:
                    result["readbackError"] = str(error)
                    self._invalidate(session, "error")
                with session.stateLock:
                    if not session.closed.is_set():
                        session.state = "verified"
                    reply = self._reply(session, result=result, started=started)
                    session.writes[requestId] = fingerprint, reply
                return reply
            result = self._read(session, device, start, dataType, count)
            with session.stateLock:
                self._requireActive(session, context)
                session.state = "verified"
            return self._reply(session, result=result, started=started)
        except Exception as error:
            if not acquired and command == "write" and requestId in session.writes:
                return self._duplicateWrite(session, requestId, fingerprint)
            unknown = attemptingWrite and not confirmedWrite and not isinstance(error, SlmpResponseError)
            if isinstance(error, (SlmpConnectionError, SlmpProtocolError, SlmpResponseError)) or unknown or session.closed.is_set():
                self._invalidate(session, "error")
            result = {"receipt": {"outcome": "unknown" if unknown else "rejected"},
                      "readback": None, "readbackError": ""} if command == "write" else {}
            reply = self._reply(session, ok=False, code="E_PLC_WRITE_UNKNOWN" if unknown else self._errorCode(error),
                                message="写入结果未知，未自动重试；" + str(error) if unknown else str(error),
                                result=result, started=started)
            if acquired and command == "write" and (requestId in session.writes or len(session.writes) < MAX_WRITE_REQUESTS):
                with session.stateLock:
                    session.writes[requestId] = fingerprint, reply
            return reply
        finally:
            if acquired:
                session.operation.release()
            armed.clear()
            if session.closed.is_set():
                self.close(session.sessionId)

    def _duplicateWrite(self, session: _Session, requestId: str, fingerprint: str) -> PlcDebugReply:
        with session.stateLock:
            oldFingerprint, oldReply = session.writes[requestId]
            if oldFingerprint != fingerprint:
                return self._reply(session, ok=False, code="E_PLC_REQUEST_CONFLICT", message="request_id was used with different values",
                                   result={"receipt": {"outcome": "rejected"}, "readback": None, "readbackError": ""})
            return replace(oldReply, state=session.state, write_enabled=session.writeEnabled and not session.closed.is_set(),
                           write_lock_generation=session.writeGeneration)

    def _read(self, session, device: str, start: int, dataType: str, count: int) -> dict:
        if dataType == "bit":
            values = session.client.readBits(start, count)
            rawWords = []
            addresses = [f"M{start + index}" for index in range(count)]
        else:
            perValue = plcWordsPerValue(dataType)
            rawWords = session.client.readWords(device, start, count * perValue)
            values = list(decodePlcWords(rawWords, dataType))
            span = plcDeviceAddressSpan(device, perValue)
            addresses = [f"{device}{start + index * span}" +
                         (f"-{device}{start + (index + 1) * span - 1}" if span > 1 else "")
                         for index in range(count)]
        return {"device": device, "startAddress": start, "dataType": dataType,
                "count": count, "values": values, "rawWords": rawWords, "addresses": addresses}

    def _requireActive(self, session: _Session, context=None) -> None:
        active = getattr(context, "is_active", lambda: True)
        # Renewal and expiry must observe one state transition, including calls
        # made while waiting for the serial operation lock.
        with session.stateLock:
            if not (session.closed.is_set() or session.expiresAt <= self._clock() or not active()):
                return
            self._retireState(session, "closed")
        session.client.close()
        raise RuntimeError("PLC debug session was closed, cancelled or expired")

    def _bindCancellation(self, session: _Session, context):
        armed = threading.Event()
        armed.set()
        register = getattr(context, "add_callback", None)
        if callable(register):
            def cancelled():
                if armed.is_set():
                    self._invalidate(session, "closed")
            if register(cancelled) is False:
                cancelled()
        return armed

    def _invalidate(self, session: _Session, state: str = "closed") -> None:
        with session.stateLock:
            self._retireState(session, state)
        session.client.close()

    @staticmethod
    def _retireState(session: _Session, state: str) -> None:
        session.closed.set()
        session.writeEnabled = False
        session.writeGeneration += 1
        session.state = state

    def close(self, sessionId: str, timeoutSeconds: float = 3.0) -> str | None:
        with self._lock:
            session = self._sessions.get(sessionId)
        if session is None:
            return None
        self._invalidate(session)
        if not session.operation.acquire(timeout=max(0.0, timeoutSeconds)):
            return "PLC debug work still owns a connection; release is unconfirmed"
        try:
            with self._lock:
                if self._sessions.get(sessionId) is session:
                    del self._sessions[sessionId]
        finally:
            session.operation.release()
        return None

    def closeProject(self, projectId: str, timeoutSeconds: float = 3.0) -> list[str]:
        with self._lock:
            sessions = [s for s in self._sessions.values() if s.projectId == projectId]
        # Invalidate the complete set before waiting for any individual owner.
        for session in sessions:
            self._invalidate(session)
        deadline = time.monotonic() + timeoutSeconds
        return [error for session in sessions if (error := self.close(
            session.sessionId, max(0.0, deadline - time.monotonic())))]

    def expire(self) -> None:
        with self._lock:
            sessions = [s for s in self._sessions.values() if s.expiresAt <= self._clock()]
        for session in sessions:
            with session.stateLock:
                if session.expiresAt > self._clock():
                    continue
                self._invalidate(session)
            self.close(session.sessionId)

    def _expireLoop(self) -> None:
        while not self._stop.wait(1.0):
            self.expire()

    def closeAll(self) -> list[str]:
        self._stop.set()
        errors = self.closeSessions()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=3.5)
            if self._thread.is_alive():
                errors.append("PLC debug lease worker has not retired")
        return errors

    def closeSessions(self) -> list[str]:
        with self._lock:
            projects = {s.projectId for s in self._sessions.values()}
        return [error for project in projects for error in self.closeProject(project)]

    @staticmethod
    def _errorCode(error: Exception) -> str:
        if isinstance(error, (ValueError, TypeError)):
            return "E_PARAM_INVALID"
        if isinstance(error, SlmpTimeoutError):
            return "E_TIMEOUT"
        if isinstance(error, SlmpResponseError):
            return "E_PLC_RESPONSE"
        if isinstance(error, SlmpProtocolError):
            return "E_PROTOCOL_INVALID"
        if isinstance(error, SlmpConnectionError):
            return "E_CONNECTION_FAILED"
        return "E_PLC_SESSION_CLOSED"
