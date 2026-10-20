import socket
import time

import pytest

from emo_master.core.contracts.communication import TcpMessage
from emo_master.core.contracts.geometry2d import Point2D
from emo_master.plugins.builtins._communication_operators import TcpClientOperator
from emo_master.plugins.builtins._gateway_coordinates import GatewayAckValidateOperator, GatewayCoordinateFormatOperator
from emo_master.plugins.builtins._image_frame import defaultFrame
from emo_master.plugins.builtins._tcp_transport import receiveFramed, TcpFrameError
from tests.plugins.test_communication_operators import _startTcpPeer, _joinPeer, _recvExact


def point(x, y):
    return {"points": [Point2D(x, y, defaultFrame(10, 10).coordinateSpace).toPayload()]}


@pytest.mark.parametrize("channel,x,y,params,text,ack", [
    ("robot", 20.38, -5.8, {}, '"20.38,-5.80"', "OK '20.38,-5.80EY'"),
    ("robot", -0.001, 2.675, {}, '"0.00,2.68"', "OK '0.00,2.68EY'"),
    ("plc", -6, 2206, {}, '"-6,2206"', "OK D7=2 D6=6"),
    ("plc", 2399, 4509, {}, '"2399,4509"', "OK D7=1 D6=2399"),
    ("plc", -6.5, 2.5, {"plcRounding": "halfAwayFromZero"}, '"-7,3"', "OK D7=2 D6=7"),
    ("plc", -6.9, 2.9, {"plcRounding": "towardZero"}, '"-6,2"', "OK D7=2 D6=6"),
])
def testNormalProtocolBytesAndCorrelatedAck(channel, x, y, params, text, ack):
    result = GatewayCoordinateFormatOperator().executeNode(point(x, y), dict(channel=channel, **params), {})
    assert result["status"] == "ok"
    assert result["outputs"]["text"] == text
    assert TcpMessage.fromPayload(result["outputs"]["message"]).toBytes() == text.encode("ascii")
    assert result["outputs"]["expectedAck"] == ack
    validation = GatewayAckValidateOperator().executeNode({"request": result["outputs"]["message"],
        "response": TcpMessage.fromBytes(ack.encode("ascii")).toPayload()}, {"channel": channel}, {})
    assert validation["outputs"] == {"acknowledged": True}
    assert validation["diagnostics"]["physicalCompletionConfirmed"] is False


@pytest.mark.parametrize("response", [b"OK", b"FAIL", b"OK D7=1 D6=6", b"OK D7=2 D6=7",
                                      b"OK '20.38,-5.80EY'", b"OK D7=2 D6=6\\n", b"OK D7=2 D6=6\n"])
def testWrongAckDoesNotCountAsSuccess(response):
    request = GatewayCoordinateFormatOperator().executeNode(point(-6, 2206), {"channel": "plc"}, {})["outputs"]["message"]
    result = GatewayAckValidateOperator().executeNode({"request": request, "response": TcpMessage.fromBytes(response).toPayload()}, {"channel": "plc"}, {})
    assert result["error"]["code"] == "E_GATEWAY_ACK_MISMATCH"
    assert result["diagnostics"]["executionOutcome"] == "uncertain"


def testCardinalityPrecisionAndPlcRange():
    operator = GatewayCoordinateFormatOperator()
    for inputs, params in (({"points": []}, {}), ({"points": point(1, 2)["points"] * 2}, {}),
                           (point(6.5, 0), {"channel": "plc"}), (point(65536, 0), {"channel": "plc"})):
        assert operator.executeNode(inputs, params, {})["status"] == "error"


def testSplitLfAckAndUncertainTimeoutNeverResends():
    request = GatewayCoordinateFormatOperator().executeNode(point(-6, 2206), {"channel": "plc"}, {})["outputs"]["message"]
    observed = []
    def handler(sock):
        observed.append(_recvExact(sock, 9))
        sock.sendall(b"OK D7=")
        time.sleep(.01)
        sock.sendall(b"2 D6=6\n")
    port, thread, errors = _startTcpPeer(handler)
    result = TcpClientOperator().executeNode({"message": request}, {"host": "127.0.0.1", "port": port,
        "operation": "exchange", "responseFraming": "newline", "rejectTrailingResponse": True}, {})
    _joinPeer(thread, errors)
    assert observed == [b'"-6,2206"']
    assert result["status"] == "ok"
    assert GatewayAckValidateOperator().executeNode({"request": request, "response": result["outputs"]["response"]}, {"channel": "plc"}, {})["status"] == "ok"
    def noAck(sock):
        observed.append(_recvExact(sock, 9))
        time.sleep(.1)
    port, thread, errors = _startTcpPeer(noAck)
    result = TcpClientOperator().executeNode({"message": request}, {"host": "127.0.0.1", "port": port,
        "operation": "exchange", "responseTimeoutMs": 20}, {})
    _joinPeer(thread, errors)
    assert result["error"]["code"] == "E_COMM_TIMEOUT"
    assert result["diagnostics"]["executionOutcome"] == "uncertain"
    assert len(observed) == 2


def testStrictFramingRejectsResidualResponse():
    receiver, sender = socket.socketpair()
    try:
        receiver.settimeout(.1)
        sender.sendall(b"OK D7=2 D6=6\nFAIL\n")
        with pytest.raises(TcpFrameError, match="trailing"):
            receiveFramed(receiver, framing="newline", maxBytes=100, expectedBytes=0, rejectTrailing=True)
    finally:
        receiver.close()
        sender.close()
