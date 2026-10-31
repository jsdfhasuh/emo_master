"""Real RPC/session admission, separate from controller-only result tests."""
import json
import os
from pathlib import Path
import threading

import grpc
import pytest

from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.clients.runtime.connection import ConnectionProfile
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.presentation.rpc import DisplayRpc
from tests.runtime.presentation.test_network import until


def testFiveRealSessionsHaveFiveHealthySubscriptions(channel):
    # No extra observer RPCs and no worker/execution dependency: isolate W01.
    jobs = [f"display-job-{i}" for i in range(5)]
    channel.jobs.update({job: {"scopeIds": [], "scopeTerminals": {}} for job in jobs})
    server = AioRuntimeServer(channel.runtime, channel)
    profile = ConnectionProfile(f"127.0.0.1:{server.port}")
    clients = [DisplaySession(profile, job) for job in jobs]
    try:
        # Server admission can precede Subscribe returning its client handle.
        until(lambda: server.active["display"] == 5
              and all(client.stream is not None and client.stream.is_active() for client in clients), timeout=3)
        assert all(client.stream is not None and client.stream.is_active() for client in clients)
        assert not [error for client in clients for error in client.errors]
    finally:
        if os.environ.get("EMO_DISPLAY_EVIDENCE"):
            path = Path(os.environ["EMO_DISPLAY_EVIDENCE"])
            path.mkdir(parents=True, exist_ok=True)
            (path / "five-sessions.json").write_text(json.dumps({
                "active": server.active, "errors": [list(c.errors) for c in clients],
                "admission": list(getattr(server, "displaySubscriptions", ()).events)
                if hasattr(server, "displaySubscriptions") else [],
            }, indent=2), encoding="utf-8")
        for client in clients:
            client.close()
        until(lambda: server.active["display"] == 0)
        server.close()
        channel.jobs.clear()
    assert all(not t.is_alive() for c in clients for t in c.threads)


def testPerJobIsolationBoundedJobsAndRetirement(channel, monkeypatch):
    from emo_master.apps.runtime.grpc_server.display_subscriptions import DisplaySubscriptions
    monkeypatch.setattr(DisplaySubscriptions, "jobsLimit", 2)
    channel.jobs.update({job: {"scopeIds": []} for job in ("a", "b", "c")})
    server = AioRuntimeServer(channel.runtime, channel, displaySubscriptionsPerJob=2)
    calls = []
    with grpc.insecure_channel(f"127.0.0.1:{server.port}") as transport:
        stub = rpc.DisplayServiceStub(transport)
        def subscribe(job):
            call = stub.Subscribe(pb.DisplayRequest(job_id=job), timeout=10)
            calls.append(call)
            assert next(call).job_id == job
            return call
        try:
            first, second = subscribe("a"), subscribe("a")
            for job, detail in (("a", "per-Job"),):
                with pytest.raises(grpc.RpcError) as error:
                    next(stub.Subscribe(pb.DisplayRequest(job_id=job), timeout=.5))
                assert error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
                assert detail in error.value.details()
            subscribe("b")  # Saturated A cannot starve B.
            with pytest.raises(grpc.RpcError) as error:
                next(stub.Subscribe(pb.DisplayRequest(job_id="c"), timeout=.5))
            assert error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
            first.cancel()
            second.cancel()
            until(lambda: "a" not in server.displaySubscriptions.byJob)
            subscribe("c")
            assert server.displaySubscriptions.workPeak <= 2
        finally:
            for call in calls:
                call.cancel()
            until(lambda: server.active["display"] == 0)
            events = list(server.displaySubscriptions.events)
            assert {e["subscriptionId"] for e in events if e["action"] == "admitted"} == {
                e["subscriptionId"] for e in events if e["action"] == "retired"}
            assert len([e for e in events if e["action"] == "retired"]) == 4
            server.close()
            channel.jobs.clear()


def testDefaultAllowsFourIndependentClientsAndBoundsTheFifth(channel):
    channel.jobs['shared'] = {'scopeIds': []}
    server = AioRuntimeServer(channel.runtime, channel)
    calls = []
    try:
        with grpc.insecure_channel(f'127.0.0.1:{server.port}') as transport:
            stub = rpc.DisplayServiceStub(transport)
            for _ in range(4):
                call = stub.Subscribe(pb.DisplayRequest(job_id='shared'), timeout=5)
                calls.append(call)
                assert next(call).job_id == 'shared'
            with pytest.raises(grpc.RpcError) as error:
                next(stub.Subscribe(pb.DisplayRequest(job_id='shared'), timeout=.5))
            assert error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
            assert server.displaySubscriptions.byJob['shared'] == 4
    finally:
        for call in calls:
            call.cancel()
        until(lambda: server.active['display'] == 0)
        server.close()
        channel.jobs.clear()


@pytest.mark.parametrize("stage", ["construct", "next", "callback", "close"])
def testCancelledDisplayKeepsItsSlotAndWorkerUntilActualRetirement(channel, monkeypatch, stage):
    entered, release = threading.Event(), threading.Event()
    closes = []
    def block():
        entered.set()
        assert release.wait(10)
    class Iterator:
        def __next__(self):
            if stage == "next":
                block()
            raise StopIteration
        def close(self):
            if stage == "close":
                block()
            closes.append(True)
    def injected(self, request, context):
        if stage == "callback":
            context.add_callback(block)
        if stage == "construct":
            block()
        return Iterator()
    monkeypatch.setattr(DisplayRpc, "Subscribe", injected)
    channel.jobs["a"] = {"scopeIds": []}
    server = AioRuntimeServer(channel.runtime, channel)
    with grpc.insecure_channel(f"127.0.0.1:{server.port}") as transport:
        call = rpc.DisplayServiceStub(transport).Subscribe(pb.DisplayRequest(job_id="a"))
        try:
            assert entered.wait(2)
            call.cancel()
            assert server.active["display"] == 1
            assert server.displaySubscriptions.workActive == (0 if stage == "callback" else 1)
            assert rpc.DisplayServiceStub(transport).Capabilities(pb.DisplayEmpty(), timeout=.5).protocol_version == "1.0"
        finally:
            release.set()
            call.cancel()
            until(lambda: server.active["display"] == 0)
            assert closes == [True]
            assert server.displaySubscriptions.workActive == 0
            assert [e["action"] for e in server.displaySubscriptions.events] == ["admitted", "retired"]
            server.close()
            channel.jobs.clear()


def testCancellationCallbacksCanUnblockBothBusyDisplayWorkers(channel, monkeypatch):
    entered = []
    ready = threading.Event()
    release = threading.Event()
    original = DisplayRpc._snapshot
    def blocked(self, request, context, incremental=False):
        assert context.add_callback(release.set)
        entered.append(context)
        if len(entered) == 2:
            ready.set()
        assert release.wait(5)
        return original(self, request, context, incremental)
    monkeypatch.setattr(DisplayRpc, "_snapshot", blocked)
    channel.jobs["a"] = {"scopeIds": []}
    server = AioRuntimeServer(channel.runtime, channel)
    with grpc.insecure_channel(f"127.0.0.1:{server.port}") as transport:
        stub = rpc.DisplayServiceStub(transport)
        calls = [stub.Subscribe(pb.DisplayRequest(job_id="a")) for _ in range(2)]
        try:
            assert ready.wait(2)
            assert server.displaySubscriptions.workActive == 2
            for call in calls:
                call.cancel()
            assert release.wait(2), "cancellation callback was starved behind its own synchronous work"
            until(lambda: server.active["display"] == 0)
            assert not server.errors
        finally:
            release.set()
            for call in calls:
                call.cancel()
            server.close()
            channel.jobs.clear()
