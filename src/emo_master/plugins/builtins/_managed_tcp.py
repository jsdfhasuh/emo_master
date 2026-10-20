"""Workflow access to Runtime-owned communication. No socket ownership here."""
from __future__ import annotations

from dataclasses import dataclass
import math

from emo_master.core.contracts.tcp_service import TcpServiceError, TcpSessionRef
from emo_master.plugins.builtins._communication_operators import _encodeTcpData


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict
    outputPorts: dict
    paramSchema: dict


def ports(**types):
    return {key: dict(type=value, required=True, nullable=False,
                      **({"schemaVersion": "1.x"} if value in {"tcpSessionRef", "tcpSendReceipt"} else {}))
            for key, value in types.items()}


class ManagedOperator:
    def validateParams(self, params):
        from emo_master.core.project.snapshots import _parameters
        try:
            _parameters(params, self.meta.paramSchema, "params")
        except (ValueError, TypeError) as error:
            return dict(code="E_PARAM_INVALID", message=str(error))
        return None

    def executeNode(self, inputs, params, runtimeContext):
        try:
            error = self.validateParams(params)
            if error:
                raise ValueError(str(error))
            result = self.execute(inputs, params, runtimeContext)
            return dict(status="ok", outputs=result,
                        diagnostics={"tcpReceipt": result["receipt"]} if "receipt" in result else {})
        except Exception as error:
            # Cancellation may win before the parent replies, in which case the
            # proxy observes a closed pipe rather than E_CANCELLED. Use the live
            # Job token, not the transport error code, to preserve ABORTED while
            # retaining a send's possibly-unknown outcome in node diagnostics.
            check = runtimeContext.get("raiseIfCancellationRequested")
            if callable(check):
                try:
                    check()
                except Exception as cancelled:
                    cancelled.diagnostics = getattr(error, "diagnostics", {})
                    raise
            return dict(status="error", error=dict(code=getattr(error, "code", "E_PARAM_INVALID"), message=str(error)),
                        diagnostics=getattr(error, "diagnostics", {}))

    @staticmethod
    def device(context):
        proxy = context.get("executeDevice")
        if proxy is None:
            raise TcpServiceError("E_TCP_UNSUPPORTED", "此执行环境未提供托管 TCP 服务，不支持生产连接的孤立调试")
        return proxy


class TcpSessionOperator(ManagedOperator):
    meta = OperatorMeta("communication.tcp.session", "TCP 通道会话", "1.0.0", {}, ports(session="tcpSessionRef"),
        dict(type="object", additionalProperties=False, properties={}, required=[], xDeviceActionFields=[]))

    def execute(self, inputs, params, context):
        return dict(session=self.device(context)("tcp", "acquire_session", {}))


class TcpSessionSendOperator(ManagedOperator):
    meta = OperatorMeta("communication.tcp.session_send", "TCP 会话发送", "1.0.0",
        ports(session="tcpSessionRef", data="string"), ports(receipt="tcpSendReceipt"),
        dict(type="object", additionalProperties=False, properties={}, required=[], xDeviceActionFields=[]))

    def execute(self, inputs, params, context):
        session = TcpSessionRef.fromPayload(inputs["session"]).toPayload()
        data = _encodeTcpData(inputs["data"], context["deviceParameters"]["tcp"]["encoding"])
        if context["deviceParameters"]["tcp"]["appendNewline"]:
            data += b"\n"
        if len(data) > 16777216:
            raise TcpServiceError("E_TCP_MESSAGE_TOO_LARGE", "报文超过 16777216 字节传输上限")
        import base64
        receipt = self.device(context)("tcp", "send", dict(session=session, dataBase64=base64.b64encode(data).decode("ascii")))
        if receipt["outcome"] != "SENT_TO_TRANSPORT":
            raise TcpServiceError(receipt["code"] or "E_TCP_SEND_FAILED", receipt["message"], receipt)
        return dict(receipt=receipt)


class CoordinateMessageFormatOperator(ManagedOperator):
    meta = OperatorMeta("communication.coordinate_message_format", "坐标报文格式化", "1.0.0",
        ports(x="number", y="number"), ports(data="string"),
        dict(type="object", additionalProperties=False, properties={
            "decimals": dict(title="舍入小数位数", type="integer", minimum=0, maximum=9, default=3),
            "numberFormat": dict(title="数值表示", type="string", enum=["round", "fixed"], default="round"),
            "quote": dict(title="包围引号", type="string", enum=["single", "double", "none"], default="single"),
            "suffix": dict(title="报文后缀", type="string", default="EY", maxLength=80)}))

    def execute(self, inputs, params, context):
        values = [inputs["x"], inputs["y"]]
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
            raise ValueError("坐标必须是有限数值")
        decimals = params.get("decimals", 3)
        text = ",".join(format(value, f".{decimals}f") if params.get("numberFormat", "round") == "fixed"
                        else str(round(value, decimals)) for value in values)
        quote = {"single": "'", "double": '"', "none": ""}[params.get("quote", "single")]
        return dict(data=quote + text + params.get("suffix", "EY") + quote)
