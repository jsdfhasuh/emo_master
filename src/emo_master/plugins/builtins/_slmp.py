from __future__ import annotations

import math
import socket
import struct
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from emo_master.core.contracts.communication import PlcScalar, plcDeviceAddressSpan


DEVICE_CODES = {"D": 0xA8, "M": 0x90}
MAX_WORDS_PER_REQUEST = 960
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
            if not math.isfinite(float(timeoutValue)) or not 0.001 <= float(
                timeoutValue
            ) <= 60.0:
                raise ValueError(f"{name} must be between 0.001 and 60 seconds")


class Slmp3EClient:
    SUBHEADER_REQUEST = b"\x50\x00"
    SUBHEADER_RESPONSE = b"\xD0\x00"
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
        expectedSize = count * 2
        if len(data) != expectedSize:
            raise SlmpProtocolError(
                f"read data length mismatch: expected {expectedSize}, got {len(data)}"
            )
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
        if responseData:
            raise SlmpProtocolError(
                f"unexpected data in write response ({len(responseData)} bytes)"
            )

    def _exchange(self, request: bytes) -> bytes:
        sock = self._openSocket()
        try:
            try:
                deadline = time.monotonic() + self.endpoint.responseTimeoutSec
                sock.sendall(request)
                header = _recvExact(sock, 9, deadline=deadline)
                if header[:2] != self.SUBHEADER_RESPONSE:
                    raise SlmpProtocolError(
                        f"unexpected response subheader: {header[:2].hex()}"
                    )
                expectedRoute = struct.pack(
                    "<BBHB",
                    self.endpoint.networkNo,
                    self.endpoint.pcNo,
                    self.endpoint.moduleIoNo,
                    self.endpoint.moduleStationNo,
                )
                if header[2:7] != expectedRoute:
                    raise SlmpProtocolError(
                        "response routing fields do not match the request"
                    )
                responseLength = struct.unpack_from("<H", header, 7)[0]
                if not 2 <= responseLength <= MAX_RESPONSE_BODY_BYTES:
                    raise SlmpProtocolError(
                        f"invalid response data length: {responseLength}"
                    )
                body = _recvExact(sock, responseLength, deadline=deadline)
            except socket.timeout as exc:
                raise SlmpTimeoutError(
                    f"PLC transaction timed out at {self.endpoint.host}:{self.endpoint.port}"
                ) from exc
            except OSError as exc:
                raise SlmpConnectionError(
                    f"PLC communication failed at "
                    f"{self.endpoint.host}:{self.endpoint.port}: {exc}"
                ) from exc
        finally:
            _safeClose(sock)
        endCode = struct.unpack_from("<H", body, 0)[0]
        if endCode != 0:
            raise SlmpResponseError(endCode)
        return body[2:]

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
    ) -> bytes:
        requestData = (
            struct.pack(
                "<HHH",
                self.endpoint.monitoringTimer,
                command,
                self.SUBCOMMAND_WORD,
            )
            + start.to_bytes(3, byteorder="little", signed=False)
            + bytes([deviceCode])
            + struct.pack("<H", count)
            + data
        )
        return self.SUBHEADER_REQUEST + struct.pack(
            "<BBHBH",
            self.endpoint.networkNo,
            self.endpoint.pcNo,
            self.endpoint.moduleIoNo,
            self.endpoint.moduleStationNo,
            len(requestData),
        ) + requestData


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
                (0, 0xFFFFFFFF)
                if dataType == "uint32"
                else (-0x80000000, 0x7FFFFFFF)
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
    if not isinstance(start, int) or isinstance(start, bool):
        raise TypeError("start must be an integer")
    if not isinstance(count, int) or isinstance(count, bool):
        raise TypeError("count must be an integer")
    if not 0 <= start <= 0xFFFFFF:
        raise ValueError("start must be within the 24-bit device range")
    if not 1 <= count <= MAX_WORDS_PER_REQUEST:
        raise ValueError(f"count must be between 1 and {MAX_WORDS_PER_REQUEST}")
    if start + plcDeviceAddressSpan(device, count) - 1 > 0xFFFFFF:
        raise ValueError("device range exceeds the 24-bit address space")


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
