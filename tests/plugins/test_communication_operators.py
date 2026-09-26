from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from emo_master.core.contracts.communication import (
    PlcValueCollection,
    PlcWriteReceipt,
    TcpMessage,
)
from emo_master.plugins.builtins._communication_operators import (
    PLC_WRITE_PARAM_SCHEMA,
    PlcSlmpReadOperator,
    PlcSlmpWriteOperator,
    TcpClientOperator,
    TcpReceiveOnceOperator,
)
from emo_master.plugins.builtins import _tcp_transport as tcp_transport_module
from emo_master.plugins.builtins._tcp_transport import (
    TcpFrameError,
    TcpTimeoutError,
    receiveFramed,
)


class _ScriptedSlmpServer:
    def __init__(self, responses: list[bytes | None]) -> None:
        self.responses = responses
        self.requests: list[bytes] = []
        self.errors: list[BaseException] = []
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(len(responses))
        self.server.settimeout(2.0)
        self.port = int(self.server.getsockname()[1])
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> _ScriptedSlmpServer:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.thread.join(timeout=3.0)
        self.server.close()
        assert not self.thread.is_alive()
        assert self.errors == []

    def _run(self) -> None:
        try:
            with self.server:
                for response in self.responses:
                    client, _ = self.server.accept()
                    with client:
                        client.settimeout(1.0)
                        header = _recvExact(client, 9)
                        bodyLength = struct.unpack_from("<H", header, 7)[0]
                        request = header + _recvExact(client, bodyLength)
                        self.requests.append(request)
                        if response is not None:
                            midpoint = max(1, len(response) // 2)
                            client.sendall(response[:midpoint])
                            client.sendall(response[midpoint:])
        except BaseException as exc:  # pragma: no cover - reported by context manager
            self.errors.append(exc)


def _slmpResponse(data: bytes = b"", *, endCode: int = 0) -> bytes:
    body = struct.pack("<H", endCode) + data
    return b"\xD0\x00\x00\xFF\xFF\x03\x00" + struct.pack("<H", len(body)) + body


def _plcParams(port: int, **overrides: object) -> dict[str, object]:
    return {
        "host": "127.0.0.1",
        "port": port,
        "connectTimeoutMs": 500,
        "responseTimeoutMs": 500,
        "retryCount": 0,
        **overrides,
    }


def testSlmpReadDecodesInt32AndBuildsTheExpected3ERequest() -> None:
    raw = struct.pack("<HHHH", 0xFFFE, 0xFFFF, 0x5678, 0x1234)
    with _ScriptedSlmpServer([_slmpResponse(raw)]) as server:
        result = PlcSlmpReadOperator().executeNode(
            {},
            _plcParams(
                server.port,
                device="D",
                startAddress=798,
                dataType="int32",
                count=2,
            ),
            {},
        )
    assert result["status"] == "ok"
    values = PlcValueCollection.fromPayload(result["outputs"]["values"])
    assert values.values == (-2, 0x12345678)
    assert values.wordCount == 4
    assert result["outputs"]["numberValue"] == -2
    assert "booleanValue" not in result["outputs"]
    request = server.requests[0]
    assert request[:2] == b"\x50\x00"
    assert struct.unpack_from("<H", request, 11)[0] == 0x0401
    assert int.from_bytes(request[15:18], "little") == 798
    assert request[18] == 0xA8
    assert struct.unpack_from("<H", request, 19)[0] == 4


def testSlmpReadRetriesTransportFailureButNotPlcEndCode() -> None:
    with _ScriptedSlmpServer([None, _slmpResponse(struct.pack("<H", 7))]) as server:
        result = PlcSlmpReadOperator().executeNode(
            {},
            _plcParams(server.port, retryCount=1),
            {},
        )
    assert result["status"] == "ok"
    assert result["metrics"]["attempts"] == 2

    with _ScriptedSlmpServer([_slmpResponse(endCode=0xC051)]) as rejected:
        failure = PlcSlmpReadOperator().executeNode(
            {},
            _plcParams(rejected.port, retryCount=1),
            {},
        )
    assert failure["status"] == "error"
    assert failure["error"]["code"] == "E_PLC_RESPONSE"
    assert len(rejected.requests) == 1


def testSlmpWriteEncodesFloat32AndReturnsStrictReceipt() -> None:
    with _ScriptedSlmpServer([_slmpResponse()]) as server:
        result = PlcSlmpWriteOperator().executeNode(
            {"values": [1.5, -2.25]},
            _plcParams(
                server.port,
                device="D",
                startAddress=100,
                dataType="float32",
            ),
            {},
        )
    assert result["status"] == "ok"
    receipt = PlcWriteReceipt.fromPayload(result["outputs"]["receipt"])
    assert (receipt.valueCount, receipt.wordCount, receipt.attempts) == (2, 4, 1)
    request = server.requests[0]
    assert struct.unpack_from("<H", request, 11)[0] == 0x1401
    assert struct.unpack_from("<H", request, 19)[0] == 4
    assert struct.unpack("<ff", request[21:29]) == (1.5, -2.25)


def testSlmpWriteCanForwardAReadContractAndRejectsAmbiguousSources() -> None:
    data = PlcValueCollection("D", 798, "uint16", (2, 1935), 2).toPayload()
    with _ScriptedSlmpServer([_slmpResponse()]) as server:
        result = PlcSlmpWriteOperator().executeNode(
            {"data": data},
            _plcParams(server.port),
            {},
        )
    assert result["status"] == "ok"
    assert int.from_bytes(server.requests[0][15:18], "little") == 798
    assert struct.unpack("<HH", server.requests[0][21:25]) == (2, 1935)

    ambiguous = PlcSlmpWriteOperator().executeNode(
        {"value": 1, "values": [2]},
        _plcParams(1),
        {},
    )
    assert ambiguous["status"] == "error"
    assert ambiguous["error"]["code"] == "E_INPUT_SHAPE"


def testSlmpParamValidationEnforcesBitAndWordLimits() -> None:
    operator = PlcSlmpReadOperator()
    assert operator.validateParams({"device": "D", "dataType": "bit"}) is not None
    error = operator.validateParams({"dataType": "int32", "count": 481})
    assert error == {
        "code": "E_PARAM_INVALID",
        "message": "requested values exceed the 960-word SLMP limit",
    }
    writeProperties = PLC_WRITE_PARAM_SCHEMA["properties"]
    assert isinstance(writeProperties, dict)
    assert writeProperties["retryCount"]["default"] == 0


@pytest.mark.parametrize(
    ("operator", "params"),
    (
        (PlcSlmpReadOperator(), {"device": []}),
        (PlcSlmpReadOperator(), {"dataType": {}}),
        (PlcSlmpWriteOperator(), {"device": {}}),
        (PlcSlmpWriteOperator(), {"dataType": []}),
        (TcpClientOperator(), {"operation": []}),
        (TcpClientOperator(), {"textEncoding": {}}),
        (TcpClientOperator(), {"responseEncoding": []}),
        (TcpClientOperator(), {"responseFraming": {}}),
        (TcpReceiveOnceOperator(), {"framing": []}),
        (TcpReceiveOnceOperator(), {"messageEncoding": {}}),
        (TcpReceiveOnceOperator(), {"ackEncoding": []}),
    ),
)
def testCommunicationEnumParamsRejectNonStringsWithoutRaising(
    operator: Any,
    params: dict[str, object],
) -> None:
    error = operator.validateParams(params)
    assert error is not None
    assert error["code"] == "E_PARAM_INVALID"


def testSlmpMWordUnitRangeAccountsForSixteenBitsPerWord() -> None:
    operator = PlcSlmpReadOperator()
    assert (
        operator.validateParams(
            {
                "device": "M",
                "startAddress": 0xFFFFF0,
                "dataType": "uint16",
                "count": 1,
            }
        )
        is None
    )
    overflow = operator.validateParams(
        {
            "device": "M",
            "startAddress": 0xFFFFF1,
            "dataType": "uint16",
            "count": 1,
        }
    )
    assert overflow is not None
    assert overflow["code"] == "E_PARAM_INVALID"

    writeOverflow = PlcSlmpWriteOperator().executeNode(
        {"value": 1},
        {
            "device": "M",
            "startAddress": 0xFFFFF1,
            "dataType": "uint16",
            "retryCount": 0,
        },
        {},
    )
    assert writeOverflow["status"] == "error"
    assert writeOverflow["error"]["code"] == "E_INPUT_SHAPE"


@pytest.mark.parametrize(
    ("dataType", "raw", "expected"),
    (
        ("uint16", struct.pack("<H", 65535), 65535),
        ("int16", struct.pack("<H", 0xFFFC), -4),
        ("uint32", struct.pack("<I", 0x12345678), 0x12345678),
        ("int32", struct.pack("<i", -123456), -123456),
        ("float32", struct.pack("<f", 1.5), 1.5),
    ),
)
def testSlmpReadEmbedsNumericScalarOutput(
    dataType: str,
    raw: bytes,
    expected: int | float,
) -> None:
    with _ScriptedSlmpServer([_slmpResponse(raw)]) as server:
        result = PlcSlmpReadOperator().executeNode(
            {},
            _plcParams(server.port, dataType=dataType, count=1),
            {},
        )
    assert result["status"] == "ok"
    assert result["outputs"]["numberValue"] == expected
    assert "booleanValue" not in result["outputs"]


def testSlmpReadSelectsOutputIndexAndNormalizesLowercaseDevice() -> None:
    with _ScriptedSlmpServer([_slmpResponse(struct.pack("<HH", 4, 9))]) as server:
        result = PlcSlmpReadOperator().executeNode(
            {},
            _plcParams(
                server.port,
                device="d",
                dataType="uint16",
                count=2,
                outputIndex=1,
            ),
            {},
        )
    assert result["status"] == "ok"
    assert result["outputs"]["numberValue"] == 9
    assert PlcValueCollection.fromPayload(result["outputs"]["values"]).device == "D"
    assert PlcSlmpReadOperator().validateParams({"count": 2, "outputIndex": 2}) == {
        "code": "E_PARAM_INVALID",
        "message": "outputIndex must be an integer smaller than count",
    }


def testSlmpBitReadEmbedsOnlyBooleanScalarOutput() -> None:
    with _ScriptedSlmpServer([_slmpResponse(struct.pack("<H", 1))]) as server:
        result = PlcSlmpReadOperator().executeNode(
            {},
            _plcParams(server.port, device="m", dataType="bit", count=1),
            {},
        )
    assert result["status"] == "ok"
    assert result["outputs"]["booleanValue"] is True
    assert "numberValue" not in result["outputs"]


def testSlmpRejectsMismatchedRouteAndUnexpectedWriteData() -> None:
    wrongRoute = bytearray(_slmpResponse(struct.pack("<H", 1)))
    wrongRoute[3] = 0
    with _ScriptedSlmpServer([bytes(wrongRoute)]) as server:
        result = PlcSlmpReadOperator().executeNode(
            {},
            _plcParams(server.port),
            {},
        )
    assert result["status"] == "error"
    assert result["error"]["code"] == "E_COMM_PROTOCOL"

    with _ScriptedSlmpServer([_slmpResponse(b"\x01\x00")]) as writeServer:
        write = PlcSlmpWriteOperator().executeNode(
            {"value": 1},
            _plcParams(writeServer.port),
            {},
        )
    assert write["status"] == "error"
    assert write["error"]["code"] == "E_COMM_PROTOCOL"


def testSlmpResponseTimeoutIsATotalDeadline() -> None:
    response = _slmpResponse(struct.pack("<H", 7))

    def handler(client: socket.socket) -> None:
        header = _recvExact(client, 9)
        _recvExact(client, struct.unpack_from("<H", header, 7)[0])
        for byte in response:
            try:
                client.sendall(bytes([byte]))
            except OSError:
                break
            time.sleep(0.015)

    port, thread, errors = _startTcpPeer(handler)
    result = PlcSlmpReadOperator().executeNode(
        {},
        _plcParams(port, responseTimeoutMs=40),
        {},
    )
    _joinPeer(thread, errors)
    assert result["status"] == "error"
    assert result["error"]["code"] == "E_COMM_TIMEOUT"


def testTcpClientExchangeUsesNewlineFramingWithoutLeakingDelimiter() -> None:
    received: list[bytes] = []

    def handler(client: socket.socket) -> None:
        received.append(_recvUntil(client, b"\n"))
        client.sendall(b"OK ready\r\ntrailing")

    port, thread, errors = _startTcpPeer(handler)
    result = TcpClientOperator().executeNode(
        {"text": "hello"},
        {
            "host": "127.0.0.1",
            "port": port,
            "operation": "exchange",
            "appendNewline": True,
            "connectTimeoutMs": 500,
            "responseTimeoutMs": 500,
            "responseFraming": "newline",
            "responseTextEncoding": "utf-8",
        },
        {},
    )
    _joinPeer(thread, errors)
    assert result["status"] == "ok"
    assert received == [b"hello\n"]
    response = TcpMessage.fromPayload(result["outputs"]["response"])
    assert (response.data, response.byteLength) == ("OK ready", 8)
    assert result["outputs"]["responseText"] == "OK ready"


@pytest.mark.parametrize(
    ("text", "encoding", "storageEncoding"),
    (
        ("坐标", "utf-8", "base64"),
        ("hello", "ascii", "hex"),
        ("café", "latin-1", "base64"),
    ),
)
def testTcpClientTextOutputDecodesRawBytesIndependentlyOfDtoEncoding(
    text: str,
    encoding: str,
    storageEncoding: str,
) -> None:
    payload = text.encode(encoding)

    def handler(client: socket.socket) -> None:
        _recvExact(client, 1)
        client.sendall(payload + b"\n")

    port, thread, errors = _startTcpPeer(handler)
    result = TcpClientOperator().executeNode(
        {"text": "x"},
        {
            "host": "127.0.0.1",
            "port": port,
            "operation": "exchange",
            "responseEncoding": storageEncoding,
            "responseTextEncoding": encoding,
        },
        {},
    )
    _joinPeer(thread, errors)
    assert result["status"] == "ok"
    assert result["outputs"]["responseText"] == text
    assert TcpMessage.fromPayload(result["outputs"]["response"]).toBytes() == payload


def testTcpClientTextOutputValidationAndDecodeFailure() -> None:
    invalidMode = TcpClientOperator().validateParams(
        {"operation": "send", "responseTextEncoding": "utf-8"}
    )
    assert invalidMode is not None
    assert invalidMode["code"] == "E_PARAM_INVALID"

    def handler(client: socket.socket) -> None:
        _recvExact(client, 1)
        client.sendall(b"\xff\n")

    port, thread, errors = _startTcpPeer(handler)
    invalidText = TcpClientOperator().executeNode(
        {"text": "x"},
        {
            "host": "127.0.0.1",
            "port": port,
            "operation": "exchange",
            "responseEncoding": "hex",
            "responseTextEncoding": "ascii",
        },
        {},
    )
    _joinPeer(thread, errors)
    assert invalidText["status"] == "error"
    assert invalidText["error"]["code"] == "E_RESULT_INVALID"


def testTcpClientSupportsBinaryMessageInputAndTimeoutErrors() -> None:
    received: list[bytes] = []

    def binaryHandler(client: socket.socket) -> None:
        received.append(_recvExact(client, 3))

    port, thread, errors = _startTcpPeer(binaryHandler)
    message = TcpMessage.fromBytes(b"\x00\xFF\x10", encoding="base64").toPayload()
    result = TcpClientOperator().executeNode(
        {"message": message},
        {"host": "127.0.0.1", "port": port},
        {},
    )
    _joinPeer(thread, errors)
    assert result["status"] == "ok"
    assert received == [b"\x00\xFF\x10"]

    def slowHandler(client: socket.socket) -> None:
        _recvExact(client, 1)
        time.sleep(0.1)

    slowPort, slowThread, slowErrors = _startTcpPeer(slowHandler)
    timeout = TcpClientOperator().executeNode(
        {"text": "x"},
        {
            "host": "127.0.0.1",
            "port": slowPort,
            "operation": "exchange",
            "responseTimeoutMs": 20,
        },
        {},
    )
    _joinPeer(slowThread, slowErrors)
    assert timeout["status"] == "error"
    assert timeout["error"]["code"] == "E_COMM_TIMEOUT"


def testTcpOutboundPayloadsEnforceConfiguredLimits() -> None:
    client = TcpClientOperator().executeNode(
        {"text": "abcd"},
        {"maxRequestBytes": 3},
        {},
    )
    assert client["status"] == "error"
    assert client["error"]["code"] == "E_INPUT_SHAPE"

    receiver = TcpReceiveOnceOperator().executeNode(
        {},
        {"ackText": "abcd", "ackAppendNewline": False, "maxAckBytes": 3},
        {},
    )
    assert receiver["status"] == "error"
    assert receiver["error"]["code"] == "E_INPUT_SHAPE"


def testTcpReceiveOnceAcceptsSplitQuotedMessageAndSendsFixedAck() -> None:
    port = _unusedTcpPort()
    resultBox: dict[str, dict[str, Any]] = {}

    def runOperator() -> None:
        resultBox["result"] = TcpReceiveOnceOperator().executeNode(
            {},
            {
                "bindHost": "127.0.0.1",
                "port": port,
                "acceptTimeoutMs": 1000,
                "readTimeoutMs": 500,
                "framing": "quoted",
                "messageEncoding": "utf-8",
                "messageTextEncoding": "utf-8",
                "ackText": "OK",
                "ackAppendNewline": True,
            },
            {},
        )

    thread = threading.Thread(target=runOperator, daemon=True)
    thread.start()
    client = _connectEventually(port)
    with client:
        client.settimeout(1.0)
        client.sendall('"20.83,'.encode())
        client.sendall('15.61"'.encode())
        assert _recvUntil(client, b"\n") == b"OK\n"
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    result = resultBox["result"]
    assert result["status"] == "ok"
    message = TcpMessage.fromPayload(result["outputs"]["message"])
    assert message.data == '"20.83,15.61"'
    assert message.peerHost == "127.0.0.1"
    assert result["outputs"]["text"] == '"20.83,15.61"'


def testTcpReceiveTextOutputIsIndependentOfMessageEncoding() -> None:
    port = _unusedTcpPort()
    resultBox: dict[str, dict[str, Any]] = {}

    def runOperator() -> None:
        resultBox["result"] = TcpReceiveOnceOperator().executeNode(
            {},
            {
                "bindHost": "127.0.0.1",
                "port": port,
                "acceptTimeoutMs": 1000,
                "readTimeoutMs": 500,
                "messageEncoding": "base64",
                "messageTextEncoding": "latin-1",
            },
            {},
        )

    thread = threading.Thread(target=runOperator, daemon=True)
    thread.start()
    with _connectEventually(port) as client:
        client.sendall("café".encode("latin-1") + b"\n")
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    result = resultBox["result"]
    assert result["status"] == "ok"
    assert result["outputs"]["text"] == "café"
    assert TcpMessage.fromPayload(result["outputs"]["message"]).toBytes() == "café".encode(
        "latin-1"
    )


def testTcpReceiveTextOutputRejectsInvalidBytesAsResultError() -> None:
    port = _unusedTcpPort()
    resultBox: dict[str, dict[str, Any]] = {}

    def runOperator() -> None:
        resultBox["result"] = TcpReceiveOnceOperator().executeNode(
            {},
            {
                "bindHost": "127.0.0.1",
                "port": port,
                "acceptTimeoutMs": 1000,
                "readTimeoutMs": 500,
                "messageEncoding": "hex",
                "messageTextEncoding": "ascii",
            },
            {},
        )

    thread = threading.Thread(target=runOperator, daemon=True)
    thread.start()
    with _connectEventually(port) as client:
        client.sendall(b"\xff\n")
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    result = resultBox["result"]
    assert result["status"] == "error"
    assert result["error"]["code"] == "E_RESULT_INVALID"


def testTcpReceiveOnceIsBoundedByAcceptTimeout() -> None:
    result = TcpReceiveOnceOperator().executeNode(
        {},
        {
            "bindHost": "127.0.0.1",
            "port": _unusedTcpPort(),
            "acceptTimeoutMs": 20,
        },
        {},
    )
    assert result["status"] == "error"
    assert result["error"]["code"] == "E_COMM_TIMEOUT"


def testTcpFramingHonorsPayloadLimitAndTotalDeadline() -> None:
    receiver, sender = socket.socketpair()
    with receiver, sender:
        receiver.settimeout(0.2)
        sender.sendall(b"12345\r\n")
        assert receiveFramed(
            receiver,
            framing="newline",
            maxBytes=5,
            expectedBytes=0,
        ) == b"12345"

    receiver, sender = socket.socketpair()
    with receiver, sender:
        receiver.settimeout(0.2)
        sender.sendall(b"123456\n")
        try:
            receiveFramed(
                receiver,
                framing="newline",
                maxBytes=5,
                expectedBytes=0,
            )
        except TcpFrameError:
            pass
        else:  # pragma: no cover - assertion branch
            raise AssertionError("oversized newline frame was accepted")

    receiver, sender = socket.socketpair()
    with receiver, sender:
        receiver.settimeout(0.04)

        def drip() -> None:
            for byte in b"abcdef":
                try:
                    sender.sendall(bytes([byte]))
                except OSError:
                    return
                time.sleep(0.015)

        thread = threading.Thread(target=drip, daemon=True)
        thread.start()
        try:
            receiveFramed(
                receiver,
                framing="newline",
                maxBytes=100,
                expectedBytes=0,
            )
        except TcpTimeoutError:
            pass
        else:  # pragma: no cover - assertion branch
            raise AssertionError("drip-fed frame exceeded the total deadline")
        thread.join(timeout=1.0)


def testTcpReceiveOnceReportsBindConflict() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        result = TcpReceiveOnceOperator().executeNode(
            {},
            {
                "bindHost": "127.0.0.1",
                "port": int(occupied.getsockname()[1]),
                "acceptTimeoutMs": 20,
            },
            {},
        )
    assert result["status"] == "error"
    assert result["error"]["code"] == "E_COMM_CONNECT_FAILED"


def testTcpListenOnceRestoresFullTimeoutBeforeSendingAck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fakeReceive(sock: socket.socket, **_: object) -> bytes:
        sock.settimeout(0.001)
        return b"message"

    def fakeSend(sock: socket.socket, data: bytes) -> None:
        observed["timeout"] = sock.gettimeout()
        observed["data"] = data

    monkeypatch.setattr(tcp_transport_module, "receiveFramed", fakeReceive)
    monkeypatch.setattr(tcp_transport_module, "sendAll", fakeSend)
    port = _unusedTcpPort()
    resultBox: dict[str, tuple[bytes, tuple[str, int]]] = {}

    def runListener() -> None:
        resultBox["value"] = tcp_transport_module.listenOnce(
            "127.0.0.1",
            port,
            acceptTimeoutSec=1.0,
            ioTimeoutSec=0.2,
            framing="idle",
            maxBytes=32,
            expectedBytes=0,
            ack=b"OK",
        )

    thread = threading.Thread(target=runListener, daemon=True)
    thread.start()
    with _connectEventually(port):
        thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert observed["timeout"] == pytest.approx(0.2)
    assert observed["data"] == b"OK"
    assert resultBox["value"][0] == b"message"


def _startTcpPeer(
    handler: Callable[[socket.socket], None],
) -> tuple[int, threading.Thread, list[BaseException]]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(2.0)
    port = int(server.getsockname()[1])
    errors: list[BaseException] = []

    def run() -> None:
        try:
            with server:
                client, _ = server.accept()
                with client:
                    client.settimeout(1.0)
                    handler(client)
        except BaseException as exc:  # pragma: no cover - asserted by caller
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return port, thread, errors


def _joinPeer(thread: threading.Thread, errors: list[BaseException]) -> None:
    thread.join(timeout=3.0)
    assert not thread.is_alive()
    assert errors == []


def _connectEventually(port: int) -> socket.socket:
    deadline = time.monotonic() + 1.0
    while True:
        try:
            return socket.create_connection(("127.0.0.1", port), timeout=0.1)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _unusedTcpPort() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _recvExact(sock: socket.socket, size: int) -> bytes:
    buffer = bytearray()
    while len(buffer) < size:
        chunk = sock.recv(size - len(buffer))
        if not chunk:
            raise ConnectionError("peer closed early")
        buffer.extend(chunk)
    return bytes(buffer)


def _recvUntil(sock: socket.socket, delimiter: bytes) -> bytes:
    buffer = bytearray()
    while delimiter not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buffer.extend(chunk)
    return bytes(buffer)
