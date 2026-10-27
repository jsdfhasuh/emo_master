import pytest

from emo_master.plugins.builtins._managed_tcp import CoordinateMessageFormatOperator, TcpSessionOperator
from emo_master.core.contracts.port_types import matchesPortSpec


@pytest.mark.parametrize("x,y,expected", [(12.3454, -6.78, "'12.345,-6.78EY'"),
    (0.0, -0.0, "'0.0,-0.0EY'"), (1.2345, 2.0001, "'1.234,2.0EY'")])
def testLn5RoundStringBytes(x, y, expected):
    result = CoordinateMessageFormatOperator().executeNode(dict(x=x, y=y), {}, {})
    assert result["outputs"]["data"].encode("utf-8") == expected.encode("utf-8")
    assert not result["outputs"]["data"].endswith("\n")
    if x == 12.3454:
        assert len(expected.encode()) == 16


def testFormatterStrictParametersAndNoProductionSessionDebug():
    assert CoordinateMessageFormatOperator().validateParams(dict(decimals=True))
    assert CoordinateMessageFormatOperator().executeNode(dict(x=float("nan"), y=0), {}, {})["status"] == "error"
    error = TcpSessionOperator().executeNode({}, {}, {})
    assert error["error"]["code"] == "E_TCP_UNSUPPORTED"
    assert not matchesPortSpec(dict(type="tcpSessionRef", schemaVersion="1.0"), "tcpSessionRef")


@pytest.mark.parametrize("code", ["E_CANCELLED", "E_TCP_IPC_CLOSED", "E_TCP_OUTCOME_UNKNOWN"])
def testLiveCancellationWinsTransportRaceWithoutLosingReceipt(code):
    from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
    receipt = dict(outcome="OUTCOME_UNKNOWN", message="cannot prove not sent")
    def executeDevice(*args):
        from emo_master.core.contracts.device_driver import DeviceError
        raise DeviceError(code, 'transport stopped', diagnostics=dict(tcpReceipt=receipt))
    token = CancellationToken()
    token.cancel()
    with pytest.raises(CancellationRequested) as error:
        TcpSessionOperator().executeNode({}, {},
            dict(executeDevice=executeDevice, raiseIfCancellationRequested=token.raise_if_cancelled))
    assert error.value.diagnostics == dict(tcpReceipt=receipt)
