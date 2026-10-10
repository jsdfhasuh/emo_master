from __future__ import annotations

import errno
import socket
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from emo_master.plugins.builtins import _slmp as slmp
from emo_master.plugins.builtins._slmp import (
    Slmp3EClient,
    Slmp3ESession,
    SlmpConnectionError,
    SlmpEndpoint,
    SlmpProtocolError,
    SlmpResponseError,
    SlmpTimeoutError,
    decodePlcWords,
    encodePlcValues,
)


def _recvExact(client: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = client.recv(count - len(data))
        if not chunk:
            if data:
                raise AssertionError("incomplete request from client")
            return b""
        data.extend(chunk)
    return bytes(data)


def _response(
    data: bytes = b"", *, route: bytes | None = None, endCode: int = 0
) -> bytes:
    route = route if route is not None else b"\x00\xff\xff\x03\x00"
    body = struct.pack("<H", endCode) + data
    return b"\xd0\x00" + route + struct.pack("<H", len(body)) + body


def _packedBits(values: list[bool]) -> bytes:
    padded = values + ([False] if len(values) % 2 else [])
    return bytes(16 * high + low for high, low in zip(padded[::2], padded[1::2]))


class _LoopbackSlmpServer:
    def __init__(
        self,
        handler: Callable[[socket.socket, bytes], bytes | None] | None = None,
    ) -> None:
        self.handler = handler
        self.bits: dict[int, bool] = {}
        self.words: dict[int, int] = {}
        self.requests: list[bytes] = []
        self.connections = 0
        self.errors: list[BaseException] = []
        self.stopped = threading.Event()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.05)
        self.port = self.listener.getsockname()[1]
        self.client: socket.socket | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> _LoopbackSlmpServer:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
        assert not self.thread.is_alive(), "loopback peer did not stop"
        assert self.errors == []

    def stop(self) -> None:
        self.stopped.set()
        self.listener.close()
        if self.client is not None:
            try:
                self.client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.thread.join(3.0)

    def endpoint(self, **overrides: Any) -> SlmpEndpoint:
        return SlmpEndpoint(
            "127.0.0.1",
            self.port,
            **{"connectTimeoutSec": 0.5, "responseTimeoutSec": 0.5, **overrides},
        )

    def _run(self) -> None:
        try:
            while not self.stopped.is_set():
                try:
                    client, _ = self.listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self.stopped.is_set():
                        return
                    raise
                self.client = client
                self.connections += 1
                with client:
                    client.settimeout(3.0)
                    while not self.stopped.is_set():
                        try:
                            header = _recvExact(client, 9)
                        except (ConnectionResetError, ConnectionAbortedError):
                            break
                        if not header:
                            break
                        body = _recvExact(
                            client, struct.unpack_from("<H", header, 7)[0]
                        )
                        assert body
                        request = header + body
                        self.requests.append(request)
                        response = (
                            self.handler(client, request)
                            if self.handler is not None
                            else self.reply(request)
                        )
                        if response is None:
                            break
                        try:
                            client.sendall(response[:3])
                            client.sendall(response[3:9])
                            client.sendall(response[9:])
                        except (
                            BrokenPipeError,
                            ConnectionResetError,
                            ConnectionAbortedError,
                        ):
                            break
                self.client = None
        except BaseException as exc:
            if not self.stopped.is_set():
                self.errors.append(exc)

    def reply(self, request: bytes) -> bytes:
        assert request[:2] == b"\x50\x00"
        assert len(request) == 9 + struct.unpack_from("<H", request, 7)[0]
        command, unit = struct.unpack_from("<HH", request, 11)
        start = int.from_bytes(request[15:18], "little")
        device = request[18]
        count = struct.unpack_from("<H", request, 19)[0]
        payload = request[21:]
        if unit == 1:
            assert device == 0x90
            if command == 0x1401:
                assert len(payload) == (count + 1) // 2
                assert all(byte in {0, 1, 16, 17} for byte in payload)
                if count % 2:
                    assert payload[-1] & 15 == 0
                for index in range(count):
                    shift = 4 if index % 2 == 0 else 0
                    self.bits[start + index] = bool((payload[index // 2] >> shift) & 15)
                data = b""
            else:
                assert command == 0x0401 and payload == b""
                data = _packedBits(
                    [self.bits.get(start + i, False) for i in range(count)]
                )
        else:
            assert unit == 0 and device in {0xA8, 0x90}
            if command == 0x1401:
                values = struct.unpack("<" + "H" * count, payload)
                for index, value in enumerate(values):
                    if device == 0xA8:
                        self.words[start + index] = value
                    else:
                        for bit in range(16):
                            self.bits[start + index * 16 + bit] = bool(
                                value & (1 << bit)
                            )
                data = b""
            else:
                assert command == 0x0401 and payload == b""
                values = tuple(
                    self.words.get(start + index, 0)
                    if device == 0xA8
                    else sum(
                        1 << bit
                        for bit in range(16)
                        if self.bits.get(start + index * 16 + bit, False)
                    )
                    for index in range(count)
                )
                data = struct.pack("<" + "H" * count, *values)
        return _response(data, route=request[2:7])


def _worker(
    operation: Callable[[], Any],
) -> tuple[threading.Thread, list[Any], list[BaseException]]:
    results: list[Any] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(operation())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, results, errors


@pytest.mark.parametrize(
    ("device", "dataType", "values"),
    [
        ("d", "uint16", [0, 65535, 0xA55A]),
        ("D", "int16", [-32768, -1, 32767]),
        ("D", "uint32", [0x12345678, 0xFFFFFFFF]),
        ("D", "int32", [-2147483648, -123456, 2147483647]),
        ("D", "float32", [1.5, -2.25]),
        ("m", "uint16", [0x8001, 0xA55A]),
    ],
)
def testPersistentWordsMatchOriginalClientBytesAnd16And32BitValues(
    device: str, dataType: str, values: list[object]
) -> None:
    words = encodePlcValues(values, dataType)
    with _LoopbackSlmpServer() as server:
        endpoint = server.endpoint(
            networkNo=2,
            pcNo=0x11,
            moduleIoNo=0x1234,
            moduleStationNo=5,
            monitoringTimer=0x2468,
        )
        client = Slmp3EClient(endpoint)
        client.writeWords(device, 0x12345, words)
        assert client.readWords(device, 0x12345, len(words)) == words
        session = Slmp3ESession(endpoint)
        try:
            session.open()
            session.open()
            session.writeWords(device, 0x12345, words)
            assert session.readWords(device, 0x12345, len(words)) == words
            assert decodePlcWords(words, dataType) == tuple(values)
        finally:
            session.close()
        assert server.connections == 3
        assert server.requests[:2] == server.requests[2:]
        route = b"\x02\x11\x34\x12\x05"
        payload = struct.pack("<" + "H" * len(words), *words)
        suffix = b"\x45\x23\x01" + (b"\x90" if device.lower() == "m" else b"\xa8")
        suffix += struct.pack("<H", len(words))
        for request, command, data in zip(
            server.requests[:2], (0x1401, 0x0401), (payload, b"")
        ):
            body = struct.pack("<HHH", 0x2468, command, 0) + suffix + data
            assert request == b"\x50\x00" + route + struct.pack("<H", len(body)) + body


def testSessionNeedsExplicitOpenAndNeverReconnectsAfterClose() -> None:
    with _LoopbackSlmpServer() as server:
        session = Slmp3ESession(server.endpoint())
        with pytest.raises(SlmpConnectionError, match="not open"):
            session.readWords("D", 0, 1)
        session.close()
        session.close()
        assert server.connections == 0
        try:
            session.open()
            assert session.readWords("D", 0, 1) == [0]
            session.close()
            with pytest.raises(SlmpConnectionError, match="not open"):
                session.writeBits(0, [True])
            assert server.connections == 1
            session.open()
            assert session.readBits(0, 1) == [False]
            assert server.connections == 2
        finally:
            session.close()


@pytest.mark.parametrize("count", [1, 2, 3, 4, 15, 16, 17, 31, 32, 33, 959, 960])
def testNativeBitWritePreservesAdjacentBitsWithoutReadModifyWrite(count: int) -> None:
    start = 13
    values = [index % 3 == 0 for index in range(count)]
    initial = {
        address: address % 2 == 0 for address in range(start - 8, start + count + 8)
    }
    with _LoopbackSlmpServer() as server:
        server.bits.update(initial)
        session = Slmp3ESession(server.endpoint())
        try:
            session.open()
            session.writeBits(start, values)
            assert len(server.requests) == 1
            write = server.requests[0]
            assert write[9:21] == (
                b"\x00\x00\x01\x14\x01\x00\x0d\x00\x00\x90" + struct.pack("<H", count)
            )
            assert write[21:] == _packedBits(values)
            assert server.bits == {
                **initial,
                **dict(zip(range(start, start + count), values)),
            }
            assert session.readBits(start, count) == values
            assert session.readBits(start - 1, 1) == [initial[start - 1]]
            assert session.readBits(start + count, 1) == [initial[start + count]]
            assert all(request[13:15] == b"\x01\x00" for request in server.requests)
            assert [struct.unpack_from("<H", r, 11)[0] for r in server.requests] == [
                0x1401,
                0x0401,
                0x0401,
                0x0401,
            ]
            assert server.connections == 1
        finally:
            session.close()


def testBitAndWordOperationsShareTheSameConnectionAndMDeviceState() -> None:
    with _LoopbackSlmpServer() as server:
        session = Slmp3ESession(server.endpoint())
        try:
            session.open()
            session.writeWords("M", 100, [0x8001, 0x8001])
            assert session.readBits(100, 32) == [True] + [False] * 14 + [True, True] + [
                False
            ] * 14 + [True]
            session.writeBits(115, [False, False, True])
            assert session.readWords("M", 100, 2) == [1, 0x8002]
            session.writeWords("D", 9, [0x5678, 0x1234])
            assert session.readWords("D", 9, 2) == [0x5678, 0x1234]
            assert server.connections == 1
        finally:
            session.close()


@pytest.mark.parametrize(
    "start,count",
    [(0xFFFFFF, 1), (0xFFFFFE, 2), (0xFFFFF1, 15), (0x1000000 - 960, 960)],
)
def testBitBoundsUseBitCountRatherThanWordSpan(start: int, count: int) -> None:
    with _LoopbackSlmpServer() as server:
        session = Slmp3ESession(server.endpoint())
        try:
            session.open()
            values = [True] * count
            session.writeBits(start, values)
            assert session.readBits(start, count) == values
            assert int.from_bytes(server.requests[0][15:18], "little") == start
        finally:
            session.close()


@pytest.mark.parametrize(
    "start,count,error",
    [
        (-1, 1, ValueError),
        (0x1000000, 1, ValueError),
        (True, 1, TypeError),
        (1.5, 1, TypeError),
        ("0", 1, TypeError),
        (None, 1, TypeError),
        (0, 0, ValueError),
        (0, -1, ValueError),
        (0, 961, ValueError),
        (0, True, TypeError),
        (0, False, TypeError),
        (0, 1.5, TypeError),
        (0, "1", TypeError),
        (0, None, TypeError),
        (0xFFFFFF, 2, ValueError),
    ],
)
def testInvalidBitRangesDoNotSendOrRetireTheConnection(
    start: Any, count: Any, error: type[Exception]
) -> None:
    with _LoopbackSlmpServer() as server:
        session = Slmp3ESession(server.endpoint())
        try:
            session.open()
            with pytest.raises(error):
                session.readBits(start, count)
            if isinstance(count, int) and not isinstance(count, bool):
                with pytest.raises(error):
                    session.writeBits(start, [False] * max(0, count))
            assert server.requests == []
            assert session.readBits(0, 1) == [False]
            assert server.connections == 1
        finally:
            session.close()


@pytest.mark.parametrize(
    "values",
    [None, True, b"\x10", (True,), [0], [1], [None], ["true"], [True, False, 1]],
)
def testBitPayloadRequiresAListOfActualBools(values: Any) -> None:
    with _LoopbackSlmpServer() as server:
        session = Slmp3ESession(server.endpoint())
        try:
            session.open()
            with pytest.raises(TypeError):
                session.writeBits(0, values)
            assert server.requests == []
            session.writeBits(0, [True, False])
            assert session.readBits(0, 2) == [True, False]
        finally:
            session.close()


def _badResponse(case: str) -> tuple[bytes | None, type[Exception], str]:
    valid = _response(b"\x07\x00")
    if case == "subheader":
        return b"\xd4\x00" + valid[2:], SlmpProtocolError, "subheader"
    if case.startswith("route"):
        broken = bytearray(valid)
        broken[int(case[-1])] ^= 1
        return bytes(broken), SlmpProtocolError, "routing"
    if case.startswith("length"):
        length = int(case.split("-")[1])
        return valid[:7] + struct.pack("<H", length), SlmpProtocolError, "data length"
    if case == "endcode":
        return _response(endCode=0xC051), SlmpResponseError, "0xC051"
    if case == "short-data":
        return _response(b"\x07"), SlmpProtocolError, "read data length"
    if case == "long-data":
        return _response(b"\x07\x00\x00"), SlmpProtocolError, "read data length"
    if case == "partial-header":
        return valid[:8], SlmpConnectionError, "bytes missing"
    if case == "partial-body":
        return valid[:-1], SlmpConnectionError, "bytes missing"
    assert case == "eof"
    return None, SlmpConnectionError, "bytes missing"


@pytest.mark.parametrize("transport", [Slmp3EClient, Slmp3ESession])
@pytest.mark.parametrize(
    "case",
    [
        "subheader",
        "route2",
        "route3",
        "route4",
        "route5",
        "route6",
        "length-0",
        "length-1",
        "length-1923",
        "length-65535",
        "endcode",
        "short-data",
        "long-data",
        "partial-header",
        "partial-body",
        "eof",
    ],
)
def testBothTransportsShareStrictResponseValidationAndSessionErrorsRetireSocket(
    transport: type[Slmp3EClient], case: str
) -> None:
    malformed, error, message = _badResponse(case)

    def handler(client: socket.socket, request: bytes) -> bytes | None:
        if len(server.requests) == 1:
            if case.startswith("partial"):
                assert malformed is not None
                client.sendall(malformed)
                return None
            return malformed
        return _response(b"\x07\x00", route=request[2:7])

    with _LoopbackSlmpServer(handler) as server:
        client = transport(server.endpoint())
        try:
            if isinstance(client, Slmp3ESession):
                client.open()
            with pytest.raises(error, match=message) as failure:
                client.readWords("D", 0, 1)
            if isinstance(failure.value, SlmpResponseError):
                assert failure.value.endCode == 0xC051
            if isinstance(client, Slmp3ESession):
                with pytest.raises(SlmpConnectionError, match="not open"):
                    client.readBits(0, 1)
                assert len(server.requests) == 1 and server.connections == 1
                client.open()
            assert client.readWords("D", 0, 1) == [7]
            assert server.connections == 2
        finally:
            if isinstance(client, Slmp3ESession):
                client.close()


@pytest.mark.parametrize("byte", [n << 4 for n in range(2, 16)] + list(range(2, 16)))
def testReadBitsRejectsEveryInvalidHighOrLowNibble(byte: int) -> None:
    with _LoopbackSlmpServer(
        lambda _client, _request: _response(bytes([byte]))
    ) as server:
        session = Slmp3ESession(server.endpoint())
        try:
            session.open()
            with pytest.raises(SlmpProtocolError, match="bit byte"):
                session.readBits(0, 2)
            with pytest.raises(SlmpConnectionError, match="not open"):
                session.readBits(0, 2)
            assert server.connections == 1 and len(server.requests) == 1
        finally:
            session.close()


@pytest.mark.parametrize(
    "count,data,message",
    [
        (1, b"\x11", "padding"),
        (3, b"\x10\x01", "padding"),
        (1, b"", "data length"),
        (2, b"\x00\x00", "data length"),
        (3, b"\x10", "data length"),
        (3, b"\x00\x00\x00", "data length"),
    ],
)
def testReadBitsRejectsWrongLengthAndNonzeroOddPadding(
    count: int, data: bytes, message: str
) -> None:
    with _LoopbackSlmpServer(lambda _client, _request: _response(data)) as server:
        session = Slmp3ESession(server.endpoint())
        try:
            session.open()
            with pytest.raises(SlmpProtocolError, match=message):
                session.readBits(0, count)
            with pytest.raises(SlmpConnectionError, match="not open"):
                session.readBits(0, count)
        finally:
            session.close()


@pytest.mark.parametrize("unit", ["word", "bit"])
@pytest.mark.parametrize(
    "response,error",
    [
        (_response(b"\x00"), SlmpProtocolError),
        (_response(endCode=0xC051), SlmpResponseError),
    ],
)
def testWriteResponseErrorsRetireBothWordAndBitSessions(
    unit: str, response: bytes, error: type[Exception]
) -> None:
    with _LoopbackSlmpServer(lambda _client, _request: response) as server:
        session = Slmp3ESession(server.endpoint())
        try:
            session.open()
            with pytest.raises(error):
                if unit == "word":
                    session.writeWords("D", 0, [1])
                else:
                    session.writeBits(0, [True])
            with pytest.raises(SlmpConnectionError, match="not open"):
                session.readWords("D", 0, 1)
        finally:
            session.close()


def testSessionTimeoutIsATotalResponseDeadlineAndDoesNotRetry() -> None:
    def handler(client: socket.socket, request: bytes) -> None:
        for byte in _response(b"\x07\x00", route=request[2:7]):
            try:
                client.sendall(bytes([byte]))
            except OSError:
                break
            if server.stopped.wait(0.02):
                break

    with _LoopbackSlmpServer(handler) as server:
        session = Slmp3ESession(server.endpoint(responseTimeoutSec=0.06))
        try:
            session.open()
            started = time.monotonic()
            with pytest.raises(SlmpTimeoutError):
                session.readWords("D", 0, 1)
            assert time.monotonic() - started < 0.6
            with pytest.raises(SlmpConnectionError, match="not open"):
                session.readWords("D", 0, 1)
            assert server.connections == 1 and len(server.requests) == 1
        finally:
            session.close()


@pytest.mark.parametrize(
    "operation", ["readWords", "writeWords", "readBits", "writeBits"]
)
def testCloseAbortsAnInFlightResponseWithoutWaitingForTimeout(operation: str) -> None:
    received = threading.Event()

    def handler(client: socket.socket, _request: bytes) -> None:
        received.set()
        assert client.recv(1) == b""

    with _LoopbackSlmpServer(handler) as server:
        session = Slmp3ESession(server.endpoint(responseTimeoutSec=10.0))
        session.open()
        operations = {
            "readWords": lambda: session.readWords("D", 0, 1),
            "writeWords": lambda: session.writeWords("D", 0, [1]),
            "readBits": lambda: session.readBits(0, 1),
            "writeBits": lambda: session.writeBits(0, [True]),
        }
        thread, results, errors = _worker(operations[operation])
        try:
            assert received.wait(2.0)
            session.close()
            session.close()
            thread.join(0.8)
            assert not thread.is_alive()
            assert results == [] and len(errors) == 1
            assert isinstance(errors[0], SlmpConnectionError)
            assert not isinstance(errors[0], SlmpTimeoutError)
            assert server.connections == 1 and len(server.requests) == 1
        finally:
            session.close()
            thread.join(2.0)


def testAConcurrentExchangeIsRejectedWithoutDisruptingTheActiveOne() -> None:
    received, release = threading.Event(), threading.Event()

    def handler(_client: socket.socket, request: bytes) -> bytes:
        if len(server.requests) == 1:
            received.set()
            assert release.wait(2.0)
        return server.reply(request)

    with _LoopbackSlmpServer(handler) as server:
        session = Slmp3ESession(server.endpoint(responseTimeoutSec=3.0))
        session.open()
        thread, results, errors = _worker(lambda: session.readBits(0, 1))
        try:
            assert received.wait(2.0)
            with pytest.raises(SlmpConnectionError, match="active exchange"):
                session.readWords("D", 0, 1)
            release.set()
            thread.join(2.0)
            assert not thread.is_alive() and errors == [] and results == [[False]]
            assert session.readWords("D", 0, 1) == [0]
            assert server.connections == 1
        finally:
            release.set()
            session.close()
            thread.join(2.0)


def testCloseCancelsAPendingConnectAndConnectTimeoutRetiresState(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selecting = threading.Event()

    def pendingSelect(
        readers: list, writers: list, exceptional: list, timeout: float
    ) -> tuple:
        selecting.set()
        threading.Event().wait(timeout)
        return [], [], []

    with _LoopbackSlmpServer() as server:

        class PendingSocket(socket.socket):
            def connect_ex(self, address: Any) -> int:
                result = super().connect_ex(address)
                return errno.EINPROGRESS if result == 0 else result

        monkeypatch.setattr(slmp.socket, "socket", PendingSocket)
        monkeypatch.setattr(slmp.select, "select", pendingSelect)
        session = Slmp3ESession(server.endpoint(connectTimeoutSec=10.0))
        thread, results, errors = _worker(session.open)
        try:
            assert selecting.wait(2.0)
            with pytest.raises(SlmpConnectionError, match="already connecting"):
                session.open()
            session.close()
            thread.join(0.8)
            assert not thread.is_alive() and results == [] and len(errors) == 1
            assert isinstance(errors[0], SlmpConnectionError)
            assert not isinstance(errors[0], SlmpTimeoutError)
            with pytest.raises(SlmpConnectionError, match="not open"):
                session.readBits(0, 1)
        finally:
            session.close()
            thread.join(2.0)
        timed = Slmp3ESession(server.endpoint(connectTimeoutSec=0.05))
        with pytest.raises(SlmpTimeoutError, match="connect timed out"):
            timed.open()
        with pytest.raises(SlmpConnectionError, match="not open"):
            timed.readBits(0, 1)
        timed.close()


def testClosedConnectCompletionCannotPublishOverANewConnection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected, release = threading.Event(), threading.Event()
    originalConnect = slmp._connectSessionSocket
    first = True

    def gatedConnect(*args: Any, **kwargs: Any) -> None:
        nonlocal first
        old = first
        first = False
        originalConnect(*args, **kwargs)
        if old:
            connected.set()
            assert release.wait(3.0)

    with _LoopbackSlmpServer() as server:
        monkeypatch.setattr(slmp, "_connectSessionSocket", gatedConnect)
        session = Slmp3ESession(server.endpoint())
        thread, results, errors = _worker(session.open)
        try:
            assert connected.wait(2.0)
            session.close()
            session.open()
            assert session.readBits(0, 1) == [False]
            release.set()
            thread.join(2.0)
            assert not thread.is_alive() and results == [] and len(errors) == 1
            assert isinstance(errors[0], SlmpConnectionError)
            assert session.readWords("D", 0, 1) == [0]
            assert server.connections == 2
        finally:
            release.set()
            session.close()
            thread.join(2.0)


@pytest.mark.parametrize("lateError", [False, True])
def testOldExchangeCompletionOrErrorCannotCloseANewConnection(
    monkeypatch: pytest.MonkeyPatch, lateError: bool
) -> None:
    completed, release = threading.Event(), threading.Event()
    originalExchange = slmp._exchangeOnSocket
    first = True

    def gatedExchange(*args: Any, **kwargs: Any) -> bytes:
        nonlocal first
        old = first
        first = False
        response = originalExchange(*args, **kwargs)
        if old:
            completed.set()
            assert release.wait(3.0)
            if lateError:
                raise SlmpProtocolError("old response failed late")
        return response

    with _LoopbackSlmpServer() as server:
        monkeypatch.setattr(slmp, "_exchangeOnSocket", gatedExchange)
        session = Slmp3ESession(server.endpoint(responseTimeoutSec=3.0))
        session.open()
        thread, results, errors = _worker(lambda: session.readBits(0, 1))
        try:
            assert completed.wait(2.0)
            session.close()
            session.open()
            assert session.readWords("D", 0, 1) == [0]
            release.set()
            thread.join(2.0)
            assert not thread.is_alive() and results == [] and len(errors) == 1
            assert isinstance(
                errors[0], SlmpProtocolError if lateError else SlmpConnectionError
            )
            assert session.readBits(0, 1) == [False]
            assert server.connections == 2
        finally:
            release.set()
            session.close()
            thread.join(2.0)


def testEachPersistentExchangeResetsItsSendTimeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sendTimeouts: list[float | None] = []

    def handler(_client: socket.socket, request: bytes) -> bytes:
        if len(server.requests) == 1:
            assert not server.stopped.wait(0.12)
        return server.reply(request)

    with _LoopbackSlmpServer(handler) as server:

        class RecordingSocket(socket.socket):
            def sendall(self, data: Any, flags: int = 0) -> None:
                if self.getpeername()[1] == server.port:
                    sendTimeouts.append(self.gettimeout())
                    assert self.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
                super().sendall(data, flags)

        monkeypatch.setattr(slmp.socket, "socket", RecordingSocket)
        session = Slmp3ESession(server.endpoint(responseTimeoutSec=0.3))
        try:
            session.open()
            assert session.readWords("D", 0, 1) == [0]
            assert session.readBits(0, 1) == [False]
            assert sendTimeouts == [0.3, 0.3]
            assert server.connections == 1
        finally:
            session.close()


@pytest.mark.parametrize("transport", [Slmp3EClient, Slmp3ESession])
def testSendAndReceiveShareOneTransactionDeadline(
    monkeypatch: pytest.MonkeyPatch, transport: type[Slmp3EClient]
) -> None:
    def handler(_client: socket.socket, request: bytes) -> bytes:
        server.stopped.wait(0.07)
        return server.reply(request)

    with _LoopbackSlmpServer(handler) as server:

        class DelayedSendSocket(socket.socket):
            def sendall(self, data: Any, flags: int = 0) -> None:
                if self.getpeername()[1] == server.port:
                    time.sleep(0.07)
                super().sendall(data, flags)

        monkeypatch.setattr(slmp.socket, "socket", DelayedSendSocket)
        client = transport(server.endpoint(responseTimeoutSec=0.1))
        try:
            if isinstance(client, Slmp3ESession):
                client.open()
            with pytest.raises(SlmpTimeoutError):
                client.readWords("D", 0, 1)
            assert len(server.requests) == 1
        finally:
            if isinstance(client, Slmp3ESession):
                client.close()


def testCloseAbortsSendUsingSocketShutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    sending = threading.Event()
    interrupted = threading.Event()
    with _LoopbackSlmpServer() as server:

        class BlockedSendSocket(socket.socket):
            def sendall(self, data: Any, flags: int = 0) -> None:
                if self.getpeername()[1] == server.port:
                    sending.set()
                    assert interrupted.wait(3.0)
                super().sendall(data, flags)

            def shutdown(self, how: int) -> None:
                try:
                    super().shutdown(how)
                finally:
                    interrupted.set()

        monkeypatch.setattr(slmp.socket, "socket", BlockedSendSocket)
        session = Slmp3ESession(server.endpoint(responseTimeoutSec=10.0))
        session.open()
        thread, results, errors = _worker(lambda: session.writeBits(0, [True]))
        try:
            assert sending.wait(2.0)
            session.close()
            thread.join(0.8)
            assert not thread.is_alive() and results == [] and len(errors) == 1
            assert isinstance(errors[0], SlmpConnectionError)
            assert not isinstance(errors[0], SlmpTimeoutError)
            assert server.requests == []
        finally:
            session.close()
            interrupted.set()
            thread.join(2.0)


def testCloseBeforeSocketPublicationCannotReviveTheCancelledOpen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved, release = threading.Event(), threading.Event()
    originalResolve = socket.getaddrinfo
    first = True

    def gatedResolve(*args: Any, **kwargs: Any) -> list:
        nonlocal first
        if kwargs.get("flags", 0) & socket.AI_NUMERICHOST:
            return originalResolve(*args, **kwargs)
        old = first
        first = False
        addresses = originalResolve("127.0.0.1", args[1], type=socket.SOCK_STREAM)
        if old:
            resolved.set()
            assert release.wait(3.0)
        return addresses

    with _LoopbackSlmpServer() as server:
        monkeypatch.setattr(slmp.socket, "getaddrinfo", gatedResolve)
        session = Slmp3ESession(replace(server.endpoint(), host="slmp.test.invalid"))
        thread, results, errors = _worker(session.open)
        try:
            assert resolved.wait(2.0)
            session.close()
            thread.join(0.8)
            assert not thread.is_alive() and results == [] and len(errors) == 1
            assert isinstance(errors[0], SlmpConnectionError)
            session.open()
            assert session.readBits(0, 1) == [False]
            release.set()
            thread.join(2.0)
            assert not thread.is_alive() and results == [] and len(errors) == 1
            assert isinstance(errors[0], SlmpConnectionError)
            assert session.readWords("D", 0, 1) == [0]
            assert server.connections == 1
        finally:
            release.set()
            session.close()
            thread.join(2.0)


def testRefusedConnectRetiresTheSessionWithoutAutomaticRetry() -> None:
    with _LoopbackSlmpServer() as server:
        session = Slmp3ESession(server.endpoint())
        server.stop()
        with pytest.raises(
            SlmpConnectionError, match="cannot connect|connect timed out"
        ):
            session.open()
        with pytest.raises(SlmpConnectionError, match="not open"):
            session.readBits(0, 1)
        session.close()
        assert server.requests == [] and server.connections == 0


@pytest.mark.parametrize(
    "case", ["subheader", "route2", "length-65535", "endcode", "partial-body", "eof"]
)
def testNativeBitReadsAlsoRejectFramingEndCodeAndTransportFailures(case: str) -> None:
    malformed, error, message = _badResponse(case)

    def handler(client: socket.socket, _request: bytes) -> bytes | None:
        if case == "partial-body":
            assert malformed is not None
            client.sendall(malformed)
            return None
        return malformed

    with _LoopbackSlmpServer(handler) as server:
        session = Slmp3ESession(server.endpoint())
        try:
            session.open()
            with pytest.raises(error, match=message):
                session.readBits(0, 2)
            with pytest.raises(SlmpConnectionError, match="not open"):
                session.readBits(0, 2)
        finally:
            session.close()


@pytest.mark.parametrize(
    "device,start", [("D", 0xFFFFFF - 959), ("M", 0x1000000 - 960 * 16)]
)
def testWordMethodsKeepTheOriginalMaximum960WordsAndAddressSpan(
    device: str, start: int
) -> None:
    with _LoopbackSlmpServer() as server:
        session = Slmp3ESession(server.endpoint())
        try:
            session.open()
            words = [index * 67 % 65536 for index in range(960)]
            session.writeWords(device, start, words)
            assert session.readWords(device, start, 960) == words
            with pytest.raises(ValueError, match="24-bit"):
                session.readWords(device, start + 1, 960)
            with pytest.raises(ValueError, match="960"):
                session.readWords(device, 0, 961)
            with pytest.raises(TypeError, match="integer"):
                session.writeWords(device, 0, [True])
            assert session.readWords(device, start, 1) == words[:1]
            assert server.connections == 1
        finally:
            session.close()


class _BlockedDns:
    def __init__(self) -> None:
        self.original = socket.getaddrinfo
        self.entered = threading.Event()
        self.bothEntered = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.calls = 0
        self.active = 0
        self.maximumActive = 0
        self.threadNames: set[str] = set()

    def resolve(self, host: str, port: int, *args: Any, **kwargs: Any) -> list:
        if kwargs.get("flags", 0) & socket.AI_NUMERICHOST:
            return self.original(host, port, *args, **kwargs)
        with self.lock:
            self.calls += 1
            self.active += 1
            self.maximumActive = max(self.maximumActive, self.active)
            self.threadNames.add(threading.current_thread().name)
            self.entered.set()
            if self.active == 2:
                self.bothEntered.set()
        try:
            assert self.release.wait(3.0), "test did not release the native DNS call"
            return self.original("127.0.0.1", port, type=socket.SOCK_STREAM)
        finally:
            with self.lock:
                self.active -= 1

    def finish(self) -> None:
        self.release.set()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with self.lock:
                active = self.active
            if active == 0 and slmp._SLMP_RESOLVER._jobs.unfinished_tasks == 0:
                return
            time.sleep(0.01)
        raise AssertionError("resolver workers did not drain released test jobs")


@pytest.fixture
def blockedDns(monkeypatch: pytest.MonkeyPatch):
    dns = _BlockedDns()
    monkeypatch.setattr(slmp.socket, "getaddrinfo", dns.resolve)
    try:
        yield dns
    finally:
        dns.finish()


def testCloseAbortsAResolverWaitWhileTheNativeCallIsStillBlocked(
    blockedDns: _BlockedDns,
) -> None:
    with _LoopbackSlmpServer() as server:
        endpoint = replace(
            server.endpoint(connectTimeoutSec=10.0), host="slmp.test.invalid"
        )
        session = Slmp3ESession(endpoint)
        thread, results, errors = _worker(session.open)
        try:
            assert blockedDns.entered.wait(2.0)
            started = time.monotonic()
            session.close()
            thread.join(0.8)
            assert time.monotonic() - started < 0.8
            assert not thread.is_alive() and results == [] and len(errors) == 1
            assert isinstance(errors[0], SlmpConnectionError)
            assert not isinstance(errors[0], SlmpTimeoutError)
            assert blockedDns.active == 1 and not blockedDns.release.is_set()
            with pytest.raises(SlmpConnectionError, match="not open"):
                session.readBits(0, 1)
            blockedDns.finish()
            assert server.connections == 0 and server.requests == []
        finally:
            session.close()
            blockedDns.finish()
            thread.join(2.0)


def testResolutionTimesOutWithinTheConnectDeadlineAndLateResultsCannotConnect(
    blockedDns: _BlockedDns,
) -> None:
    with _LoopbackSlmpServer() as server:
        endpoint = replace(
            server.endpoint(connectTimeoutSec=0.08), host="slmp.test.invalid"
        )
        session = Slmp3ESession(endpoint)
        thread, results, errors = _worker(session.open)
        try:
            assert blockedDns.entered.wait(2.0)
            thread.join(0.8)
            assert not thread.is_alive() and results == [] and len(errors) == 1
            assert isinstance(errors[0], SlmpTimeoutError)
            assert blockedDns.active == 1 and not blockedDns.release.is_set()
            with pytest.raises(SlmpConnectionError, match="not open"):
                session.readBits(0, 1)
            blockedDns.finish()
            assert server.connections == 0 and server.requests == []
        finally:
            session.close()
            blockedDns.finish()
            thread.join(2.0)


def testBlockedResolversAndRetryQueueStayGloballyCappedAndNumericIpsStillWork(
    blockedDns: _BlockedDns,
) -> None:
    with _LoopbackSlmpServer() as server:
        endpoint = replace(
            server.endpoint(connectTimeoutSec=0.1), host="slmp.test.invalid"
        )
        sessions = [Slmp3ESession(endpoint) for _ in range(8)]
        workers = [_worker(session.open) for session in sessions]
        numeric = Slmp3ESession(server.endpoint())
        try:
            assert blockedDns.bothEntered.wait(2.0)
            for thread, results, errors in workers:
                thread.join(0.8)
                assert not thread.is_alive() and results == [] and len(errors) == 1
                assert isinstance(errors[0], SlmpTimeoutError)
            for _ in range(4):
                with pytest.raises(SlmpTimeoutError):
                    sessions[0].open()
            assert blockedDns.calls == 2 and blockedDns.maximumActive == 2
            assert len(blockedDns.threadNames) == 2
            assert len(slmp._SLMP_RESOLVER._workers) == 2
            assert all(worker.daemon for worker in slmp._SLMP_RESOLVER._workers)
            assert slmp._SLMP_RESOLVER._jobs.maxsize == 2
            assert slmp._SLMP_RESOLVER._jobs.qsize() <= 2
            numeric.open()
            assert numeric.readBits(0, 1) == [False]
            numeric.close()
            assert blockedDns.calls == 2
            blockedDns.finish()
            assert blockedDns.calls == 2
            assert server.connections == 1 and len(server.requests) == 1
        finally:
            numeric.close()
            for session in sessions:
                session.close()
            blockedDns.finish()
            for thread, _, _ in workers:
                thread.join(2.0)


def testNumericIpUsesAiNumericHostWithoutSubmittingAResolverJob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    originalResolve = socket.getaddrinfo
    calls: list[tuple[int, int]] = []

    def numericResolve(*args: Any, **kwargs: Any) -> list:
        calls.append((kwargs.get("flags", 0), threading.get_ident()))
        return originalResolve(*args, **kwargs)

    def unexpectedResolve(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("numeric IP was submitted to the DNS worker pool")

    with _LoopbackSlmpServer() as server:
        workerCount = len(slmp._SLMP_RESOLVER._workers)
        monkeypatch.setattr(slmp.socket, "getaddrinfo", numericResolve)
        monkeypatch.setattr(slmp._SLMP_RESOLVER, "resolve", unexpectedResolve)
        session = Slmp3ESession(server.endpoint())
        try:
            session.open()
            assert session.readBits(0, 1) == [False]
            assert calls == [(socket.AI_NUMERICHOST, threading.get_ident())]
            assert len(slmp._SLMP_RESOLVER._workers) == workerCount
        finally:
            session.close()


def testSocketConnectReceivesOnlyTheTimeRemainingAfterResolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    originalResolve = socket.getaddrinfo
    originalConnect = slmp._connectSessionSocket
    budgets: list[float] = []

    def delayedResolve(host: str, port: int, *args: Any, **kwargs: Any) -> list:
        if kwargs.get("flags", 0) & socket.AI_NUMERICHOST:
            return originalResolve(host, port, *args, **kwargs)
        time.sleep(0.08)
        return originalResolve("127.0.0.1", port, type=socket.SOCK_STREAM)

    def recordBudget(
        sock: socket.socket, address: tuple, cancelled: threading.Event, deadline: float
    ) -> None:
        budgets.append(deadline - time.monotonic())
        originalConnect(sock, address, cancelled, deadline)

    def pendingSelect(
        _readers: list, _writers: list, _exceptional: list, timeout: float
    ) -> tuple:
        threading.Event().wait(timeout)
        return [], [], []

    class PendingSocket(socket.socket):
        def connect_ex(self, address: Any) -> int:
            result = super().connect_ex(address)
            return errno.EINPROGRESS if result == 0 else result

    with _LoopbackSlmpServer() as server:
        monkeypatch.setattr(slmp.socket, "getaddrinfo", delayedResolve)
        monkeypatch.setattr(slmp.socket, "socket", PendingSocket)
        monkeypatch.setattr(slmp.select, "select", pendingSelect)
        monkeypatch.setattr(slmp, "_connectSessionSocket", recordBudget)
        endpoint = replace(
            server.endpoint(connectTimeoutSec=0.15), host="slmp.test.invalid"
        )
        session = Slmp3ESession(endpoint)
        started = time.monotonic()
        try:
            with pytest.raises(SlmpTimeoutError):
                session.open()
            assert time.monotonic() - started < 0.8
            assert len(budgets) == 1 and 0 < budgets[0] < 0.11
            assert server.requests == []
            with pytest.raises(SlmpConnectionError, match="not open"):
                session.readBits(0, 1)
        finally:
            session.close()
