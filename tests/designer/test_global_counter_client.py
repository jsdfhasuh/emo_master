import pytest

from emo_master.apps.designer.services.runtime_client import (
    GlobalCounterInfo,
    RuntimeClient,
    RuntimeClientError,
)
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2


def testRuntimeClientMapsGlobalCounterDtosAndRequests() -> None:
    class Service:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, int]] = []

        def ListGlobalCounters(self, request, context):
            _ = context
            self.calls.append(("list", request.project_id, "", 0))
            return runtime_pb2.ListGlobalCountersReply(
                ok=True,
                counters=[
                    runtime_pb2.GlobalCounterInfo(
                        name="parts",
                        value=7,
                        updated_at_ms=123,
                    )
                ],
            )

        def GetGlobalCounter(self, request, context):
            _ = context
            self.calls.append(("get", request.project_id, request.name, 0))
            return _reply("get", 0)

        def SetGlobalCounter(self, request, context):
            _ = context
            self.calls.append(
                ("set", request.project_id, request.name, request.value)
            )
            return runtime_pb2.SetGlobalCounterReply(
                ok=True,
                counter=runtime_pb2.GlobalCounterInfo(
                    name=request.name,
                    value=request.value,
                    updated_at_ms=456,
                ),
            )

        def ResetGlobalCounter(self, request, context):
            _ = context
            self.calls.append(("reset", request.project_id, request.name, 0))
            return runtime_pb2.ResetGlobalCounterReply(
                ok=True,
                counter=runtime_pb2.GlobalCounterInfo(
                    name=request.name,
                    value=0,
                    updated_at_ms=789,
                ),
            )

    service = Service()
    client = RuntimeClient(service)

    assert client.listGlobalCounters("project") == [
        GlobalCounterInfo(name="parts", value=7, updatedAtMs=123)
    ]
    assert client.getGlobalCounter("project", "parts").value == 0
    assert client.setGlobalCounter("project", "parts", 9).value == 9
    assert client.resetGlobalCounter("project", "parts").value == 0
    assert service.calls == [
        ("list", "project", "", 0),
        ("get", "project", "parts", 0),
        ("set", "project", "parts", 9),
        ("reset", "project", "parts", 0),
    ]


def testRuntimeClientRaisesStableGlobalCounterReplyError() -> None:
    class Service:
        def ListGlobalCounters(self, request, context):
            _ = request, context
            return runtime_pb2.ListGlobalCountersReply(
                ok=False,
                code="E_COUNTER_BUSY",
                message="busy",
            )

    with pytest.raises(RuntimeClientError) as error:
        RuntimeClient(Service()).listGlobalCounters("project")
    assert error.value.code == "E_COUNTER_BUSY"
    assert str(error.value) == "busy"

    with pytest.raises(RuntimeClientError) as rangeError:
        RuntimeClient(Service()).setGlobalCounter("project", "parts", -1)
    assert rangeError.value.code == "E_COUNTER_VALUE_RANGE"


def _reply(name: str, value: int):
    return runtime_pb2.GetGlobalCounterReply(
        ok=True,
        counter=runtime_pb2.GlobalCounterInfo(
            name=name,
            value=value,
            updated_at_ms=321,
        ),
    )
