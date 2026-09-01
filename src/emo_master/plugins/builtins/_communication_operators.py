from __future__ import annotations

import base64
import binascii
import math
import socket
import time
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

from emo_master.core.contracts.communication import (
    PLC_DATA_TYPES,
    TCP_MESSAGE_ENCODINGS,
    CommunicationPayloadValidationError,
    PlcValueCollection,
    PlcWriteReceipt,
    TcpMessage,
    plcDeviceAddressSpan,
    plcWordsPerValue,
)
from emo_master.core.contracts.operator_logging import getOperatorLogger
from emo_master.plugins.builtins._slmp import (
    MAX_WORDS_PER_REQUEST,
    Slmp3EClient,
    SlmpConnectionError,
    SlmpEndpoint,
    SlmpProtocolError,
    SlmpResponseError,
    SlmpTimeoutError,
    decodePlcWords,
    encodePlcValues,
    runSlmpWithRetry,
)
from emo_master.plugins.builtins._tcp_transport import (
    TCP_FRAMING_MODES,
    TcpConnectError,
    TcpFrameError,
    TcpIoError,
    TcpTimeoutError,
    listenOnce,
    openTcpClient,
    receiveFramed,
    sendAll,
)


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


_PLC_COMMON_PROPERTIES: dict[str, object] = {
    "host": {"type": "string", "default": "127.0.0.1"},
    "port": {
        "type": "integer",
        "minimum": 1,
        "maximum": 65535,
        "default": 10001,
    },
    "device": {"type": "string", "enum": ["D", "M", "d", "m"], "default": "D"},
    "startAddress": {
        "type": "integer",
        "minimum": 0,
        "maximum": 16777215,
        "default": 0,
    },
    "connectTimeoutMs": {
        "type": "integer",
        "minimum": 1,
        "maximum": 60000,
        "default": 2000,
    },
    "responseTimeoutMs": {
        "type": "integer",
        "minimum": 1,
        "maximum": 60000,
        "default": 2000,
    },
    "retryCount": {
        "type": "integer",
        "minimum": 0,
        "maximum": 3,
        "default": 1,
    },
    "retryDelayMs": {
        "type": "integer",
        "minimum": 0,
        "maximum": 10000,
        "default": 0,
    },
    "networkNo": {
        "type": "integer",
        "minimum": 0,
        "maximum": 255,
        "default": 0,
    },
    "pcNo": {
        "type": "integer",
        "minimum": 0,
        "maximum": 255,
        "default": 255,
    },
    "moduleIoNo": {
        "type": "integer",
        "minimum": 0,
        "maximum": 65535,
        "default": 1023,
    },
    "moduleStationNo": {
        "type": "integer",
        "minimum": 0,
        "maximum": 255,
        "default": 0,
    },
    "monitoringTimer": {
        "type": "integer",
        "minimum": 0,
        "maximum": 65535,
        "default": 0,
    },
}

PLC_READ_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        **_PLC_COMMON_PROPERTIES,
        "dataType": {
            "type": "string",
            "enum": ["uint16", "int16", "uint32", "int32", "float32", "bit"],
            "default": "uint16",
        },
        "count": {
            "type": "integer",
            "minimum": 1,
            "maximum": 960,
            "default": 1,
        },
        "outputIndex": {
            "type": "integer",
            "minimum": 0,
            "maximum": MAX_WORDS_PER_REQUEST - 1,
            "default": 0,
        },
    },
}

PLC_WRITE_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        **_PLC_COMMON_PROPERTIES,
        "retryCount": {
            "type": "integer",
            "minimum": 0,
            "maximum": 3,
            "default": 0,
        },
        "dataType": {
            "type": "string",
            "enum": ["uint16", "int16", "uint32", "int32", "float32"],
            "default": "uint16",
        },
        "values": {
            "type": "array",
            "items": {"type": "number"},
            "default": [],
        },
    },
}

TCP_CLIENT_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "host": {"type": "string", "default": "127.0.0.1"},
        "port": {
            "type": "integer",
            "minimum": 1,
            "maximum": 65535,
            "default": 10001,
        },
        "operation": {
            "type": "string",
            "enum": ["send", "exchange"],
            "default": "send",
        },
        "text": {"type": "string", "default": ""},
        "textEncoding": {
            "type": "string",
            "enum": ["utf-8", "ascii", "latin-1", "hex", "base64"],
            "default": "utf-8",
        },
        "appendNewline": {"type": "boolean", "default": False},
        "shutdownWrite": {"type": "boolean", "default": False},
        "connectTimeoutMs": {
            "type": "integer",
            "minimum": 1,
            "maximum": 60000,
            "default": 2000,
        },
        "responseTimeoutMs": {
            "type": "integer",
            "minimum": 1,
            "maximum": 60000,
            "default": 2000,
        },
        "responseFraming": {
            "type": "string",
            "enum": ["newline", "quoted", "idle", "fixedLength"],
            "default": "newline",
        },
        "expectedBytes": {
            "type": "integer",
            "minimum": 0,
            "maximum": 16777216,
            "default": 0,
        },
        "maxResponseBytes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 16777216,
            "default": 1048576,
        },
        "maxRequestBytes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 16777216,
            "default": 1048576,
        },
        "responseEncoding": {
            "type": "string",
            "enum": ["utf-8", "ascii", "latin-1", "hex", "base64"],
            "default": "utf-8",
        },
        "responseTextEncoding": {
            "type": "string",
            "enum": ["none", "utf-8", "ascii", "latin-1"],
            "default": "none",
        },
    },
}

TCP_RECEIVE_ONCE_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "bindHost": {"type": "string", "default": "127.0.0.1"},
        "port": {
            "type": "integer",
            "minimum": 1,
            "maximum": 65535,
            "default": 10001,
        },
        "acceptTimeoutMs": {
            "type": "integer",
            "minimum": 1,
            "maximum": 60000,
            "default": 5000,
        },
        "readTimeoutMs": {
            "type": "integer",
            "minimum": 1,
            "maximum": 60000,
            "default": 1000,
        },
        "framing": {
            "type": "string",
            "enum": ["newline", "quoted", "idle", "fixedLength"],
            "default": "newline",
        },
        "expectedBytes": {
            "type": "integer",
            "minimum": 0,
            "maximum": 16777216,
            "default": 0,
        },
        "maxMessageBytes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 16777216,
            "default": 1048576,
        },
        "messageEncoding": {
            "type": "string",
            "enum": ["utf-8", "ascii", "latin-1", "hex", "base64"],
            "default": "utf-8",
        },
        "messageTextEncoding": {
            "type": "string",
            "enum": ["none", "utf-8", "ascii", "latin-1"],
            "default": "none",
        },
        "ackText": {"type": "string", "default": ""},
        "ackEncoding": {
            "type": "string",
            "enum": ["utf-8", "ascii", "latin-1", "hex", "base64"],
            "default": "utf-8",
        },
        "ackAppendNewline": {"type": "boolean", "default": True},
        "maxAckBytes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 16777216,
            "default": 1048576,
        },
    },
}

class PlcSlmpReadOperator:
    meta = OperatorMeta(
        operatorId="communication.plc.slmp_read",
        displayName="PLC SLMP Read",
        version="1.1.0",
        inputPorts={},
        outputPorts={
            "values": {
                "type": "plcValueCollection",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "numberValue": {"type": "number", "required": False, "nullable": False},
            "booleanValue": {
                "type": "boolean",
                "required": False,
                "nullable": False,
            },
        },
        paramSchema=PLC_READ_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        error = _validatePlcCommonParams(params, {"dataType", "count", "outputIndex"})
        if error is not None:
            return error
        dataType = params.get("dataType", "uint16")
        if not isinstance(dataType, str) or dataType not in PLC_DATA_TYPES:
            return _paramError("dataType is not supported")
        count = params.get("count", 1)
        if not _isIntegerInRange(count, 1, MAX_WORDS_PER_REQUEST):
            return _paramError("count must be an integer between 1 and 960")
        outputIndex = params.get("outputIndex", 0)
        if not _isIntegerInRange(outputIndex, 0, cast(int, count) - 1):
            return _paramError("outputIndex must be an integer smaller than count")
        device = str(params.get("device", "D")).upper()
        if dataType == "bit" and (device != "M" or count != 1):
            return _paramError("bit reads require device M and count=1")
        if dataType in {"uint32", "int32", "float32"} and device != "D":
            return _paramError(f"{dataType} reads require device D")
        wordCount = cast(int, count) * plcWordsPerValue(cast(str, dataType))
        if wordCount > MAX_WORDS_PER_REQUEST:
            return _paramError("requested values exceed the 960-word SLMP limit")
        startAddress = cast(int, params.get("startAddress", 0))
        if startAddress + plcDeviceAddressSpan(device, wordCount) - 1 > 0xFFFFFF:
            return _paramError("requested address range exceeds the 24-bit device space")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = inputs
        logger = getOperatorLogger(runtimeContext)
        startedAt = perf_counter()
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        endpoint = _slmpEndpoint(params)
        device = str(params.get("device", "D")).upper()
        startAddress = cast(int, params.get("startAddress", 0))
        dataType = cast(str, params.get("dataType", "uint16"))
        valueCount = cast(int, params.get("count", 1))
        outputIndex = cast(int, params.get("outputIndex", 0))
        wordCount = valueCount * plcWordsPerValue(dataType)
        retryCount = cast(int, params.get("retryCount", 1))
        retryDelayMs = cast(int, params.get("retryDelayMs", 0))
        client = Slmp3EClient(endpoint)
        logger.info(
            "starting SLMP read",
            payload={
                "host": endpoint.host,
                "port": endpoint.port,
                "device": device,
                "startAddress": startAddress,
                "valueCount": valueCount,
                "dataType": dataType,
            },
        )
        try:
            words, attempts = runSlmpWithRetry(
                lambda: client.readWords(device, startAddress, wordCount),
                retryCount=retryCount,
                beforeRetry=lambda: _delay(retryDelayMs),
            )
            values = decodePlcWords(words, dataType)
            payload = PlcValueCollection(
                device=device,
                startAddress=startAddress,
                dataType=dataType,
                values=values,
                wordCount=wordCount,
            ).toPayload()
        except Exception as exc:
            logger.error(
                "SLMP read failed",
                code=getattr(exc, "code", "E_COMMUNICATION_IO"),
                payload={"errorType": type(exc).__name__},
            )
            return _communicationError(exc)
        latencyMs = round((perf_counter() - startedAt) * 1000.0, 3)
        outputs: dict[str, object] = {"values": payload}
        selectedValue = values[outputIndex]
        if dataType == "bit":
            outputs["booleanValue"] = selectedValue
        else:
            outputs["numberValue"] = selectedValue
        logger.info(
            "SLMP read completed",
            payload={"attempts": attempts, "valueCount": valueCount, "wordCount": wordCount},
        )
        return {
            "status": "ok",
            "outputs": outputs,
            "metrics": {
                "latencyMs": latencyMs,
                "attempts": attempts,
                "valueCount": valueCount,
                "wordCount": wordCount,
                "outputIndex": outputIndex,
            },
            "diagnostics": {
                "text": f"Read {valueCount} {dataType} value(s) from {device}{startAddress}"
            },
        }


class PlcSlmpWriteOperator:
    meta = OperatorMeta(
        operatorId="communication.plc.slmp_write",
        displayName="PLC SLMP Write",
        version="1.0.0",
        inputPorts={
            "data": {
                "type": "plcValueCollection",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "value": {"type": "number", "required": False, "nullable": False},
            "values": {
                "type": "list<number>",
                "required": False,
                "nullable": False,
            },
        },
        outputPorts={
            "receipt": {
                "type": "plcWriteReceipt",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.x",
            }
        },
        paramSchema=PLC_WRITE_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        error = _validatePlcCommonParams(params, {"dataType", "values"})
        if error is not None:
            return error
        dataType = params.get("dataType", "uint16")
        if not isinstance(dataType, str) or dataType not in PLC_DATA_TYPES - {"bit"}:
            return _paramError("write dataType is not supported")
        device = str(params.get("device", "D")).upper()
        if dataType in {"uint32", "int32", "float32"} and device != "D":
            return _paramError(f"{dataType} writes require device D")
        paramValues = params.get("values", [])
        if not isinstance(paramValues, list):
            return _paramError("values must be an array")
        if any(not _isFiniteNumber(value) for value in paramValues):
            return _paramError("values must contain only finite numbers")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        logger = getOperatorLogger(runtimeContext)
        startedAt = perf_counter()
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        try:
            device, startAddress, dataType, sourceValues = _plcWriteSource(inputs, params)
            words = encodePlcValues(sourceValues, dataType)
            if (
                startAddress + plcDeviceAddressSpan(device, len(words)) - 1
                > 0xFFFFFF
            ):
                raise ValueError("write address range exceeds the 24-bit device space")
        except CommunicationPayloadValidationError as exc:
            return _error("E_INPUT_TYPE", str(exc))
        except (TypeError, ValueError) as exc:
            return _error("E_INPUT_SHAPE", str(exc))
        endpoint = _slmpEndpoint(params)
        retryCount = cast(int, params.get("retryCount", 0))
        retryDelayMs = cast(int, params.get("retryDelayMs", 0))
        client = Slmp3EClient(endpoint)
        logger.info(
            "starting SLMP write",
            payload={
                "host": endpoint.host,
                "port": endpoint.port,
                "device": device,
                "startAddress": startAddress,
                "valueCount": len(sourceValues),
                "wordCount": len(words),
                "dataType": dataType,
            },
        )
        try:
            _, attempts = runSlmpWithRetry(
                lambda: client.writeWords(device, startAddress, words),
                retryCount=retryCount,
                beforeRetry=lambda: _delay(retryDelayMs),
            )
            receipt = PlcWriteReceipt(
                device=device,
                startAddress=startAddress,
                dataType=dataType,
                valueCount=len(sourceValues),
                wordCount=len(words),
                attempts=attempts,
            ).toPayload()
        except Exception as exc:
            logger.error(
                "SLMP write failed",
                code=getattr(exc, "code", "E_COMMUNICATION_IO"),
                payload={"errorType": type(exc).__name__},
            )
            return _communicationError(exc)
        latencyMs = round((perf_counter() - startedAt) * 1000.0, 3)
        logger.info(
            "SLMP write completed",
            payload={"attempts": attempts, "valueCount": len(sourceValues), "wordCount": len(words)},
        )
        return {
            "status": "ok",
            "outputs": {"receipt": receipt},
            "metrics": {
                "latencyMs": latencyMs,
                "attempts": attempts,
                "valueCount": len(sourceValues),
                "wordCount": len(words),
            },
            "diagnostics": {
                "text": f"Wrote {len(sourceValues)} {dataType} value(s) to "
                f"{device}{startAddress}"
            },
        }


class TcpClientOperator:
    meta = OperatorMeta(
        operatorId="communication.tcp.client",
        displayName="TCP Client",
        version="1.1.0",
        inputPorts={
            "message": {
                "type": "tcpMessage",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "text": {"type": "string", "required": False, "nullable": False},
        },
        outputPorts={
            "response": {
                "type": "tcpMessage",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "sentBytes": {"type": "integer", "required": True, "nullable": False},
            "responseText": {
                "type": "string",
                "required": False,
                "nullable": False,
            },
        },
        paramSchema=TCP_CLIENT_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        error = _validateTcpParams(
            params,
            allowed={
                "host",
                "port",
                "operation",
                "text",
                "textEncoding",
                "appendNewline",
                "shutdownWrite",
                "connectTimeoutMs",
                "responseTimeoutMs",
                "responseFraming",
                "expectedBytes",
                "maxResponseBytes",
                "maxRequestBytes",
                "responseEncoding",
                "responseTextEncoding",
            },
        )
        if error is not None:
            return error
        operation = params.get("operation", "send")
        if not isinstance(operation, str) or operation not in {"send", "exchange"}:
            return _paramError("operation must be send or exchange")
        responseTextEncoding = params.get("responseTextEncoding", "none")
        if not isinstance(responseTextEncoding, str) or responseTextEncoding not in {
            "none",
            "utf-8",
            "ascii",
            "latin-1",
        }:
            return _paramError("responseTextEncoding is not supported")
        if responseTextEncoding != "none" and operation != "exchange":
            return _paramError("responseTextEncoding requires operation=exchange")
        if not isinstance(params.get("text", ""), str):
            return _paramError("text must be a string")
        for name, default in (
            ("textEncoding", "utf-8"),
            ("responseEncoding", "utf-8"),
        ):
            encoding = params.get(name, default)
            if not isinstance(encoding, str) or encoding not in TCP_MESSAGE_ENCODINGS:
                return _paramError(f"{name} is not supported")
        framing = params.get("responseFraming", "newline")
        if not isinstance(framing, str) or framing not in TCP_FRAMING_MODES:
            return _paramError("responseFraming is not supported")
        expectedBytes = params.get("expectedBytes", 0)
        maxBytes = params.get("maxResponseBytes", 1048576)
        if not _isIntegerInRange(expectedBytes, 0, 16777216):
            return _paramError("expectedBytes must be between 0 and 16777216")
        if not _isIntegerInRange(maxBytes, 1, 16777216):
            return _paramError("maxResponseBytes must be between 1 and 16777216")
        if not _isIntegerInRange(
            params.get("maxRequestBytes", 1048576), 1, 16777216
        ):
            return _paramError("maxRequestBytes must be between 1 and 16777216")
        if framing == "fixedLength" and not 1 <= cast(int, expectedBytes) <= cast(
            int, maxBytes
        ):
            return _paramError(
                "fixedLength framing requires expectedBytes between 1 and maxResponseBytes"
            )
        for name, booleanDefault in (
            ("appendNewline", False),
            ("shutdownWrite", False),
        ):
            if not isinstance(params.get(name, booleanDefault), bool):
                return _paramError(f"{name} must be boolean")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        logger = getOperatorLogger(runtimeContext)
        startedAt = perf_counter()
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        try:
            outbound = _tcpClientSource(inputs, params)
        except CommunicationPayloadValidationError as exc:
            return _error("E_INPUT_TYPE", str(exc))
        except (TypeError, ValueError) as exc:
            return _error("E_INPUT_SHAPE", str(exc))
        if bool(params.get("appendNewline", False)):
            outbound += b"\n"
        if not outbound:
            return _error("E_INPUT_SHAPE", "TCP message must contain at least one byte")
        maxRequestBytes = cast(int, params.get("maxRequestBytes", 1048576))
        if len(outbound) > maxRequestBytes:
            return _error(
                "E_INPUT_SHAPE",
                f"TCP request exceeds maxRequestBytes={maxRequestBytes}",
            )
        host = cast(str, params.get("host", "127.0.0.1"))
        port = cast(int, params.get("port", 10001))
        operation = cast(str, params.get("operation", "send"))
        outputs: dict[str, object] = {"sentBytes": len(outbound)}
        logger.info(
            "starting TCP client operation",
            payload={
                "host": host,
                "port": port,
                "operation": operation,
                "requestBytes": len(outbound),
            },
        )
        try:
            with openTcpClient(
                host,
                port,
                connectTimeoutSec=cast(int, params.get("connectTimeoutMs", 2000))
                / 1000.0,
                ioTimeoutSec=cast(int, params.get("responseTimeoutMs", 2000))
                / 1000.0,
            ) as sock:
                sendAll(sock, outbound)
                if bool(params.get("shutdownWrite", False)):
                    try:
                        sock.shutdown(socket.SHUT_WR)
                    except OSError as exc:
                        raise TcpIoError(f"TCP shutdown(SHUT_WR) failed: {exc}") from exc
                if operation == "exchange":
                    responseBytes = receiveFramed(
                        sock,
                        framing=cast(str, params.get("responseFraming", "newline")),
                        maxBytes=cast(int, params.get("maxResponseBytes", 1048576)),
                        expectedBytes=cast(int, params.get("expectedBytes", 0)),
                    )
                    outputs["response"] = TcpMessage.fromBytes(
                        responseBytes,
                        encoding=cast(str, params.get("responseEncoding", "utf-8")),
                        peerHost=host,
                        peerPort=port,
                    ).toPayload()
                    responseTextEncoding = cast(
                        str, params.get("responseTextEncoding", "none")
                    )
                    if responseTextEncoding != "none":
                        outputs["responseText"] = _decodeTcpText(
                            responseBytes, responseTextEncoding
                        )
        except Exception as exc:
            logger.error(
                "TCP client operation failed",
                code=getattr(exc, "code", "E_COMMUNICATION_IO"),
                payload={"errorType": type(exc).__name__},
            )
            return _communicationError(exc)
        latencyMs = round((perf_counter() - startedAt) * 1000.0, 3)
        logger.info(
            "TCP client operation completed",
            payload={
                "sentBytes": len(outbound),
                "receivedBytes": (
                    cast(dict[str, object], outputs["response"])["byteLength"]
                    if "response" in outputs
                    else 0
                ),
            },
        )
        return {
            "status": "ok",
            "outputs": outputs,
            "metrics": {
                "latencyMs": latencyMs,
                "sentBytes": len(outbound),
                "receivedBytes": (
                    cast(dict[str, object], outputs["response"])["byteLength"]
                    if "response" in outputs
                    else 0
                ),
            },
            "diagnostics": {
                "text": f"TCP {operation} completed with {host}:{port}"
            },
        }


class TcpReceiveOnceOperator:
    meta = OperatorMeta(
        operatorId="communication.tcp.receive_once",
        displayName="TCP Receive Once",
        version="1.1.0",
        inputPorts={},
        outputPorts={
            "message": {
                "type": "tcpMessage",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "peerHost": {"type": "string", "required": True, "nullable": False},
            "peerPort": {"type": "integer", "required": True, "nullable": False},
            "text": {"type": "string", "required": False, "nullable": False},
        },
        paramSchema=TCP_RECEIVE_ONCE_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        error = _validateTcpParams(
            params,
            allowed={
                "bindHost",
                "port",
                "acceptTimeoutMs",
                "readTimeoutMs",
                "framing",
                "expectedBytes",
                "maxMessageBytes",
                "messageEncoding",
                "messageTextEncoding",
                "ackText",
                "ackEncoding",
                "ackAppendNewline",
                "maxAckBytes",
            },
            hostName="bindHost",
            connectTimeoutName="acceptTimeoutMs",
            responseTimeoutName="readTimeoutMs",
        )
        if error is not None:
            return error
        framing = params.get("framing", "newline")
        if not isinstance(framing, str) or framing not in TCP_FRAMING_MODES:
            return _paramError("framing is not supported")
        expectedBytes = params.get("expectedBytes", 0)
        maxBytes = params.get("maxMessageBytes", 1048576)
        if not _isIntegerInRange(expectedBytes, 0, 16777216):
            return _paramError("expectedBytes must be between 0 and 16777216")
        if not _isIntegerInRange(maxBytes, 1, 16777216):
            return _paramError("maxMessageBytes must be between 1 and 16777216")
        if framing == "fixedLength" and not 1 <= cast(int, expectedBytes) <= cast(
            int, maxBytes
        ):
            return _paramError(
                "fixedLength framing requires expectedBytes between 1 and maxMessageBytes"
            )
        for name, default in (
            ("messageEncoding", "utf-8"),
            ("ackEncoding", "utf-8"),
        ):
            encoding = params.get(name, default)
            if not isinstance(encoding, str) or encoding not in TCP_MESSAGE_ENCODINGS:
                return _paramError(f"{name} is not supported")
        messageTextEncoding = params.get("messageTextEncoding", "none")
        if not isinstance(messageTextEncoding, str) or messageTextEncoding not in {
            "none",
            "utf-8",
            "ascii",
            "latin-1",
        }:
            return _paramError("messageTextEncoding is not supported")
        if not isinstance(params.get("ackText", ""), str):
            return _paramError("ackText must be a string")
        if not isinstance(params.get("ackAppendNewline", True), bool):
            return _paramError("ackAppendNewline must be boolean")
        if not _isIntegerInRange(params.get("maxAckBytes", 1048576), 1, 16777216):
            return _paramError("maxAckBytes must be between 1 and 16777216")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = inputs
        logger = getOperatorLogger(runtimeContext)
        startedAt = perf_counter()
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        ackText = cast(str, params.get("ackText", ""))
        bindHost = cast(str, params.get("bindHost", "127.0.0.1"))
        port = cast(int, params.get("port", 10001))
        logger.info(
            "waiting for one TCP message",
            payload={"bindHost": bindHost, "port": port},
        )
        try:
            ack = (
                _encodeTcpData(
                    ackText,
                    cast(str, params.get("ackEncoding", "utf-8")),
                )
                if ackText
                else None
            )
            if ack is not None and bool(params.get("ackAppendNewline", True)):
                ack += b"\n"
            maxAckBytes = cast(int, params.get("maxAckBytes", 1048576))
            if ack is not None and len(ack) > maxAckBytes:
                raise ValueError(f"TCP acknowledgement exceeds maxAckBytes={maxAckBytes}")
            messageBytes, peer = listenOnce(
                bindHost,
                port,
                acceptTimeoutSec=cast(int, params.get("acceptTimeoutMs", 5000))
                / 1000.0,
                ioTimeoutSec=cast(int, params.get("readTimeoutMs", 1000)) / 1000.0,
                framing=cast(str, params.get("framing", "newline")),
                maxBytes=cast(int, params.get("maxMessageBytes", 1048576)),
                expectedBytes=cast(int, params.get("expectedBytes", 0)),
                ack=ack,
            )
            message = TcpMessage.fromBytes(
                messageBytes,
                encoding=cast(str, params.get("messageEncoding", "utf-8")),
                peerHost=peer[0],
                peerPort=peer[1],
            ).toPayload()
            textEncoding = cast(str, params.get("messageTextEncoding", "none"))
            textValue = (
                _decodeTcpText(messageBytes, textEncoding)
                if textEncoding != "none"
                else None
            )
        except Exception as exc:
            logger.error(
                "TCP receive failed",
                code=getattr(exc, "code", "E_COMMUNICATION_IO"),
                payload={"errorType": type(exc).__name__},
            )
            return _communicationError(exc)
        latencyMs = round((perf_counter() - startedAt) * 1000.0, 3)
        outputs: dict[str, object] = {
            "message": message,
            "peerHost": peer[0],
            "peerPort": peer[1],
        }
        if textValue is not None:
            outputs["text"] = textValue
        logger.info(
            "TCP message received",
            payload={
                "peerHost": peer[0],
                "peerPort": peer[1],
                "receivedBytes": len(messageBytes),
                "ackBytes": len(ack) if ack is not None else 0,
            },
        )
        return {
            "status": "ok",
            "outputs": outputs,
            "metrics": {"latencyMs": latencyMs, "receivedBytes": len(messageBytes)},
            "diagnostics": {
                "text": f"Received one TCP message from {peer[0]}:{peer[1]}"
            },
        }


def _validatePlcCommonParams(
    params: dict[str, object],
    extraNames: set[str],
) -> dict[str, str] | None:
    allowed = set(_PLC_COMMON_PROPERTIES) | extraNames
    unknown = sorted(set(params) - allowed)
    if unknown:
        return _paramError(f"unknown params: {', '.join(unknown)}")
    host = params.get("host", "127.0.0.1")
    if not isinstance(host, str) or not host.strip():
        return _paramError("host must be a non-empty string")
    if not _isIntegerInRange(params.get("port", 10001), 1, 65535):
        return _paramError("port must be an integer between 1 and 65535")
    device = params.get("device", "D")
    if not isinstance(device, str) or device.upper() not in {"D", "M"}:
        return _paramError("device must be D or M")
    if not _isIntegerInRange(params.get("startAddress", 0), 0, 0xFFFFFF):
        return _paramError("startAddress must be within the 24-bit device range")
    for name in ("connectTimeoutMs", "responseTimeoutMs"):
        if not _isIntegerInRange(params.get(name, 2000), 1, 60000):
            return _paramError(f"{name} must be between 1 and 60000")
    if not _isIntegerInRange(params.get("retryCount", 1), 0, 3):
        return _paramError("retryCount must be between 0 and 3")
    if not _isIntegerInRange(params.get("retryDelayMs", 0), 0, 10000):
        return _paramError("retryDelayMs must be between 0 and 10000")
    for name, maximum, default in (
        ("networkNo", 0xFF, 0),
        ("pcNo", 0xFF, 0xFF),
        ("moduleIoNo", 0xFFFF, 0x03FF),
        ("moduleStationNo", 0xFF, 0),
        ("monitoringTimer", 0xFFFF, 0),
    ):
        if not _isIntegerInRange(params.get(name, default), 0, maximum):
            return _paramError(f"{name} must be between 0 and {maximum}")
    return None


def _validateTcpParams(
    params: dict[str, object],
    *,
    allowed: set[str],
    hostName: str = "host",
    connectTimeoutName: str = "connectTimeoutMs",
    responseTimeoutName: str = "responseTimeoutMs",
) -> dict[str, str] | None:
    unknown = sorted(set(params) - allowed)
    if unknown:
        return _paramError(f"unknown params: {', '.join(unknown)}")
    host = params.get(hostName, "127.0.0.1")
    if not isinstance(host, str) or not host.strip():
        return _paramError(f"{hostName} must be a non-empty string")
    if not _isIntegerInRange(params.get("port", 10001), 1, 65535):
        return _paramError("port must be an integer between 1 and 65535")
    for name, default in ((connectTimeoutName, 2000), (responseTimeoutName, 2000)):
        if not _isIntegerInRange(params.get(name, default), 1, 60000):
            return _paramError(f"{name} must be between 1 and 60000")
    return None


def _slmpEndpoint(params: dict[str, object]) -> SlmpEndpoint:
    return SlmpEndpoint(
        host=cast(str, params.get("host", "127.0.0.1")),
        port=cast(int, params.get("port", 10001)),
        connectTimeoutSec=cast(int, params.get("connectTimeoutMs", 2000)) / 1000.0,
        responseTimeoutSec=cast(int, params.get("responseTimeoutMs", 2000))
        / 1000.0,
        networkNo=cast(int, params.get("networkNo", 0)),
        pcNo=cast(int, params.get("pcNo", 0xFF)),
        moduleIoNo=cast(int, params.get("moduleIoNo", 0x03FF)),
        moduleStationNo=cast(int, params.get("moduleStationNo", 0)),
        monitoringTimer=cast(int, params.get("monitoringTimer", 0)),
    )


def _plcWriteSource(
    inputs: dict[str, object],
    params: dict[str, object],
) -> tuple[str, int, str, list[object]]:
    paramValues = cast(list[object], params.get("values", []))
    sources = [name for name in ("data", "value", "values") if name in inputs]
    if paramValues:
        sources.append("params.values")
    if not sources:
        raise ValueError("one of data, value, values, or params.values is required")
    if len(sources) != 1:
        raise ValueError("exactly one PLC write value source must be provided")
    source = sources[0]
    if source == "data":
        collection = PlcValueCollection.fromPayload(inputs["data"])
        if collection.dataType == "bit":
            raise ValueError("bit collections cannot be written with the word write node")
        return (
            collection.device,
            collection.startAddress,
            collection.dataType,
            list(collection.values),
        )
    device = cast(str, params.get("device", "D")).upper()
    startAddress = cast(int, params.get("startAddress", 0))
    dataType = cast(str, params.get("dataType", "uint16"))
    if source == "value":
        sourceValues = [inputs["value"]]
    elif source == "values":
        rawValues = inputs["values"]
        if not isinstance(rawValues, list):
            raise TypeError("values input must be an array")
        sourceValues = rawValues
    else:
        sourceValues = paramValues
    if not sourceValues:
        raise ValueError("PLC write values cannot be empty")
    return device, startAddress, dataType, sourceValues


def _tcpClientSource(
    inputs: dict[str, object],
    params: dict[str, object],
) -> bytes:
    paramText = cast(str, params.get("text", ""))
    sources = [name for name in ("message", "text") if name in inputs]
    if paramText:
        sources.append("params.text")
    if not sources:
        raise ValueError("one of message, text, or params.text is required")
    if len(sources) != 1:
        raise ValueError("exactly one TCP message source must be provided")
    source = sources[0]
    if source == "message":
        return TcpMessage.fromPayload(inputs["message"]).toBytes()
    text = inputs["text"] if source == "text" else paramText
    if not isinstance(text, str):
        raise TypeError("text input must be a string")
    return _encodeTcpData(text, cast(str, params.get("textEncoding", "utf-8")))


def _encodeTcpData(value: str, encoding: str) -> bytes:
    try:
        if encoding == "hex":
            return bytes.fromhex(value)
        if encoding == "base64":
            return base64.b64decode(value, validate=True)
        if encoding not in {"utf-8", "ascii", "latin-1"}:
            raise ValueError(f"unsupported encoding: {encoding!r}")
        return value.encode(encoding)
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise ValueError(f"text is not valid {encoding}: {exc}") from exc


def _decodeTcpText(value: bytes, encoding: str) -> str:
    try:
        return value.decode(encoding)
    except UnicodeDecodeError as exc:
        raise CommunicationPayloadValidationError(
            f"TCP response is not valid {encoding}: {exc}"
        ) from exc


def _communicationError(exc: Exception) -> dict[str, object]:
    if isinstance(exc, (SlmpTimeoutError, TcpTimeoutError)):
        return _error("E_COMM_TIMEOUT", str(exc))
    if isinstance(exc, (SlmpResponseError,)):
        return _error("E_PLC_RESPONSE", str(exc))
    if isinstance(exc, (TcpFrameError, SlmpProtocolError)):
        return _error("E_COMM_PROTOCOL", str(exc))
    if isinstance(exc, TcpConnectError):
        return _error("E_COMM_CONNECT_FAILED", str(exc))
    if isinstance(exc, SlmpConnectionError):
        return _error("E_COMM_CONNECT_FAILED", str(exc))
    if isinstance(exc, TcpIoError):
        return _error("E_COMM_IO", str(exc))
    if isinstance(exc, CommunicationPayloadValidationError):
        return _error("E_RESULT_INVALID", str(exc))
    if isinstance(exc, (TypeError, ValueError)):
        return _error("E_INPUT_SHAPE", str(exc))
    return _error("E_EXEC_FAILED", f"communication operation failed: {exc}")


def _delay(delayMs: int) -> None:
    if delayMs > 0:
        time.sleep(delayMs / 1000.0)


def _isIntegerInRange(value: object, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _isFiniteNumber(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, object]:
    return {"status": "error", "error": {"code": code, "message": message}}
