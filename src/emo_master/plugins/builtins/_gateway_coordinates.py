"""Normal coordinate input and correlated ACKs for the observed gateway protocol.

The gateway appends EY itself. PLC ACKs confirm X magnitude/sign only: they
cannot establish any Y write or physical completion. No NG or retry policy.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP
import re

from emo_master.core.contracts.communication import TcpMessage, CommunicationPayloadValidationError
from emo_master.core.contracts.geometry2d import Point2D, PayloadValidationError
from emo_master.plugins.builtins._geometry_bridges import OperatorMeta, error, port


CHANNEL = {"title": "网关输入通道", "type": "string", "enum": ["plc", "robot"], "default": "robot",
           "xGlobalVariableBindingDisabled": True, "xOptionLabels": {"plc": "PLC 正常坐标", "robot": "机器人正常坐标"}}


def _validateChannel(params, allowed):
    if set(params) - allowed or not isinstance(params.get("channel", "robot"), str) or params.get("channel", "robot") not in {"plc", "robot"}:
        return {"code": "E_PARAM_INVALID", "message": "unsupported gateway channel/parameters"}
    return None


def _requestAck(request, channel):
    """Validate canonical outgoing bytes, then derive this transaction's ACK."""
    text = request.decode("ascii")
    pattern = r'"(-?(?:0|[1-9][0-9]*)),(-?(?:0|[1-9][0-9]*))"' if channel == "plc" else r'"(-?(?:0|[1-9][0-9]*)\.[0-9]{2}),(-?(?:0|[1-9][0-9]*)\.[0-9]{2})"'
    matched = re.fullmatch(pattern, text)
    if matched is None:
        raise ValueError("request must contain exactly one quoted canonical coordinate pair, without newline or EY")
    if channel == "robot":
        return f"OK '{matched[1]},{matched[2]}EY'"
    x = int(matched[1])
    if abs(x) > 65535:
        raise ValueError("PLC X magnitude exceeds the observed D6 uint16 range")
    return f"OK D7={2 if x < 0 else 1} D6={abs(x)}"


class GatewayCoordinateFormatOperator:
    meta = OperatorMeta("communication.gateway.coordinate_format", "网关正常坐标报文", "1.0.0",
        {"points": port("list<point2d>")},
        {"message": port("tcpMessage"), "text": {"type": "string", "required": True, "nullable": False},
         "expectedAck": {"type": "string", "required": True, "nullable": False}},
        {"type": "object", "additionalProperties": False, "properties": {
            "channel": CHANNEL,
            "plcRounding": {"title": "PLC 整数规则", "type": "string", "default": "requireInteger",
                "enum": ["requireInteger", "halfAwayFromZero", "towardZero", "floor", "ceil"],
                "xEnabledWhen": {"channel": ["plc"]},
                "xOptionLabels": {"requireInteger": "拒绝非整数", "halfAwayFromZero": "半值向远离零方向舍入", "towardZero": "向零截断", "floor": "向下取整", "ceil": "向上取整"}}}})

    def validateParams(self, params):
        validation = _validateChannel(params, {"channel", "plcRounding"})
        if validation:
            return validation
        if params.get("plcRounding", "requireInteger") not in self.meta.paramSchema["properties"]["plcRounding"]["enum"]:
            return {"code": "E_PARAM_INVALID", "message": "unsupported PLC rounding rule"}
        return None

    def executeNode(self, inputs, params, runtimeContext):
        validation = self.validateParams(params)
        if validation:
            return {"status": "error", "error": validation}
        values = inputs.get("points")
        if not isinstance(values, list) or len(values) != 1:
            return error("E_INPUT_SHAPE", "one coordinate pair per transaction is required; select/order multi-targets explicitly")
        try:
            point = Point2D.fromPayload(values[0])
            channel = params.get("channel", "robot")
            numbers = [Decimal(str(value)) for value in (point.x, point.y)]
            if channel == "plc":
                mode = params.get("plcRounding", "requireInteger")
                if mode == "requireInteger" and any(n != n.to_integral_value() for n in numbers):
                    return error("E_INPUT_SHAPE", "PLC coordinates must be integers under requireInteger")
                rounding = {"requireInteger": ROUND_DOWN, "halfAwayFromZero": ROUND_HALF_UP,
                            "towardZero": ROUND_DOWN, "floor": ROUND_FLOOR, "ceil": ROUND_CEILING}[mode]
                text = '"' + ",".join(str(int(n.to_integral_value(rounding=rounding))) for n in numbers) + '"'
            else:
                # Deterministic decimal half-away rounding; normalize negative zero.
                rounded = [n.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for n in numbers]
                text = '"' + ",".join(format(n if n else Decimal("0.00"), ".2f") for n in rounded) + '"'
            request = text.encode("ascii")
            ack = _requestAck(request, channel)
            return {"status": "ok", "outputs": {"message": TcpMessage.fromBytes(request, encoding="ascii").toPayload(),
                    "text": text, "expectedAck": ack},
                    "diagnostics": {"channel": channel, "robotRounding": "decimalHalfAwayFromZero",
                                    "coordinateUnit": point.coordinateSpace.unit, "unitConversion": False}}
        except PayloadValidationError as exc:
            return error("E_INPUT_TYPE", str(exc))
        except (ValueError, ArithmeticError) as exc:
            return error("E_RESULT_INVALID", str(exc))


class GatewayAckValidateOperator:
    meta = OperatorMeta("communication.gateway.ack_validate", "校验网关关联 ACK", "1.0.0",
        {"request": port("tcpMessage"), "response": port("tcpMessage")},
        {"acknowledged": {"type": "boolean", "required": True, "nullable": False}},
        {"type": "object", "additionalProperties": False, "properties": {"channel": CHANNEL}})

    def validateParams(self, params):
        return _validateChannel(params, {"channel"})

    def executeNode(self, inputs, params, runtimeContext):
        validation = self.validateParams(params)
        if validation:
            return {"status": "error", "error": validation}
        if "request" not in inputs or "response" not in inputs:
            return error("E_INPUT_MISSING", "current request and framed response are required")
        try:
            request = TcpMessage.fromPayload(inputs["request"]).toBytes()
            response = TcpMessage.fromPayload(inputs["response"]).toBytes()
            expected = _requestAck(request, params.get("channel", "robot")).encode("ascii")
            # TCP newline framing already consumed LF. No strip/OK-prefix match.
            if response != expected:
                result = error("E_GATEWAY_ACK_MISMATCH", "ACK does not match this channel and coordinate request; stop, do not resend")
                result["diagnostics"] = {"executionOutcome": "uncertain", "expectedAck": expected.decode("ascii")}
                return result
            return {"status": "ok", "outputs": {"acknowledged": True},
                    "diagnostics": {"physicalCompletionConfirmed": False, "plcYWriteConfirmed": False}}
        except CommunicationPayloadValidationError as exc:
            return error("E_INPUT_TYPE", str(exc))
        except (ValueError, UnicodeError) as exc:
            return error("E_INPUT_SHAPE", str(exc))
