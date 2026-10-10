from __future__ import annotations

import errno
import math
import queue
import select
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence, TypeVar

from emo_master.core.contracts.communication import PlcScalar, plcDeviceAddressSpan


DEVICE_CODES = {"D": 0xA8, "M": 0x90}
MAX_WORDS_PER_REQUEST = 960
MAX_BITS_PER_REQUEST = 960
MAX_RESPONSE_BODY_BYTES = 2 + MAX_WORDS_PER_REQUEST * 2


class SlmpError(Exception):
    """Base exception for Mitsubishi SLMP communication failures."""


class SlmpConnectionError(SlmpError):
    """Raised when a TCP connection cannot be established or is lost."""


class SlmpTimeoutError(SlmpConnectionError):
    """Raised when a bounded connect/send/receive operation times out."""


class SlmpProtocolError(SlmpError):
    """Raised when a peer response is malformed or internally inconsistent."""


class SlmpResponseError(SlmpError):
    """Raised when the PLC returns a non-zero SLMP end code."""

    def __init__(self, endCode: int) -> None:
        self.endCode = endCode
        super().__init__(f"PLC returned SLMP end code 0x{endCode:04X}")


@dataclass(frozen=True)
class SlmpEndpoint:
    host: str
    port: int
    connectTimeoutSec: float = 2.0
    responseTimeoutSec: float = 2.0
    networkNo: int = 0x00
    pcNo: int = 0xFF
    moduleIoNo: int = 0x03FF
    moduleStationNo: int = 0x00
    monitoringTimer: int = 0x0000

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("host must be a non-empty string")
        if not isinstance(self.port, int) or isinstance(self.port, bool):
            raise TypeError("port must be an integer")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        for name, value, maximum in (
            ("networkNo", self.networkNo, 0xFF),
            ("pcNo", self.pcNo, 0xFF),
            ("moduleIoNo", self.moduleIoNo, 0xFFFF),
            ("moduleStationNo", self.moduleStationNo, 0xFF),
            ("monitoringTimer", self.monitoringTimer, 0xFFFF),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an integer")
            if not 0 <= value <= maximum:
                raise ValueError(f"{name} must be between 0 and {maximum}")
        for name, timeoutValue in (
            ("connectTimeoutSec", self.connectTimeoutSec),
            ("responseTimeoutSec", self.responseTimeoutSec),
        ):
            if not isinstance(timeoutValue, (int, float)) or isinstance(
                timeoutValue, bool
            ):
                raise TypeError(f"{name} must be a number")
            if (
                not math.isfinite(float(timeoutValue))
                or not 0.001 <= float(timeoutValue) <= 60.0
            ):
                raise ValueError(f"{name} must be between 0.001 and 60 seconds")


class Slmp3EClient:
    SUBHEADER_REQUEST = b"\x50\x00"
    SUBHEADER_RESPONSE = b"\xd0\x00"
    COMMAND_BATCH_READ = 0x0401
    COMMAND_BATCH_WRITE = 0x1401
    SUBCOMMAND_WORD = 0x0000

    def __init__(self, endpoint: SlmpEndpoint) -> None:
        self.endpoint = endpoint

    def readWords(self, device: str, start: int, count: int) -> list[int]:
        deviceCode = _deviceCode(device)
        _validateDeviceRange(device, start, count)
        request = self._buildDeviceRequest(
            command=self.COMMAND_BATCH_READ,
            deviceCode=deviceCode,
            start=start,
            count=count,
        )
        data = self._exchange(request)
        _validateReadDataLength(data, count * 2)
        return list(struct.unpack("<" + "H" * count, data))

    def writeWords(self, device: str, start: int, values: list[int]) -> None:
        if not values:
            raise ValueError("values cannot be empty")
        deviceCode = _deviceCode(device)
        _validateDeviceRange(device, start, len(values))
        data = b"".join(_packWord(value) for value in values)
        request = self._buildDeviceRequest(
            command=self.COMMAND_BATCH_WRITE,
            deviceCode=deviceCode,
            start=start,
            count=len(values),
            data=data,
        )
        responseData = self._exchange(request)
        _validateWriteData(responseData)

    def _exchange(self, request: bytes) -> bytes:
        sock = self._openSocket()
        try:
            body = _exchangeOnSocket(
                sock, request, self.endpoint, responseSubheader=self.SUBHEADER_RESPONSE
            )
        finally:
            _safeClose(sock)
        return _responseData(body)

    def _openSocket(self) -> socket.socket:
        sock: socket.socket | None = None
        try:
            sock = socket.create_connection(
                (self.endpoint.host, self.endpoint.port),
                timeout=self.endpoint.connectTimeoutSec,
            )
            sock.settimeout(self.endpoint.responseTimeoutSec)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            return sock
        except socket.timeout as exc:
            if sock is not None:
                _safeClose(sock)
            raise SlmpTimeoutError(
                f"PLC connect timed out at {self.endpoint.host}:{self.endpoint.port}"
            ) from exc
        except OSError as exc:
            if sock is not None:
                _safeClose(sock)
            raise SlmpConnectionError(
                f"cannot connect to PLC {self.endpoint.host}:{self.endpoint.port}: {exc}"
            ) from exc

    def _buildDeviceRequest(
        self,
        *,
        command: int,
        deviceCode: int,
        start: int,
        count: int,
        data: bytes = b"",
        subcommand: int | None = None,
    ) -> bytes:
        requestData = (
            struct.pack(
                "<HHH",
                self.endpoint.monitoringTimer,
                command,
                self.SUBCOMMAND_WORD if subcommand is None else subcommand,
            )
            + start.to_bytes(3, byteorder="little", signed=False)
            + bytes([deviceCode])
            + struct.pack("<H", count)
            + data
        )
        return (
            self.SUBHEADER_REQUEST
            + struct.pack(
                "<BBHBH",
                self.endpoint.networkNo,
                self.endpoint.pcNo,
                self.endpoint.moduleIoNo,
                self.endpoint.moduleStationNo,
                len(requestData),
            )
            + requestData
        )


@dataclass
class _SlmpResolution:
    endpoint: SlmpEndpoint
    cancelled: threading.Event
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    addresses: Sequence[tuple[int, int, int, str, tuple]] = field(default_factory=list)
    error: BaseException | None = None


class _SlmpResolver:
    """Bound native DNS calls and queued work even after callers abandon them."""

    def __init__(self) -> None:
        self._jobs: queue.Queue[_SlmpResolution] = queue.Queue(maxsize=2)
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []

    def resolve(
        self, endpoint: SlmpEndpoint, cancelled: threading.Event, deadline: float
    ) -> Sequence[tuple[int, int, int, str, tuple]]:
        with self._lock:
            while len(self._workers) < 2:
                worker = threading.Thread(
                    target=self._run,
                    name=f"slmp-debug-dns-{len(self._workers)}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)
        job = _SlmpResolution(endpoint, cancelled, deadline)
        while True:
            remaining = _connectTimeRemaining(cancelled, deadline)
            try:
                self._jobs.put_nowait(job)
                break
            except queue.Full:
                cancelled.wait(min(remaining, 0.05))
        while not job.done.is_set():
            remaining = _connectTimeRemaining(cancelled, deadline)
            job.done.wait(min(remaining, 0.05))
        _connectTimeRemaining(cancelled, deadline)
        if job.error is not None:
            raise job.error
        return job.addresses

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                _connectTimeRemaining(job.cancelled, job.deadline)
                job.addresses = socket.getaddrinfo(
                    job.endpoint.host, job.endpoint.port, type=socket.SOCK_STREAM
                )
            except BaseException as exc:
                job.error = exc
            finally:
                job.done.set()
                self._jobs.task_done()


_SLMP_RESOLVER = _SlmpResolver()


def _resolveSessionAddresses(
    endpoint: SlmpEndpoint, cancelled: threading.Event, deadline: float
) -> Sequence[tuple[int, int, int, str, tuple]]:
    _connectTimeRemaining(cancelled, deadline)
    try:
        addresses = socket.getaddrinfo(
            endpoint.host,
            endpoint.port,
            type=socket.SOCK_STREAM,
            flags=socket.AI_NUMERICHOST,
        )
    except socket.gaierror:
        return _SLMP_RESOLVER.resolve(endpoint, cancelled, deadline)
    _connectTimeRemaining(cancelled, deadline)
    return addresses


def _connectTimeRemaining(cancelled: threading.Event, deadline: float) -> float:
    if cancelled.is_set():
        raise SlmpConnectionError("SLMP session was closed during connect")
    remaining = deadline - time.monotonic()
    if remaining <= 0.0:
        raise socket.timeout("SLMP connect deadline exceeded")
    return remaining


@dataclass
class _SlmpSessionState:
    cancelled: threading.Event = field(default_factory=threading.Event)
    sock: socket.socket | None = None
    connected: bool = False
    exchanging: bool = False


class Slmp3ESession(Slmp3EClient):
    """Explicitly opened debug connection; callers serialize word/bit operations.

    close() may run on another thread to abort connect or I/O. Errors retire the
    connection; only an explicit open() can establish another one.

    DNS waits share the connect deadline. Native resolver calls cannot be killed;
    at most two daemon workers may remain occupied after cancellation/timeout.
    """

    SUBCOMMAND_BIT = 0x0001

    def __init__(self, endpoint: SlmpEndpoint) -> None:
        super().__init__(endpoint)
        self._stateLock = threading.Lock()
        self._state: _SlmpSessionState | None = None

    def open(self) -> None:
        with self._stateLock:
            if self._state is not None:
                if self._state.connected:
                    return
                raise SlmpConnectionError("SLMP session is already connecting")
            state = _SlmpSessionState()
            self._state = state
        try:
            deadline = time.monotonic() + self.endpoint.connectTimeoutSec
            addresses = _resolveSessionAddresses(
                self.endpoint, state.cancelled, deadline
            )
            lastError: OSError | None = None
            for family, sockType, protocol, _, address in addresses:
                _connectTimeRemaining(state.cancelled, deadline)
                sock = socket.socket(family, sockType, protocol)
                with self._stateLock:
                    current = self._state is state
                    if current:
                        state.sock = sock
                if not current:
                    _safeClose(sock)
                    raise SlmpConnectionError("SLMP session was closed during connect")
                try:
                    _connectSessionSocket(sock, address, state.cancelled, deadline)
                    with self._stateLock:
                        if self._state is not state:
                            raise SlmpConnectionError(
                                "SLMP session was closed during connect"
                            )
                        sock.settimeout(self.endpoint.responseTimeoutSec)
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                        state.connected = True
                    return
                except socket.timeout:
                    raise
                except OSError as exc:
                    if state.cancelled.is_set():
                        raise SlmpConnectionError(
                            "SLMP session was closed during connect"
                        ) from exc
                    lastError = exc
                    with self._stateLock:
                        if state.sock is sock:
                            state.sock = None
                    _safeClose(sock)
            raise lastError or OSError("no TCP addresses found for PLC")
        except socket.timeout as exc:
            self._closeState(state)
            raise SlmpTimeoutError(
                f"PLC connect timed out at {self.endpoint.host}:{self.endpoint.port}"
            ) from exc
        except OSError as exc:
            self._closeState(state)
            raise SlmpConnectionError(
                f"cannot connect to PLC {self.endpoint.host}:{self.endpoint.port}: {exc}"
            ) from exc
        except BaseException:
            self._closeState(state)
            raise

    def close(self) -> None:
        self._closeState()

    def _closeState(self, expected: _SlmpSessionState | None = None) -> None:
        with self._stateLock:
            state = self._state
            if state is None or (expected is not None and state is not expected):
                return
            self._state = None
            state.cancelled.set()
            sock, state.sock = state.sock, None
        if sock is not None:
            _safeClose(sock)

    def readBits(self, start: int, count: int) -> list[bool]:
        _validateBitRange(start, count)
        request = self._buildDeviceRequest(
            command=self.COMMAND_BATCH_READ,
            subcommand=self.SUBCOMMAND_BIT,
            deviceCode=DEVICE_CODES["M"],
            start=start,
            count=count,
        )
        return _decodeBits(self._exchange(request), count)

    def writeBits(self, start: int, values: list[bool]) -> None:
        if not isinstance(values, list):
            raise TypeError("values must be a list of bools")
        _validateBitRange(start, len(values))
        for index, value in enumerate(values):
            if not isinstance(value, bool):
                raise TypeError(f"values[{index}] must be a bool")
        data = bytes(
            (int(values[index]) << 4)
            | (int(values[index + 1]) if index + 1 < len(values) else 0)
            for index in range(0, len(values), 2)
        )
        request = self._buildDeviceRequest(
            command=self.COMMAND_BATCH_WRITE,
            subcommand=self.SUBCOMMAND_BIT,
            deviceCode=DEVICE_CODES["M"],
            start=start,
            count=len(values),
            data=data,
        )
        self._exchange(request)

    def _exchange(self, request: bytes) -> bytes:
        with self._stateLock:
            state = self._state
            if state is None or not state.connected or state.sock is None:
                raise SlmpConnectionError("SLMP session is not open")
            if state.exchanging:
                raise SlmpConnectionError("SLMP session already has an active exchange")
            state.exchanging = True
            sock = state.sock
        try:
            data = _responseData(
                _exchangeOnSocket(
                    sock,
                    request,
                    self.endpoint,
                    responseSubheader=self.SUBHEADER_RESPONSE,
                    resetSendTimeout=True,
                )
            )
            # Validate before releasing this socket's lifecycle identity.
            command, subcommand = struct.unpack_from("<HH", request, 11)
            count = struct.unpack_from("<H", request, 19)[0]
            if command == self.COMMAND_BATCH_READ:
                if subcommand == self.SUBCOMMAND_BIT:
                    _decodeBits(data, count)
                else:
                    _validateReadDataLength(data, count * 2)
            else:
                _validateWriteData(data)
            with self._stateLock:
                if self._state is not state:
                    raise SlmpConnectionError("SLMP session was closed during exchange")
            return data
        except BaseException:
            self._closeState(state)
            raise
        finally:
            with self._stateLock:
                state.exchanging = False


def _connectSessionSocket(
    sock: socket.socket,
    address: tuple,
    cancelled: threading.Event,
    deadline: float,
) -> None:
    _connectTimeRemaining(cancelled, deadline)
    sock.setblocking(False)
    error = sock.connect_ex(address)
    pendingErrors = {
        errno.EINPROGRESS,
        errno.EWOULDBLOCK,
        errno.EALREADY,
        errno.EINTR,
    }
    if error not in {0, errno.EISCONN}:
        if error not in pendingErrors:
            raise OSError(error, "SLMP socket connect failed")
        while True:
            remaining = _connectTimeRemaining(cancelled, deadline)
            try:
                _, writable, exceptional = select.select(
                    [], [sock], [sock], min(remaining, 0.05)
                )
            except ValueError as exc:
                raise SlmpConnectionError("SLMP connect socket was closed") from exc
            if writable or exceptional:
                error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error:
                    raise OSError(error, "SLMP socket connect failed")
                break
    _connectTimeRemaining(cancelled, deadline)


def _exchangeOnSocket(
    sock: socket.socket,
    request: bytes,
    endpoint: SlmpEndpoint,
    *,
    responseSubheader: bytes,
    resetSendTimeout: bool = False,
) -> bytes:
    try:
        deadline = time.monotonic() + endpoint.responseTimeoutSec
        if resetSendTimeout:
            sock.settimeout(endpoint.responseTimeoutSec)
        sock.sendall(request)
        header = _recvExact(sock, 9, deadline=deadline)
        if header[:2] != responseSubheader:
            raise SlmpProtocolError(
                f"unexpected response subheader: {header[:2].hex()}"
            )
        expectedRoute = struct.pack(
            "<BBHB",
            endpoint.networkNo,
            endpoint.pcNo,
            endpoint.moduleIoNo,
            endpoint.moduleStationNo,
        )
        if header[2:7] != expectedRoute:
            raise SlmpProtocolError("response routing fields do not match the request")
        responseLength = struct.unpack_from("<H", header, 7)[0]
        if not 2 <= responseLength <= MAX_RESPONSE_BODY_BYTES:
            raise SlmpProtocolError(f"invalid response data length: {responseLength}")
        return _recvExact(sock, responseLength, deadline=deadline)
    except socket.timeout as exc:
        raise SlmpTimeoutError(
            f"PLC transaction timed out at {endpoint.host}:{endpoint.port}"
        ) from exc
    except OSError as exc:
        raise SlmpConnectionError(
            f"PLC communication failed at {endpoint.host}:{endpoint.port}: {exc}"
        ) from exc


def _responseData(body: bytes) -> bytes:
    endCode = struct.unpack_from("<H", body, 0)[0]
    if endCode != 0:
        raise SlmpResponseError(endCode)
    return body[2:]


def _validateReadDataLength(data: bytes, expectedSize: int) -> None:
    if len(data) != expectedSize:
        raise SlmpProtocolError(
            f"read data length mismatch: expected {expectedSize}, got {len(data)}"
        )


def _validateWriteData(data: bytes) -> None:
    if data:
        raise SlmpProtocolError(
            f"unexpected data in write response ({len(data)} bytes)"
        )


def _validateBitRange(start: int, count: int) -> None:
    _validateStartCount(start, count, MAX_BITS_PER_REQUEST)
    if start + count - 1 > 0xFFFFFF:
        raise ValueError("device range exceeds the 24-bit address space")


def _decodeBits(data: bytes, count: int) -> list[bool]:
    _validateReadDataLength(data, (count + 1) // 2)
    result: list[bool] = []
    for byte in data:
        high, low = byte >> 4, byte & 0x0F
        if high not in {0, 1} or low not in {0, 1}:
            raise SlmpProtocolError(f"invalid SLMP bit byte: 0x{byte:02X}")
        result.extend((bool(high), bool(low)))
    if count % 2:
        if result[-1]:
            raise SlmpProtocolError("non-zero padding nibble in SLMP bit response")
        result.pop()
    return result


def decodePlcWords(words: list[int], dataType: str) -> tuple[PlcScalar, ...]:
    if dataType == "uint16":
        return tuple(words)
    if dataType == "int16":
        return tuple(value - 0x10000 if value >= 0x8000 else value for value in words)
    if dataType == "bit":
        if len(words) != 1:
            raise ValueError("bit reads require exactly one word")
        return (bool(words[0] & 0x0001),)
    if dataType not in {"uint32", "int32", "float32"}:
        raise ValueError(f"unsupported PLC data type: {dataType!r}")
    if len(words) % 2:
        raise SlmpProtocolError("32-bit PLC data requires an even word count")
    decoded: list[PlcScalar] = []
    for index in range(0, len(words), 2):
        raw = words[index] | (words[index + 1] << 16)
        if dataType == "uint32":
            decoded.append(raw)
        elif dataType == "int32":
            decoded.append(raw - 0x100000000 if raw >= 0x80000000 else raw)
        else:
            value = struct.unpack("<f", struct.pack("<I", raw))[0]
            if not math.isfinite(value):
                raise SlmpProtocolError("PLC returned a non-finite float32 value")
            decoded.append(float(value))
    return tuple(decoded)


def encodePlcValues(values: list[object], dataType: str) -> list[int]:
    words: list[int] = []
    for index, value in enumerate(values):
        path = f"values[{index}]"
        if dataType == "uint16":
            integerValue = _requireIntegerValue(value, path, dataType)
            if not 0 <= integerValue <= 0xFFFF:
                raise ValueError(f"{path} is outside uint16 range")
            words.append(integerValue)
        elif dataType == "int16":
            integerValue = _requireIntegerValue(value, path, dataType)
            if not -0x8000 <= integerValue <= 0x7FFF:
                raise ValueError(f"{path} is outside int16 range")
            words.append(integerValue & 0xFFFF)
        elif dataType in {"uint32", "int32"}:
            integerValue = _requireIntegerValue(value, path, dataType)
            minimum, maximum = (
                (0, 0xFFFFFFFF) if dataType == "uint32" else (-0x80000000, 0x7FFFFFFF)
            )
            if not minimum <= integerValue <= maximum:
                raise ValueError(f"{path} is outside {dataType} range")
            raw = integerValue & 0xFFFFFFFF
            words.extend((raw & 0xFFFF, (raw >> 16) & 0xFFFF))
        elif dataType == "float32":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError(f"{path} must be a number for float32")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError(f"{path} must be finite")
            try:
                rawBytes = struct.pack("<f", numeric)
            except (OverflowError, struct.error) as exc:
                raise ValueError(f"{path} is outside float32 range") from exc
            low, high = struct.unpack("<HH", rawBytes)
            words.extend((low, high))
        else:
            raise ValueError(f"unsupported PLC write data type: {dataType!r}")
    if not 1 <= len(words) <= MAX_WORDS_PER_REQUEST:
        raise ValueError(
            f"encoded PLC write must contain 1..{MAX_WORDS_PER_REQUEST} words"
        )
    return words


def _requireIntegerValue(value: object, path: str, dataType: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{path} must be an integer for {dataType}")
    return value


T = TypeVar("T")


def runSlmpWithRetry(
    operation: Callable[[], T],
    *,
    retryCount: int,
    beforeRetry: Callable[[], None] | None = None,
) -> tuple[T, int]:
    attempts = 0
    while True:
        attempts += 1
        try:
            return operation(), attempts
        except (SlmpConnectionError, SlmpProtocolError):
            if attempts > retryCount:
                raise
            if beforeRetry is not None:
                beforeRetry()


def _deviceCode(device: str) -> int:
    normalized = device.strip().upper()
    try:
        return DEVICE_CODES[normalized]
    except KeyError as exc:
        raise ValueError("device must be D or M") from exc


def _validateDeviceRange(device: str, start: int, count: int) -> None:
    _validateStartCount(start, count, MAX_WORDS_PER_REQUEST)
    if start + plcDeviceAddressSpan(device, count) - 1 > 0xFFFFFF:
        raise ValueError("device range exceeds the 24-bit address space")


def _validateStartCount(start: int, count: int, maximum: int) -> None:
    if not isinstance(start, int) or isinstance(start, bool):
        raise TypeError("start must be an integer")
    if not isinstance(count, int) or isinstance(count, bool):
        raise TypeError("count must be an integer")
    if not 0 <= start <= 0xFFFFFF:
        raise ValueError("start must be within the 24-bit device range")
    if not 1 <= count <= maximum:
        raise ValueError(f"count must be between 1 and {maximum}")


def _packWord(value: int) -> bytes:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("PLC word must be an integer")
    if not 0 <= value <= 0xFFFF:
        raise ValueError("PLC word must be between 0 and 65535")
    return struct.pack("<H", value)


def _recvExact(sock: socket.socket, size: int, *, deadline: float) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        remainingTime = deadline - time.monotonic()
        if remainingTime <= 0.0:
            raise socket.timeout("SLMP response deadline exceeded")
        sock.settimeout(remainingTime)
        chunk = sock.recv(remaining)
        if not chunk:
            raise SlmpConnectionError(
                f"PLC closed the connection with {remaining} response bytes missing"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _safeClose(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass
