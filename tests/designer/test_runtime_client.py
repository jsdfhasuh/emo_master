from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2


def testRuntimeClientHasListOperatorsMethod() -> None:
    assert hasattr(RuntimeClient, "listOperators")


def testRuntimeClientUsesServiceForListOperators() -> None:
    class StubService:
        def __init__(self) -> None:
            self.called = False

        def ListOperators(self, request, context):
            _ = request
            _ = context
            self.called = True
            return type(
                "Reply",
                (),
                {
                    "operators": [
                        type(
                            "Operator",
                            (),
                            {
                                "operator_id": "vision.demo.empty",
                                "display_name": "Empty Operator",
                                "version": "0.1.0",
                                "category": "预处理",
                                "icon_key": "source",
                                "summary": "测试算子",
                                "input_ports": {"image": "image"},
                                "output_ports": {"result": "json"},
                                "param_schema_json": '{"type":"object","properties":{}}',
                            },
                        )()
                    ]
                },
            )()

    stub = StubService()
    client = RuntimeClient(runtimeService=stub)
    operators = client.listOperators()
    assert stub.called is True
    assert len(operators) == 1
    assert operators[0].operatorId == "vision.demo.empty"
    assert operators[0].category == "预处理"
    assert operators[0].iconKey == "source"
    assert operators[0].inputPorts["image"] == "image"


def testRuntimeClientCallsGetJobStatus() -> None:
    class StubService:
        def __init__(self) -> None:
            self.called = False

        def GetJobStatus(self, request, context):
            _ = context
            assert getattr(request, "job_id", "") == "job-1"
            self.called = True
            return type(
                "Reply", (), {"ok": True, "status": "COMPLETED", "message": "done"}
            )()

    stub = StubService()
    client = RuntimeClient(runtimeService=stub)
    reply = client.getJobStatus("job-1")
    assert stub.called is True
    assert getattr(reply, "status", "") == "COMPLETED"


def testRuntimeClientReadsStreamJobEvents() -> None:
    class StubService:
        def StreamJobEvents(self, request, context):
            _ = context
            assert getattr(request, "job_id", "") == "job-2"
            return [
                type(
                    "Event",
                    (),
                    {"event_type": "job.started", "message": "start", "level": "INFO"},
                )(),
                type(
                    "Event",
                    (),
                    {"event_type": "job.completed", "message": "done", "level": "INFO"},
                )(),
            ]

    client = RuntimeClient(runtimeService=StubService())
    events = client.streamJobEvents("job-2")
    assert len(events) == 2
    assert getattr(events[0], "event_type", "") == "job.started"


class _FakeStreamCall:
    def __init__(self, events) -> None:
        self.events = iter(events)
        self.nextCalls = 0
        self.cancelCalls = 0
        self.closeCalls = 0

    def __iter__(self):
        return self

    def __next__(self):
        self.nextCalls += 1
        return next(self.events)

    def cancel(self) -> None:
        self.cancelCalls += 1

    def close(self) -> None:
        self.closeCalls += 1


def _streamEvent(eventType: str) -> object:
    return type(
        "Event",
        (),
        {"event_type": eventType, "message": eventType, "level": "INFO"},
    )()


def testRuntimeClientFollowIsLazyAndPassesReplayArguments() -> None:
    call = _FakeStreamCall([_streamEvent("job.started")])

    class StubService:
        def StreamJobEvents(self, request, context):
            _ = context
            assert request.job_id == "job-follow"
            assert request.after_sequence == 7
            assert request.follow is True
            return call

    client = RuntimeClient(runtimeService=StubService())
    stream = client.streamJobEvents("job-follow", afterSequence=7, follow=True)

    assert call.nextCalls == 0
    event = next(stream)
    assert event.eventType == "job.started"
    assert call.nextCalls == 1

    assert client.cancelEventStream("job-follow") is True
    assert call.cancelCalls == 1
    assert call.closeCalls == 1
    assert client.cancelEventStream("job-follow") is False


def testRuntimeClientFollowClosesAndUnregistersOnExhaustion() -> None:
    call = _FakeStreamCall([])

    class StubService:
        def StreamJobEvents(self, request, context):
            _ = request
            _ = context
            return call

    client = RuntimeClient(runtimeService=StubService())
    stream = client.streamJobEvents("job-exhausted", follow=True)

    assert list(stream) == []
    assert call.closeCalls == 1
    assert client.cancelEventStream("job-exhausted") is False


def testRuntimeClientReplacesPreviousFollowWithoutRemovingNewStream() -> None:
    first = _FakeStreamCall([])
    second = _FakeStreamCall([])
    calls = iter([first, second])

    class StubService:
        def StreamJobEvents(self, request, context):
            _ = request
            _ = context
            return next(calls)

    client = RuntimeClient(runtimeService=StubService())
    client.streamJobEvents("job-replaced", follow=True)
    client.streamJobEvents("job-replaced", follow=True)

    assert first.cancelCalls == 1
    assert first.closeCalls == 1
    assert client.cancelEventStream("job-replaced") is True
    assert second.cancelCalls == 1
    assert second.closeCalls == 1


def testRuntimeClientParsesEventPayloadJson() -> None:
    class StubService:
        def StreamJobEvents(self, request, context):
            _ = context
            assert getattr(request, "job_id", "") == "job-3"
            return [
                type(
                    "Event",
                    (),
                    {
                        "event_type": "node.completed",
                        "message": "done",
                        "level": "INFO",
                        "node_id": "if1",
                        "payload_json": '{"status":"SKIPPED","branch":"true"}',
                    },
                )()
            ]

    client = RuntimeClient(runtimeService=StubService())
    events = client.streamJobEvents("job-3")
    assert len(events) == 1
    payload = getattr(events[0], "payload", None)
    assert isinstance(payload, dict)
    assert payload.get("status") == "SKIPPED"
    assert payload.get("branch") == "true"


def testRuntimeClientParsesProtoMapContainerPorts() -> None:
    class StubService:
        def ListOperators(self, request, context):
            _ = request
            _ = context
            operator = runtime_pb2.OperatorInfo(
                operator_id="vision.edge.canny",
                display_name="Canny Edge",
                version="1.0.0",
                input_ports={"image": "image"},
                output_ports={"edges": "image"},
                param_schema_json='{"type":"object","properties":{}}',
            )
            return type("Reply", (), {"operators": [operator]})()

    client = RuntimeClient(runtimeService=StubService())
    operators = client.listOperators()

    assert len(operators) == 1
    assert operators[0].inputPorts == {"image": "image"}
    assert operators[0].outputPorts == {"edges": "image"}
