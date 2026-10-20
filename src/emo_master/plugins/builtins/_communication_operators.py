from __future__ import annotations

import base64
import binascii
import math
import time
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

from emo_master.core.contracts.communication import (
    PLC_DATA_TYPES,
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
    TcpConnectError,
    TcpFrameError,
    TcpIoError,
    TcpTimeoutError,
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
    "frameEncoding": {"title": "通信格式", "type": "string", "enum": ["binary", "ascii"],
                      "default": "binary", "xOptionLabels": {"binary": "MC 3E · 二进制（Binary）", "ascii": "MC 3E · ASCII"}},
    "host": {"title": "主机地址", "type": "string", "default": "127.0.0.1"},
    "port": {
        "title": "端口",
        "type": "integer",
        "minimum": 1,
        "maximum": 65535,
        "default": 10001,
    },
    "device": {"title": "PLC 软元件", "type": "string", "enum": ["D", "M", "d", "m"], "default": "D"},
    "startAddress": {
        "title": "起始地址",
        "type": "integer",
        "minimum": 0,
        "maximum": 16777215,
        "default": 0,
    },
    "connectTimeoutMs": {
        "title": "连接超时（毫秒）",
        "type": "integer",
        "minimum": 1,
        "maximum": 60000,
        "default": 2000,
    },
    "responseTimeoutMs": {
        "title": "响应超时（毫秒）",
        "type": "integer",
        "minimum": 1,
        "maximum": 60000,
        "default": 2000,
    },
    "retryCount": {
        "title": "异常重试次数",
        "type": "integer",
        "minimum": 0,
        "maximum": 3,
        "default": 1,
    },
    "retryDelayMs": {
        "title": "重试间隔（毫秒）",
        "type": "integer",
        "minimum": 0,
        "maximum": 10000,
        "default": 0,
    },
    "networkNo": {
        "title": "网络编号",
        "type": "integer",
        "minimum": 0,
        "maximum": 255,
        "default": 0,
    },
    "pcNo": {
        "title": "PC 编号",
        "type": "integer",
        "minimum": 0,
        "maximum": 255,
        "default": 255,
    },
    "moduleIoNo": {
        "title": "模块 I/O 编号",
        "type": "integer",
        "minimum": 0,
        "maximum": 65535,
        "default": 1023,
    },
    "moduleStationNo": {
        "title": "模块站号",
        "type": "integer",
        "minimum": 0,
        "maximum": 255,
        "default": 0,
    },
    "monitoringTimer": {
        "title": "监视定时器（250 毫秒单位）",
        "type": "integer",
        "minimum": 0,
        "maximum": 65535,
        "default": 0,
    },
}

PLC_READ_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "xDeviceActionFields": ["device", "startAddress", "dataType", "count", "outputIndex"],
    "properties": {
        **_PLC_COMMON_PROPERTIES,
        "dataType": {
            "title": "数据类型",
            "type": "string",
            "enum": ["uint16", "int16", "uint32", "int32", "float32", "bit"],
            "default": "uint16",
        },
        "count": {
            "title": "数量",
            "type": "integer",
            "minimum": 1,
            "maximum": 960,
            "default": 1,
        },
        "outputIndex": {
            "title": "输出值索引",
            "type": "integer",
            "minimum": 0,
            "maximum": MAX_WORDS_PER_REQUEST - 1,
            "default": 0,
        },
    },
}

PLC_WRITE_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "xDeviceActionFields": ["device", "startAddress", "dataType", "values"],
    "properties": {
        **_PLC_COMMON_PROPERTIES,
        "retryCount": {
            "title": "异常重试次数",
            "type": "integer",
            "minimum": 0,
            "maximum": 3,
            "default": 0,
        },
        "dataType": {
            "title": "数据类型",
            "type": "string",
            "enum": ["uint16", "int16", "uint32", "int32", "float32"],
            "default": "uint16",
        },
        "values": {
            "title": "写入值",
            "type": "array",
            "items": {"type": "number"},
            "default": [],
        },
    },
}

TCP_CLIENT_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "host": {"title": "主机地址", "type": "string", "default": "127.0.0.1"},
        "port": {
            "title": "端口",
            "type": "integer",
            "minimum": 1,
            "maximum": 65535,
            "default": 10001,
        },
        "operation": {
            "title": "操作类型",
            "type": "string",
            "enum": ["send", "exchange"],
            "default": "send",
        },
        "text": {"title": "发送文本", "type": "string", "default": ""},
        "textEncoding": {
            "title": "发送文本编码",
            "type": "string",
            "enum": ["utf-8", "ascii", "latin-1", "hex", "base64"],
            "default": "utf-8",
        },
        "appendNewline": {"title": "追加换行", "type": "boolean", "default": False},
        "rejectTrailingResponse": {"title": "拒绝响应帧后的残留数据", "type": "boolean", "default": False,
                                   "description": "单次换行事务中，拒绝同一接收块内多余报文；连接仍按事务关闭。"},
        "shutdownWrite": {"title": "发送后关闭写入方向", "type": "boolean", "default": False},
        "connectTimeoutMs": {
            "title": "连接超时（毫秒）",
            "type": "integer",
            "minimum": 1,
            "maximum": 60000,
            "default": 2000,
        },
        "responseTimeoutMs": {
            "title": "响应超时（毫秒）",
            "type": "integer",
            "minimum": 1,
            "maximum": 60000,
            "default": 2000,
        },
        "responseFraming": {
            "title": "响应分帧方式",
            "type": "string",
            "enum": ["newline", "quoted", "idle", "fixedLength"],
            "default": "newline",
        },
        "expectedBytes": {
            "title": "预期字节数",
            "type": "integer",
            "minimum": 0,
            "maximum": 16777216,
            "default": 0,
        },
        "maxResponseBytes": {
            "title": "最大响应大小（字节）",
            "type": "integer",
            "minimum": 1,
            "maximum": 16777216,
            "default": 1048576,
        },
        "maxRequestBytes": {
            "title": "最大发送大小（字节）",
            "type": "integer",
            "minimum": 1,
            "maximum": 16777216,
            "default": 1048576,
        },
        "responseEncoding": {
            "title": "响应内容编码",
            "type": "string",
            "enum": ["utf-8", "ascii", "latin-1", "hex", "base64"],
            "default": "utf-8",
        },
        "responseTextEncoding": {
            "title": "响应文本编码",
            "type": "string",
            "enum": ["none", "utf-8", "ascii", "latin-1"],
            "default": "none",
        },
    },
}

TCP_RECEIVE_ONCE_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "bindHost": {"title": "监听地址", "type": "string", "default": "127.0.0.1"},
        "port": {
            "title": "端口",
            "type": "integer",
            "minimum": 1,
            "maximum": 65535,
            "default": 10001,
        },
        "acceptTimeoutMs": {
            "title": "等待连接超时（毫秒）",
            "type": "integer",
            "minimum": 1,
            "maximum": 60000,
            "default": 5000,
        },
        "readTimeoutMs": {
            "title": "读取超时（毫秒）",
            "type": "integer",
            "minimum": 1,
            "maximum": 60000,
            "default": 1000,
        },
        "framing": {
            "title": "消息分帧方式",
            "type": "string",
            "enum": ["newline", "quoted", "idle", "fixedLength"],
            "default": "newline",
        },
        "expectedBytes": {
            "title": "预期字节数",
            "type": "integer",
            "minimum": 0,
            "maximum": 16777216,
            "default": 0,
        },
        "maxMessageBytes": {
            "title": "最大消息大小（字节）",
            "type": "integer",
            "minimum": 1,
            "maximum": 16777216,
            "default": 1048576,
        },
        "messageEncoding": {
            "title": "消息内容编码",
            "type": "string",
            "enum": ["utf-8", "ascii", "latin-1", "hex", "base64"],
            "default": "utf-8",
        },
        "messageTextEncoding": {
            "title": "消息文本编码",
            "type": "string",
            "enum": ["none", "utf-8", "ascii", "latin-1"],
            "default": "none",
        },
        "ackText": {"title": "应答文本", "type": "string", "default": ""},
        "ackEncoding": {
            "title": "应答文本编码",
            "type": "string",
            "enum": ["utf-8", "ascii", "latin-1", "hex", "base64"],
            "default": "utf-8",
        },
        "ackAppendNewline": {"title": "应答追加换行", "type": "boolean", "default": True},
        "maxAckBytes": {
            "title": "最大应答大小（字节）",
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
        if params.get("frameEncoding", "binary") == "ascii" and startAddress + plcDeviceAddressSpan(device, wordCount) - 1 > 999999:
            return _paramError("ASCII address range must be within 0..999999")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        if callable(runtimeContext.get("executeDevice")):
            return _executeManagedPlc(self, "read", inputs, params, runtimeContext)
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
        if callable(runtimeContext.get("executeDevice")):
            return _executeManagedPlc(self, "write", inputs, params, runtimeContext)
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
            if params.get("frameEncoding", "binary") == "ascii" and startAddress + plcDeviceAddressSpan(device, len(words)) - 1 > 999999:
                raise ValueError("ASCII write address range must be within 0..999999")
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


def _executeManagedPlc(operator, action, inputs, params, context):
    """Typed legacy ports over the managed Runtime channel, never a local socket."""
    from emo_master.core.project.snapshots import _parameters

    started = perf_counter()
    schema = operator.meta.paramSchema
    fields = schema["xDeviceActionFields"]
    actionSchema = dict(type="object", additionalProperties=False,
                        properties={key: schema["properties"][key] for key in fields})
    outcome = "NOT_EXECUTED"
    try:
        checked = _parameters(dict(params), actionSchema, "PLC action parameters")
        # Schema numbers normalize to float; integer PLC codecs require the
        # original integral type, not a lossy round trip through float.
        if "values" in params:
            checked["values"] = params["values"]
        params = checked
        error = operator.validateParams(params)
        if error:
            return {"status": "error", "error": error}
        if action == "read":
            device = params["device"].upper()
            address, dataType = params["startAddress"], params["dataType"]
            count = params["count"] * plcWordsPerValue(dataType)
            outcome = "UNKNOWN"
            reply = context["executeDevice"]("plc", "read", dict(device=device, startAddress=address, count=count))
            outcome = reply["outcome"]
            values = decodePlcWords(reply["words"], dataType)
            outputs = {"values": PlcValueCollection(device=device, startAddress=address, dataType=dataType,
                        values=values, wordCount=count).toPayload(),
                       "booleanValue" if dataType == "bit" else "numberValue": values[params["outputIndex"]]}
        else:
            device, address, dataType, values = _plcWriteSource(inputs, params)
            words = encodePlcValues(values, dataType)
            count = len(words)
            outcome = "UNKNOWN"
            reply = context["executeDevice"]("plc", "write", dict(device=device, startAddress=address, words=words))
            outcome = reply["outcome"]
            outputs = {"receipt": PlcWriteReceipt(device=device, startAddress=address, dataType=dataType,
                        valueCount=len(values), wordCount=count, attempts=1).toPayload()}
        return dict(status="ok", outputs=outputs,
                    metrics=dict(attempts=1, wordCount=count, valueCount=len(values),
                                 latencyMs=round((perf_counter() - started) * 1000, 3)),
                    diagnostics={"deviceOperation": {"action": action, "outcome": reply["outcome"],
                                 "connectionGeneration": reply["connectionGeneration"]}})
    except Exception as error:
        return dict(status="error", error=dict(code=getattr(error, "code", "E_PARAM_INVALID"), message=str(error)),
                    diagnostics={"deviceOperation": {"action": action, "outcome": getattr(error, "outcome", outcome)}})


TCP_CLIENT_INPUT_PORTS: dict[str, object] = {'message': {'type': 'tcpMessage', 'required': False, 'nullable': False, 'schemaVersion': '1.x'}, 'text': {'type': 'string', 'required': False, 'nullable': False}}
TCP_CLIENT_OUTPUT_PORTS: dict[str, object] = {'response': {'type': 'tcpMessage', 'required': False, 'nullable': False, 'schemaVersion': '1.x'}, 'sentBytes': {'type': 'integer', 'required': True, 'nullable': False}, 'responseText': {'type': 'string', 'required': False, 'nullable': False}}
TCP_CLIENT_NODE_SCHEMA = {'type': 'object', 'properties': {'operation': {'title': '操作类型', 'type': 'string', 'enum': ['send', 'exchange'], 'default': 'send'}, 'text': {'title': '发送文本', 'type': 'string', 'default': ''}}, 'additionalProperties': False, 'required': [], 'xDeviceActionFields': ['operation', 'text']}
TCP_RECEIVE_OUTPUT_PORTS: dict[str, object] = {'message': {'type': 'tcpMessage', 'required': True, 'nullable': False, 'schemaVersion': '1.x'}, 'peerHost': {'type': 'string', 'required': True, 'nullable': False}, 'peerPort': {'type': 'integer', 'required': True, 'nullable': False}, 'text': {'type': 'string', 'required': False, 'nullable': False}}
TCP_RECEIVE_NODE_SCHEMA = {'type': 'object', 'properties': {}, 'additionalProperties': False, 'required': [], 'xDeviceActionFields': []}


class TcpClientOperator:
    meta = OperatorMeta(operatorId="communication.tcp.client", displayName="TCP Client", version="1.2.0",
        inputPorts=TCP_CLIENT_INPUT_PORTS, outputPorts=TCP_CLIENT_OUTPUT_PORTS,
        paramSchema=TCP_CLIENT_NODE_SCHEMA)

    def validateParams(self, params):
        from emo_master.core.project.snapshots import _parameters
        try:
            _parameters(params, self.meta.paramSchema, "params")
        except ValueError as error:
            return _paramError(str(error))
        return None

    def executeNode(self, inputs, params, runtimeContext):
        try:
            execute = runtimeContext.get('executeDevice')
            if execute is None:
                return _error('E_DEVICE_RUNTIME_UNAVAILABLE', '请绑定工程 TCP 设备并从 Runtime 运行')
            work = runtimeContext['deviceParameters']['tcp']
            data = _tcpClientSource(inputs, dict(params, textEncoding=work['textEncoding']))
            if work['appendNewline']:
                data += b'\n'
            result = execute('tcp', params.get('operation', 'send'),
                             {'dataBase64': base64.b64encode(data).decode('ascii')})
            outputs = {'sentBytes': result['sentBytes']}
            if 'dataBase64' in result:
                raw = base64.b64decode(result['dataBase64'], validate=True)
                outputs['response'] = TcpMessage.fromBytes(raw, encoding=work['responseEncoding'],
                    peerHost=result['peerHost'], peerPort=result['peerPort']).toPayload()
                if work['responseTextEncoding'] != 'none':
                    outputs['responseText'] = _decodeTcpText(raw, work['responseTextEncoding'])
            return dict(status='ok', outputs=outputs)
        except Exception as error:
            return dict(status='error', error=dict(code=getattr(error, 'code', 'E_TCP_IO'), message=str(error)),
                        diagnostics=getattr(error, 'diagnostics', {}))


class TcpReceiveOnceOperator:
    meta = OperatorMeta(operatorId="communication.tcp.receive_once", displayName="TCP Receive Once", version="1.1.0",
        inputPorts={}, outputPorts=TCP_RECEIVE_OUTPUT_PORTS, paramSchema=TCP_RECEIVE_NODE_SCHEMA)

    def validateParams(self, params):
        return _paramError('此节点的工作参数由设备继承') if params else None

    def executeNode(self, inputs, params, runtimeContext):
        try:
            execute = runtimeContext.get('executeDevice')
            if execute is None:
                return _error('E_DEVICE_RUNTIME_UNAVAILABLE', '请绑定工程 TCP 设备并从 Runtime 运行')
            result = execute('tcp', 'receive', {})
            work = runtimeContext['deviceParameters']['tcp']
            raw = base64.b64decode(result['dataBase64'], validate=True)
            outputs = dict(message=TcpMessage.fromBytes(raw, encoding=work['messageEncoding'],
                peerHost=result['peerHost'], peerPort=result['peerPort']).toPayload(),
                peerHost=result['peerHost'], peerPort=result['peerPort'])
            if work['messageTextEncoding'] != 'none':
                outputs['text'] = _decodeTcpText(raw, work['messageTextEncoding'])
            return dict(status='ok', outputs=outputs)
        except Exception as error:
            return dict(status='error', error=dict(code=getattr(error, 'code', 'E_TCP_IO'), message=str(error)),
                        diagnostics=getattr(error, 'diagnostics', {}))


def _validatePlcCommonParams(
    params: dict[str, object],
    extraNames: set[str],
) -> dict[str, str] | None:
    allowed = set(_PLC_COMMON_PROPERTIES) | extraNames
    unknown = sorted(set(params) - allowed)
    if unknown:
        return _paramError(f"unknown params: {', '.join(unknown)}")
    if params.get("frameEncoding", "binary") not in ("binary", "ascii"):
        return _paramError("frameEncoding must be binary or ascii")
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
    if params.get("frameEncoding", "binary") == "ascii" and cast(int, params.get("startAddress", 0)) > 999999:
        return _paramError("ASCII startAddress must be within 0..999999")
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
        frameEncoding=cast(str, params.get("frameEncoding", "binary")),
    )


def _plcWriteSource(
    inputs: dict[str, object],
    params: dict[str, object],
) -> tuple[str, int, str, list[object]]:
    paramValues = cast(list[object], params.get("values", []))
    sources: list[str] = [
        name for name in ("data", "value", "values") if name in inputs
    ]
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
    sources: list[str] = [name for name in ("message", "text") if name in inputs]
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
