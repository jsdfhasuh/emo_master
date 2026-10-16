"""MC 3E ASCII wire vectors and loopback coverage; no hardware endpoints."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from emo_master.apps.runtime.preview.plc_debug import PlcDebugManager, endpointFromParams
from emo_master.plugins.builtins._communication_operators import PlcSlmpReadOperator, PlcSlmpWriteOperator
from emo_master.plugins.builtins._slmp import (
    Slmp3EClient, Slmp3ESession, SlmpConnectionError, SlmpEndpoint,
    SlmpProtocolError, SlmpResponseError, SlmpTimeoutError,
)
from tests.runtime.plc_debug_server import BlockResponse, PlcDebugServer


def endpoint(peer, **kwargs):
    return SlmpEndpoint("127.0.0.1", peer.port, frameEncoding="ascii", responseTimeoutSec=.5, **kwargs)


def testKnownAsciiReadAndWriteFrames():
    # Literal 3E frames pin decimal addresses, hex point counts and byte order
    # independently of the loopback peer's parser and response builder.
    with PlcDebugServer(encoding="ascii", fragment=True) as peer:
        peer.words[800] = 0x1234
        client = Slmp3EClient(endpoint(peer))
        assert client.readWords("D", 800, 1) == [0x1234]
        client.writeWords("D", 796, [0x1234, 0xFFFF])
        assert peer.requests[0].raw == b"500000FF03FF000018000004010000D*0008000001"
        assert peer.requests[1].raw == b"500000FF03FF000020000014010000D*00079600021234FFFF"
        assert peer.words[796] == 0x1234 and peer.words[797] == 65535


def testLiteralResponseAndNonDefaultRoute():
    wire = b"D00002FE123403000C00001234FFFF"
    with PlcDebugServer([wire], encoding="ascii", fragment=True) as peer:
        client = Slmp3EClient(endpoint(peer, networkNo=2, pcNo=254, moduleIoNo=0x1234, moduleStationNo=3))
        assert client.readWords("D", 800, 2) == [0x1234, 65535]
        assert peer.requests[0].raw[4:14] == b"02FE123403"


@pytest.mark.parametrize("dataType,values", [
    ("uint16", [0, 65535]), ("int16", [-32768, 32767]),
    ("uint32", [4294967295, 65541]), ("int32", [-2147483648, -1]),
    ("float32", [1.5, -2.25]),
])
def testAsciiOperatorsRoundTripAllWordTypes(dataType, values):
    with PlcDebugServer(encoding="ascii") as peer:
        params = peer.params(startAddress=796, dataType=dataType, retryCount=0)
        written = PlcSlmpWriteOperator().executeNode({}, dict(params, values=values), {})
        assert written["status"] == "ok", written
        read = PlcSlmpReadOperator().executeNode({}, dict(params, count=2, outputIndex=1), {})
        assert read["status"] == "ok", read
        assert read["outputs"]["values"]["values"] == values
        assert read["outputs"]["numberValue"] == values[1]
        assert all(request.ascii for request in peer.requests)


def testPersistentAsciiWordAndOddBitAccess():
    with PlcDebugServer(encoding="ascii", fragment=True) as peer:
        client = Slmp3ESession(endpoint(peer))
        client.open()
        try:
            client.writeBits(100, [True, False, True])
            assert peer.requests[0].raw == b"500000FF03FF00001B000014010001M*0001000003101"
            assert client.readBits(100, 3) == [True, False, True]
            assert client.readWords("M", 100, 1) == [5]
            client.writeWords("M", 100, [10])
            assert client.readBits(100, 4) == [False, True, False, True]
            assert peer.connections == 1
        finally:
            client.close()


@pytest.mark.parametrize("wire,error", [
    (b"\xd0\x00\x00\xff\xff\x03\x00\x04\x00\x00\x00\x06\x00", SlmpProtocolError),
    (b"D00000FF03FF0000080000ZZZZ", SlmpProtocolError),
    (b"D00000FF03FF0000080000 001", SlmpProtocolError),
    (b"D00000FF03FF0000080000+001", SlmpProtocolError),
    (b"D00000FF03FF000004C051", SlmpResponseError),
    (b"D00001FF03FF00000800000001", SlmpProtocolError),
    (b"D00000FF03FF00FFFF", SlmpProtocolError),
    (b"D00000FF03FF000003", SlmpProtocolError),
    (b"D00000FF03FF0000080000", SlmpTimeoutError),
    (b"D00000FF03FF0000040000", SlmpProtocolError),
    (b"D00000FF03FF00+00800000001", SlmpProtocolError),
])
def testMalformedAsciiResponseRetiresSession(wire, error):
    with PlcDebugServer([wire], encoding="ascii") as peer:
        client = Slmp3ESession(endpoint(peer))
        client.open()
        try:
            with pytest.raises(error):
                client.readWords("D", 800, 1)
            with pytest.raises(SlmpConnectionError, match="not open"):
                client.readWords("D", 800, 1)
            assert len(peer.requests) == 1
        finally:
            client.close()


def testAsciiBitAndWriteResponsesAreStrict():
    for wire, write in [(b"D00000FF03FF00000500002", False), (b"D00000FF03FF00000800000001", True)]:
        with PlcDebugServer([wire], encoding="ascii") as peer:
            client = Slmp3ESession(endpoint(peer))
            client.open()
            try:
                with pytest.raises(SlmpProtocolError):
                    client.writeBits(100, [True]) if write else client.readBits(100, 1)
            finally:
                client.close()


def testAsciiCancellationClosesHeldRead():
    block = BlockResponse()
    with PlcDebugServer([block], encoding="ascii") as peer, ThreadPoolExecutor(1) as pool:
        client = Slmp3ESession(endpoint(peer))
        client.open()
        future = pool.submit(client.readWords, "D", 800, 1)
        try:
            assert block.entered.wait(1)
            client.close()
            with pytest.raises(SlmpConnectionError):
                future.result(timeout=1)
        finally:
            client.close()
            block.release.set()


def testAsciiRangeFailsBeforeNetworkAndLegacyDefault(monkeypatch):
    assert endpointFromParams({}).frameEncoding == "binary"
    assert endpointFromParams({"frameEncoding": "ascii"}).frameEncoding == "ascii"
    for invalidEncoding in ("auto", None, 1, True, [], {}):
        with pytest.raises(ValueError, match="frameEncoding must be binary or ascii"):
            endpointFromParams({"frameEncoding": invalidEncoding})
    client = Slmp3EClient(SlmpEndpoint("127.0.0.1", 4998, frameEncoding="ascii"))
    monkeypatch.setattr(client, "_openSocket", lambda: pytest.fail("invalid request opened a socket"))
    for device, start, count in [("D", 1000000, 1), ("D", 999999, 2), ("M", 999990, 1)]:
        with pytest.raises(ValueError, match="six decimal"):
            client.readWords(device, start, count)
    assert PlcSlmpReadOperator().validateParams({"frameEncoding": "auto"})
    assert PlcSlmpReadOperator().validateParams({"frameEncoding": "ascii", "startAddress": 999999, "count": 2})
    result = PlcSlmpWriteOperator().executeNode({}, {"frameEncoding": "ascii", "startAddress": 999999, "values": [1, 2]}, {})
    assert result["status"] == "error" and result["error"]["code"] == "E_INPUT_SHAPE"


def testBinaryDoesNotSwitchProtocolOnAsciiReply():
    with PlcDebugServer([b"D00000FF03FF00000800000006"]) as peer:
        client = Slmp3EClient(SlmpEndpoint("127.0.0.1", peer.port))
        with pytest.raises(SlmpProtocolError):
            client.readWords("D", 800, 1)
        assert len(peer.requests) == 1 and not peer.requests[0].ascii


def testAsciiDebugNeedsReadToVerifyProtocolAndPreservesWriteLock():
    manager = PlcDebugManager("ascii-test")
    with PlcDebugServer(encoding="ascii") as peer:
        session = manager.reserve("project", peer.params())
        try:
            connected = manager.connect(session)
            assert connected.ok and connected.state == "connected"
            assert not peer.requests and not connected.write_enabled
            read = manager.execute(session.sessionId, "ascii-test", "read", dict(device="D", startAddress=800, count=1), "read")
            assert read.ok and read.state == "verified"
            invalid = manager.execute(session.sessionId, "ascii-test", "write", dict(device="D", startAddress=999999, values=[1, 2]), "invalid")
            assert not invalid.ok and invalid.code == "E_PARAM_INVALID"
            denied = manager.execute(session.sessionId, "ascii-test", "write", dict(device="D", startAddress=796, values=[1]), "write")
            assert not denied.ok and not any(r.command == 0x1401 for r in peer.requests)
        finally:
            manager.close(session.sessionId)
