"""Deterministic retirement scheduling regressions for both bounded adapters."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import grpc
import pytest

from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer, SyncContext
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from prototypes.runtime_pages_p0.network import DisplayFeed, IsolatedServer


class StatusService(rpc.RuntimeServiceServicer):
    def __init__(self):
        self.on_status = None
        self.contexts = []

    def GetJobStatus(self, request, context):
        self.contexts.append(context)
        if self.on_status is not None:
            self.on_status(context)
        return pb.GetJobStatusReply(ok=True, status="RUNNING")


@pytest.fixture(params=["p0", "production"])
def adapter(request):
    service = StatusService()
    server = (IsolatedServer(service, DisplayFeed()) if request.param == "p0"
              else AioRuntimeServer(service, None))
    channel = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    try:
        yield server, rpc.RuntimeServiceStub(channel), service
    finally:
        channel.close()
        server.close()


def drain(server):
    async def finish():
        await asyncio.gather(*tuple(server.cleanups))
        assert server.active["control"] == 0
    asyncio.run_coroutine_threadsafe(finish(), server.loop).result(3)


def control_count(server):
    async def read():
        return server.active["control"]
    return asyncio.run_coroutine_threadsafe(read(), server.loop).result(3)


class GatedCleanupExecutor(ThreadPoolExecutor):
    def __init__(self):
        super().__init__(max_workers=13, thread_name_prefix="test-rpc-cleanup")
        self.release = threading.Event()
        self.submissions = []

    def submit(self, fn, /, *args, **kwargs):
        self.submissions.append(fn)

        def gated():
            assert self.release.wait(5), "cleanup gate was not released"
            return fn(*args, **kwargs)
        return super().submit(gated)


def testSequentialControlRepliesDoNotQueueEmptyCallbacks(adapter):
    server, stub, service = adapter
    name = "cleanup_pool" if isinstance(server, IsolatedServer) else "cleanupPool"
    getattr(server, name).shutdown(wait=True)
    cleanup = GatedCleanupExecutor()
    setattr(server, name, cleanup)
    try:
        # Four deferred no-op callbacks used to consume every control credit,
        # rejecting the fifth request despite each handler having returned.
        for _ in range(12):
            assert stub.GetJobStatus(pb.GetJobStatusRequest(), timeout=1).ok
        assert not cleanup.release.is_set()
        assert not cleanup.submissions
        assert all(not context.callbacks for context in service.contexts)
        drain(server)
    finally:
        cleanup.release.set()


def testNonemptyCallbacksKeepQuotaOffLoopAndReportErrors(adapter):
    server, stub, service = adapter
    entered, release = threading.Event(), threading.Event()
    threads = []
    lock = threading.Lock()

    def callback():
        with lock:
            threads.append(threading.get_ident())
            if len(threads) == 4:
                entered.set()
        assert release.wait(5), "callback gate was not released"
        raise ValueError("injected callback failure")

    def register(context):
        assert context.add_callback(callback)
    service.on_status = register
    try:
        for _ in range(4):
            assert stub.GetJobStatus(pb.GetJobStatusRequest(), timeout=1).ok
        assert entered.wait(2)
        assert all(ident != server.thread.ident for ident in threads)
        assert control_count(server) == 4
        with pytest.raises(grpc.RpcError) as rejected:
            stub.GetJobStatus(pb.GetJobStatusRequest(), timeout=1)
        assert rejected.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
        assert rejected.value.details() == "control"
        release.set()
        drain(server)
        assert len(server.errors) == 4
        assert all("injected callback failure" in error for error in server.errors)
        service.on_status = None
        assert stub.GetJobStatus(pb.GetJobStatusRequest(), timeout=1).ok
        drain(server)
    finally:
        release.set()


def testCancelledPendingHandlerKeepsQuotaAndRejectsLateCallback(adapter):
    server, stub, service = adapter
    entered, release, registered, called = (threading.Event() for _ in range(4))
    registrations = []

    def blocked(context):
        entered.set()
        assert release.wait(5), "handler gate was not released"
        registrations.append(context.add_callback(called.set))
        registered.set()
    service.on_status = blocked
    call = stub.GetJobStatus.future(pb.GetJobStatusRequest(), timeout=5)
    try:
        assert entered.wait(2)
        assert call.cancel()
        assert service.contexts[0].cancelled.wait(2)
        assert control_count(server) == 1
        release.set()
        assert registered.wait(2)
        drain(server)
        assert registrations == [False]
        assert not called.is_set()
    finally:
        release.set()
        call.cancel()


def testRegistrationHoldingLockBeforeCancellationIsStillRetired(adapter):
    server, stub, service = adapter
    registering, allow_append, callback_entered, release_callback = (
        threading.Event() for _ in range(4))
    registration_done = threading.Event()
    registrations = []

    def callback():
        assert threading.get_ident() != server.thread.ident
        callback_entered.set()
        assert release_callback.wait(5), "callback gate was not released"

    def register(context):
        class PausedAppend(list):
            def append(self, value):
                registering.set()
                assert allow_append.wait(5), "registration gate was not released"
                assert context.cancelled.is_set()
                super().append(value)
        context.callbacks = PausedAppend()
        registrations.append(context.add_callback(callback))
        registration_done.set()
    service.on_status = register
    call = stub.GetJobStatus.future(pb.GetJobStatusRequest(), timeout=5)
    try:
        # The handler passed the active check while holding the token lock,
        # but has not appended yet when retirement sets cancellation.
        assert registering.wait(2)
        assert call.cancel()
        assert service.contexts[0].cancelled.wait(2)
        allow_append.set()
        assert callback_entered.wait(2)
        assert registration_done.wait(2)
        assert registrations == [True]
        assert control_count(server) == 1
        release_callback.set()
        drain(server)
        assert not server.errors
    finally:
        allow_append.set()
        release_callback.set()
        call.cancel()


def testEmptyCallbacksStillWaitForGrpcCompletion():
    async def check():
        server = AioRuntimeServer.__new__(AioRuntimeServer)
        server.loop = asyncio.get_running_loop()
        server.active = {"control": 1}
        server.cleanups = set()
        server.errors = []
        reached = asyncio.Event()

        class Completion(asyncio.Event):
            async def wait(self):
                reached.set()
                return await super().wait()

        completed = Completion()
        with ThreadPoolExecutor(max_workers=1) as server.cleanupPool:
            try:
                server._retire("control", SyncContext(), None, completed=completed)
                await reached.wait()
                assert server.active["control"] == 1
            finally:
                completed.set()
                await asyncio.gather(*tuple(server.cleanups))
        assert server.active["control"] == 0
        assert not server.errors
    asyncio.run(asyncio.wait_for(check(), 2))
