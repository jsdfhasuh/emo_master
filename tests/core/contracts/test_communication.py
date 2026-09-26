from __future__ import annotations

import math

import pytest

from emo_master.core.contracts.communication import (
    CommunicationPayloadValidationError,
    PlcValueCollection,
    PlcWriteReceipt,
    TcpMessage,
    matchesCommunicationPortType,
    parseCommunicationPayload,
)
from emo_master.core.contracts.port_compatibility import arePortTypesCompatible
from emo_master.core.contracts.port_types import (
    PortSpecValidationError,
    matchesPortSpec,
    validatePortSpec,
)


def testPlcValueCollectionRoundTripsEverySupportedShape() -> None:
    cases = (
        PlcValueCollection("D", 10, "uint16", (0, 65535), 2),
        PlcValueCollection("D", 20, "int16", (-32768, 32767), 2),
        PlcValueCollection("D", 30, "uint32", (0, 0xFFFFFFFF), 4),
        PlcValueCollection("D", 40, "int32", (-0x80000000, 0x7FFFFFFF), 4),
        PlcValueCollection("D", 50, "float32", (1.25, -2.5), 4),
        PlcValueCollection("M", 60, "bit", (True,), 1),
    )
    for value in cases:
        payload = value.toPayload()
        assert PlcValueCollection.fromPayload(payload) == value
        assert parseCommunicationPayload(payload) == value
        assert matchesPortSpec(
            payload,
            {
                "type": "plcValueCollection",
                "required": True,
                "schemaVersion": "1.x",
            },
        )


def testPlcContractsRejectUnknownFieldsNonFiniteAndInconsistentWords() -> None:
    payload = PlcValueCollection("D", 0, "uint16", (1,), 1).toPayload()
    payload["extra"] = True
    with pytest.raises(CommunicationPayloadValidationError, match="unknown fields"):
        PlcValueCollection.fromPayload(payload)
    with pytest.raises(CommunicationPayloadValidationError, match="finite"):
        PlcValueCollection("D", 0, "float32", (math.inf,), 2)
    with pytest.raises(CommunicationPayloadValidationError, match="float32"):
        PlcValueCollection("D", 0, "float32", (1e100,), 2)
    with pytest.raises(CommunicationPayloadValidationError, match="wordCount"):
        PlcValueCollection("D", 0, "int32", (1,), 1)
    with pytest.raises(CommunicationPayloadValidationError, match="device M"):
        PlcValueCollection("D", 0, "bit", (True,), 1)
    tuplePayload = PlcValueCollection("D", 0, "uint16", (1,), 1).toPayload()
    tuplePayload["values"] = (1,)
    with pytest.raises(CommunicationPayloadValidationError, match="must be an array"):
        PlcValueCollection.fromPayload(tuplePayload)
    assert PlcValueCollection("M", 0xFFFFF0, "uint16", (1,), 1).startAddress == 0xFFFFF0
    with pytest.raises(CommunicationPayloadValidationError, match="address range"):
        PlcValueCollection("M", 0xFFFFF1, "uint16", (1,), 1)


def testPlcWriteReceiptRoundTripsStrictly() -> None:
    receipt = PlcWriteReceipt(
        device="d",
        startAddress=798,
        dataType="int32",
        valueCount=2,
        wordCount=4,
        attempts=2,
    )
    payload = receipt.toPayload()
    assert payload["device"] == "D"
    assert PlcWriteReceipt.fromPayload(payload) == receipt
    assert matchesCommunicationPortType(payload, "plcWriteReceipt", "1.0")
    with pytest.raises(CommunicationPayloadValidationError, match="address range"):
        PlcWriteReceipt("D", 0xFFFFFF, "uint32", 1, 2, 1)
    assert PlcWriteReceipt("M", 0xFFFFF0, "uint16", 1, 1, 1).wordCount == 1
    with pytest.raises(CommunicationPayloadValidationError, match="address range"):
        PlcWriteReceipt("M", 0xFFFFF1, "uint16", 1, 1, 1)


def testTcpMessagePreservesTextAndBinaryPayloads() -> None:
    text = TcpMessage.fromBytes(
        "坐标,-1.5".encode(),
        encoding="utf-8",
        peerHost="127.0.0.1",
        peerPort=10001,
    )
    assert TcpMessage.fromPayload(text.toPayload()) == text
    assert text.toBytes() == "坐标,-1.5".encode()

    raw = b"\x00\xff\x10"
    for encoding in ("hex", "base64"):
        message = TcpMessage.fromBytes(raw, encoding=encoding)
        assert TcpMessage.fromPayload(message.toPayload()).toBytes() == raw


def testTcpMessageRejectsBadLengthPeerAndEncoding() -> None:
    with pytest.raises(CommunicationPayloadValidationError, match="byteLength"):
        TcpMessage("abc", "ascii", 2)
    with pytest.raises(CommunicationPayloadValidationError, match="provided together"):
        TcpMessage("abc", "ascii", 3, peerHost="127.0.0.1")
    with pytest.raises(CommunicationPayloadValidationError, match="valid base64"):
        TcpMessage("***", "base64", 0)
    with pytest.raises(CommunicationPayloadValidationError, match="encoding must be"):
        TcpMessage.fromBytes(b"x", encoding=1)  # type: ignore[arg-type]
    with pytest.raises(CommunicationPayloadValidationError, match="value must be bytes"):
        TcpMessage.fromBytes("x", encoding="base64")  # type: ignore[arg-type]


def testCommunicationTypesAreStrictSemanticJsonSubtypes() -> None:
    messageSpec = {
        "type": "tcpMessage",
        "required": True,
        "schemaVersion": "1.x",
    }
    assert validatePortSpec(messageSpec) == messageSpec
    assert arePortTypesCompatible(messageSpec, "json")
    assert not arePortTypesCompatible("json", messageSpec)
    with pytest.raises(PortSpecValidationError, match="supported exact version"):
        validatePortSpec({"type": "tcpMessage", "schemaVersion": "1.2"})
    assert not matchesCommunicationPortType(
        {"type": "tcpMessage", "schemaVersion": "1.0"},
        "tcpMessage",
        "1.x",
    )
