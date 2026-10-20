from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2


def testRuntimeClientHasListOperatorsMethod() -> None:
    assert hasattr(RuntimeClient, "listOperators")


def testInspectionReadDoesNotOverallocateOrCancelSuccessfulDecodeContext() -> None:
    from emo_master.apps.designer.services.display_calls import DisplayCallContext

    class Service:
        def StreamPreviewAsset(self, request, context):
            for _ in range(16):
                yield runtime_pb2.PreviewDownloadChunk(asset_id=request.asset_id,
                    mime_type='image/png', content=b'x' * (256 * 1024))

    client = RuntimeClient(Service())
    context = DisplayCallContext()
    payload, mime = client.readInspectionAsset('project', 'session', 'asset', context)
    context.check()  # The same deadline continues through decode.
    assert mime == 'image/png' and len(payload) == 4 * 1024 * 1024
    assert payload.__alloc__() == len(payload) + 1  # CPython terminating byte, no spare payload capacity.
    assert client.inspectionPeakScratchBytes <= 8 * 1024 * 1024


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


def testRuntimeClientCloseIsIdempotentAndClosesOwnedResources() -> None:
    class Call:
        def __iter__(self):
            return self

        def __next__(self):
            raise RuntimeError("stream should be cancelled")

        def cancel(self) -> None:
            self.cancelled = getattr(self, "cancelled", 0) + 1

        def close(self) -> None:
            self.closed = getattr(self, "closed", 0) + 1

    class Service:
        def __init__(self) -> None:
            self.call = Call()
            self.closed = 0

        def StreamJobEvents(self, request, context):
            _ = request
            _ = context
            return self.call

        def close(self) -> None:
            self.closed += 1

    class Channel:
        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    service = Service()
    channel = Channel()
    client = RuntimeClient(
        runtimeService=service,
        ownedRuntimeService=service,
        ownedChannel=channel,
    )
    client.streamJobEvents("job-owned", follow=True)

    client.close()
    client.close()

    assert service.call.cancelled == 1
    assert service.call.closed == 1
    assert service.closed == 1
    assert channel.closed == 1


def testRuntimeClientDoesNotCloseUnownedRuntimeService() -> None:
    class Service:
        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    class Channel:
        def close(self) -> None:
            self.closed = getattr(self, "closed", 0) + 1

    service = Service()
    channel = Channel()
    client = RuntimeClient(runtimeService=service, ownedChannel=channel)
    client.close()

    assert service.closed == 0
    assert channel.closed == 1


def testReadOnlyDisplayLookupNeverProvisionsEmbeddedService(monkeypatch) -> None:
    import pytest
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError

    client = RuntimeClient(object())
    assert client.displayAddress() == ""
    with pytest.raises(RuntimeClientError, match="页面服务"):
        client.getDisplayCapabilities()
    assert client._presentationServer is None
    assert client._presentationService is None


def testDisplayJobLookupFiltersExactProjectEvenIfServerIgnoresFilter() -> None:
    from types import SimpleNamespace

    class Display:
        def ListJobs(self, request, timeout):
            assert request.project_id == "current-project"
            return SimpleNamespace(jobs=[SimpleNamespace(job_id="current", project_id="current-project"),
                                        SimpleNamespace(job_id="other", project_id="other-project"),
                                        SimpleNamespace(job_id="old-server")])

    client = RuntimeClient(object(), displayService=Display())
    assert [job.job_id for job in client.listDisplayJobs("current-project")] == ["current"]
    assert client._presentationServer is None


def testClientAddsOptionalCaptureFieldsAndQueriesSameStartRequest() -> None:
    class Service:
        def StartJob(self, request, context):
            assert request.project_id == "production-project"
            assert request.workflow_id == "main"
            assert request.inputs_json == '{"value": 2}'
            assert request.capture_presentation
            assert request.start_request_id == "request-one"
            assert request.expected_runtime_instance_id == "runtime-one"
            return runtime_pb2.StartJobReply(ok=True, job_id="same-job", runtime_instance_id="runtime-one",
                                            start_request_id="request-one")

        def GetStartRequest(self, request, context):
            assert request.start_request_id == "request-one"
            assert request.runtime_instance_id == "runtime-one"
            return runtime_pb2.StartJobReply(ok=True, job_id="same-job", runtime_instance_id="runtime-one",
                                            start_request_id="request-one")

    client = RuntimeClient(Service())
    start = client.startJob("production-project", "main", {"value": 2}, capturePresentation=True,
                            startRequestId="request-one", expectedRuntimeInstanceId="runtime-one")
    assert client.getStartRequest("request-one", "runtime-one").job_id == start.job_id


def testPrepareCaptureRequiresNegotiatedCapabilityAndKeepsLegacyNoPagePath() -> None:
    import pytest
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError

    class Display:
        def Capabilities(self, request, timeout):
            return runtime_pb2.DisplayCapabilities(runtime_instance_id="legacy", capabilities=["snapshot"])

    client = RuntimeClient(object(), displayService=Display())
    assert client.prepareStart(False) == ""
    with pytest.raises(RuntimeClientError, match="正常运行"):
        client.prepareStart(True)


def testReleaseJobRequiresVerifiedTerminalAndPreservesGeneration() -> None:
    import pytest
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError

    class Service:
        terminal = False

        def GetJobStatus(self, request, context):
            return runtime_pb2.GetJobStatusReply(ok=True, status="COMPLETED" if self.terminal else "RUNNING")

    class Display:
        calls = []

        def ReleaseJob(self, request, timeout):
            self.calls.append((request.job_id, request.runtime_instance_id))
            return runtime_pb2.DisplayEmpty()

    service, display = Service(), Display()
    client = RuntimeClient(service, displayService=display)
    with pytest.raises(RuntimeClientError, match="尚未确认结束"):
        client.releaseDisplayJob("job-one", "runtime-one")
    assert not display.calls
    service.terminal = True
    client.releaseDisplayJob("job-one", "runtime-one")
    assert display.calls == [("job-one", "runtime-one")]


def testClientCloseFailureRetainsOwnerAndCanRetryWithoutReclosingFinishedResources() -> None:
    import pytest

    class Resource:
        def __init__(self, fail=False):
            self.calls, self.fail = 0, fail

        def close(self):
            self.calls += 1
            if self.fail:
                self.fail = False
                raise TimeoutError("worker still owns resources")

    runtime, endpoint, displayChannel, channel = Resource(True), Resource(), Resource(), Resource()
    client = RuntimeClient(object(), ownedRuntimeService=runtime, ownedChannel=channel)
    client._presentationServer, client._displayChannel = endpoint, displayChannel
    with pytest.raises(TimeoutError, match="still owns"):
        client.close()
    assert not client._closed and client._closing
    assert client._ownedRuntimeService is runtime
    assert client._presentationServer is None and client._displayChannel is None
    assert channel.calls == 0
    client.close()
    assert client._closed and not client._closing
    assert runtime.calls == 2 and endpoint.calls == displayChannel.calls == channel.calls == 1
    client.close()
    assert runtime.calls == 2


def testEmbeddedEventCancellationRetiresGeneratorOnConsumerThread() -> None:
    import threading
    import time

    entered = threading.Event()
    retired = []
    received = []

    class Service:
        def StreamJobEvents(self, request, context):
            assert context is not None
            try:
                entered.set()
                while context.is_active():
                    time.sleep(.005)
                # Even a concurrently available item cannot escape after cancel.
                yield _streamEvent("node.completed")
            finally:
                retired.append(threading.get_ident())

    client = RuntimeClient(Service())
    stream = client.streamJobEvents("active", follow=True)
    consumer = threading.Thread(target=lambda: received.extend(stream))
    consumer.start()
    assert entered.wait(1)
    assert client.cancelEventStream("active")
    consumer.join(1)
    assert not consumer.is_alive()
    assert not received
    assert retired == [consumer.ident]
    assert client.cancelEventStream("active") is False
    client.close()


def testEventStreamCloseFailureRemainsTrackedForRetry() -> None:
    import pytest

    class Call(_FakeStreamCall):
        def close(self):
            super().close()
            if self.closeCalls == 1:
                raise OSError("real iterator cleanup failure")

    call = Call([])

    class Service:
        def StreamJobEvents(self, request, context):
            return call

    client = RuntimeClient(Service())
    stream = client.streamJobEvents("failed-cleanup", follow=True)
    with pytest.raises(OSError, match="real iterator"):
        client.cancelEventStream("failed-cleanup")
    assert not stream._closed
    assert client._activeStreams["failed-cleanup"] is stream
    assert client.cancelEventStream("failed-cleanup")
    assert stream._closed and not client._activeStreams
    assert call.closeCalls == 2
    assert call.cancelCalls == 1


def testClientCloseWaitsForInFlightEventConsumerBeforeClosingOwnedRuntime() -> None:
    import pytest
    import threading

    entered, release = threading.Event(), threading.Event()

    class Service:
        closed = 0

        def StreamJobEvents(self, request, context):
            entered.set()
            release.wait(2)
            yield _streamEvent("node.completed")

        def close(self):
            self.closed += 1

    service = Service()
    client = RuntimeClient(service, ownedRuntimeService=service, deadlineMs=10)
    stream = client.streamJobEvents("retiring", follow=True)
    consumer = threading.Thread(target=lambda: list(stream))
    consumer.start()
    try:
        assert entered.wait(1)
        with pytest.raises(TimeoutError, match="consumer still owns"):
            client.close()
        assert not client._closed and client._closing
        assert client._activeStreams["retiring"] is stream
        assert service.closed == 0
    finally:
        release.set()
        consumer.join(1)
    assert not consumer.is_alive()
    client.close()
    assert client._closed and service.closed == 1
