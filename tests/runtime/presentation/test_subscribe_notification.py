"""Ordered Subscribe notification/pacing checks, never latency measurements.

Controlled waits keep the real Condition registration/notification mechanism;
only their deadline is controlled. Separate tests retain the real 20 ms timeout.
Every controller and consumer has a five-second deadlock guard for cleanup.
"""
import asyncio
from concurrent.futures import Future
import json
import queue
import threading
import time
from types import FunctionType, SimpleNamespace

import grpc
import pytest

from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer, RpcAbort, SyncContext
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.jobs.models import JobRecord
from emo_master.apps.runtime.presentation import rpc as display_rpc
from emo_master.apps.runtime.presentation.store import ResultStore


GUARD = 5


class Gate:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.requested = []

    def sleep(self, seconds):
        self.requested.append(seconds)
        self.entered.set()
        assert self.release.wait(GUARD), "controller did not release cooldown"


class WaitProbe:
    """Wrap real Condition.wait, without enlarging any production timeout.

    Registration is announced while the shared lock is held. A publisher must
    acquire that lock, so it cannot notify before real wait releases the lock
    and registers. None is a test-only continuation gate, not a timeout claim.
    For spurious-wake checks, copy unchanged stdlib wait_for bytecode with a
    local frozen clock; do not patch threading or a shared clock globally.
    """
    def __init__(self, monkeypatch, store, *, freezeDeadline=False):
        self.condition = store.changed
        self.originalWait = self.condition.wait
        self.registered = queue.Queue()
        self.returned = queue.Queue()
        self.closing = False
        if freezeDeadline:
            original = self.condition.wait_for.__func__
            globalsCopy = dict(original.__globals__, _time=lambda: 1.0 if self.closing else 0.0)
            localWaitFor = FunctionType(original.__code__, globalsCopy, original.__name__,
                                        original.__defaults__, original.__closure__)
            monkeypatch.setattr(self.condition, "wait_for", localWaitFor.__get__(self.condition))
        monkeypatch.setattr(self.condition, "wait", self.wait)

    def wait(self, timeout=None):
        if self.closing:
            return self.originalWait(timeout)
        assert timeout == 0.02
        self.registered.put(timeout)
        notified = self.originalWait(None)
        self.returned.put(notified)
        return notified

    def registration(self):
        assert self.registered.get(timeout=GUARD) == 0.02

    def close(self):
        with self.condition:
            self.closing = True
            # Cancellation does not change the cursor: a cleanup notification
            # could otherwise re-enter an untimed wait after a spurious wake.
            self.condition.wait = self.originalWait
            self.condition.notify_all()


def scalarItem(ordinal, scope="root"):
    return {"identity": {
        "runtimeInstanceId": "runtime", "jobId": "job", "resultScopeId": scope,
        "invocationId": str(ordinal), "resultKey": f"job-{scope}-{ordinal}",
        "resultOrdinal": ordinal, "executionRevision": "a" * 64,
        "capturePlanRevision": "b" * 64, "mode": "runtime"}, "expected": ["count"]}


def beginScalar(store, ordinal=1, scope="root"):
    item = scalarItem(ordinal, scope)
    assert store.begin(item)
    return item["identity"]["resultKey"]


def closeScalar(store, ordinal, scope="root", value=None):
    key = beginScalar(store, ordinal, scope)
    finishScalar(store, key, ordinal if value is None else value)
    return key


def finishScalar(store, key, value=1):
    assert store.close(key, [{"sourceId": "count", "state": "AVAILABLE",
                              "valueJson": json.dumps(value)}], "COMPLETED")


def harness(monkeypatch, *, service=None, request=None):
    if service is None:
        service = SimpleNamespace(store=ResultStore(), jobs={"job": {}},
                                  runtimeInstanceId="runtime", supportsNormalCapture=False)
    adapter = display_rpc.DisplayRpc(service)
    snapshots = []
    originalSnapshot = adapter._snapshot

    def observeSnapshot(*args, **kwargs):
        result = originalSnapshot(*args, **kwargs)
        snapshots.append(result)
        return result

    monkeypatch.setattr(adapter, "_snapshot", observeSnapshot)
    gate = Gate()
    monkeypatch.setattr(display_rpc, "time", SimpleNamespace(
        sleep=gate.sleep, perf_counter_ns=time.perf_counter_ns))
    context = SyncContext()
    request = request or pb.DisplayRequest(job_id="job", runtime_instance_id=service.runtimeInstanceId)
    stream = adapter.Subscribe(request, context)
    return SimpleNamespace(store=service.store, service=service, adapter=adapter,
                           snapshots=snapshots, gate=gate, context=context, stream=stream)


def inThread(action):
    result = Future()

    def run():
        try:
            result.set_result(action())
        except BaseException as error:
            result.set_exception(error)

    thread = threading.Thread(target=run, name="subscribe-notification-test", daemon=True)
    thread.start()
    return thread, result


def retire(h, thread, probe=None):
    h.context.cancelled.set()
    h.gate.release.set()
    if probe is not None:
        probe.close()
    thread.join(GUARD)
    assert not thread.is_alive(), "Subscribe consumer did not retire"
    h.stream.close()



def observeFiniteWait(monkeypatch, store, entered):
    original = store.changed.wait
    originalWaitFor = store.changed.wait_for

    def observe(timeout=None):
        entered.set()  # Called with the actual shared lock held.
        return original(timeout)

    def observeWaitFor(predicate, timeout=None):
        # This is the total budget; internal waits may use a smaller remainder.
        assert timeout == 0.02
        return originalWaitFor(predicate, timeout=timeout)

    monkeypatch.setattr(store.changed, "wait", observe)
    monkeypatch.setattr(store.changed, "wait_for", observeWaitFor)

def testPublicationBeforeWaitEntryCannotLoseWake(monkeypatch):
    h = harness(monkeypatch)
    entry = Gate()
    original = h.store.waitForChange
    observed = []

    def beforeWait(cursor):
        observed.append(cursor)
        entry.sleep(0.02)
        return original(cursor)

    monkeypatch.setattr(h.store, "waitForChange", beforeWait)
    monkeypatch.setattr(h.store.changed, "wait", lambda timeout: pytest.fail("changed cursor must not wait"))
    thread, delivery = inThread(lambda: next(h.stream))
    try:
        assert entry.entered.wait(GUARD)
        assert observed == [h.snapshots[0].cursor] == [0]
        assert not h.snapshots[0].results and not h.snapshots[0].reset_required
        key = closeScalar(h.store, 1)
        entry.release.set()
        message = delivery.result(GUARD)
        assert message.cursor == 2 and message.results[0].identity.result_key == key
        assert len(h.snapshots) == 2 and not h.gate.requested
    finally:
        entry.release.set()
        retire(h, thread)


@pytest.mark.parametrize("publication", ["begin", "close"])
@pytest.mark.parametrize("subscribers", [1, 2])
def testRegisteredWaitersWakeFromRealNotifyAll(monkeypatch, publication, subscribers):
    first = harness(monkeypatch)
    key = beginScalar(first.store) if publication == "close" else None
    cursor = first.store.cursor
    probe = WaitProbe(monkeypatch, first.store)
    streams = [first]
    # A reconnect at the completed current cursor enters the idle path even if
    # authoritative latest exists. Each admitted observer has its own cursor.
    first.stream.close()
    first.stream = first.adapter.Subscribe(pb.DisplayRequest(
        job_id="job", runtime_instance_id="runtime", after_cursor=cursor), first.context)
    for _ in range(subscribers - 1):
        streams.append(harness(monkeypatch, service=first.service, request=pb.DisplayRequest(
            job_id="job", runtime_instance_id="runtime", after_cursor=cursor)))
    workers = [inThread(lambda h=h: next(h.stream)) for h in streams]
    try:
        for _ in streams:
            probe.registration()
        with first.store.lock:
            if publication == "begin":
                beginScalar(first.store)
            else:
                finishScalar(first.store, key)
        messages = [delivery.result(GUARD) for _, delivery in workers]
        assert [probe.returned.get(timeout=GUARD) for _ in streams] == [True] * subscribers
        assert all(message == messages[0] for message in messages)
        assert messages[0].cursor == cursor + 1
        assert dict(messages[0].latest_started_ordinals) == {"root": 1}
        assert bool(messages[0].results) == (publication == "close")
        assert all(len(h.snapshots) == 2 and not h.gate.requested for h in streams)
    finally:
        for h in streams:
            h.context.cancelled.set()
        probe.close()
        for h, (thread, _) in zip(streams, workers):
            retire(h, thread)


def testSpuriousNotificationReentersWaitWithoutSnapshot(monkeypatch):
    h = harness(monkeypatch)
    probe = WaitProbe(monkeypatch, h.store, freezeDeadline=True)
    thread, delivery = inThread(lambda: next(h.stream))
    try:
        probe.registration()
        with h.store.lock:
            h.store.changed.notify_all()
        probe.registration()  # Same wait_for, after a real spurious wake.
        assert probe.returned.get(timeout=GUARD) is True
        assert len(h.snapshots) == 1 and not delivery.done()
        assert not h.gate.requested
        with h.store.lock:
            closeScalar(h.store, 1)
        assert delivery.result(GUARD).results[0].identity.result_ordinal == 1
        assert probe.returned.get(timeout=GUARD) is True
        assert len(h.snapshots) == 2
    finally:
        retire(h, thread, probe)


def testRealTimeoutReturnsFalseWithoutChangingStore():
    store = ResultStore()
    thread, returned = inThread(lambda: store.waitForChange(0))
    try:
        assert returned.result(GUARD) is False
        assert store.cursor == 0 and not store.events and not store.history
    finally:
        thread.join(GUARD)
        assert not thread.is_alive()


def testFiniteWaitObserverAllowsRemainingDeadline(monkeypatch):
    store = ResultStore()
    entered = threading.Event()
    originalWait = store.changed.wait
    originalWaitFor = store.changed.wait_for
    original = originalWaitFor.__func__
    originalClock = original.__globals__["_time"]
    # Model a timeout whose clock has advanced only 16 ms. Unchanged stdlib
    # wait_for must pass the remaining 4 ms to a second real finite wait.
    clock = iter([0.0, 0.016, 0.02])
    localWaitFor = FunctionType(original.__code__, dict(original.__globals__, _time=clock.__next__),
                               original.__name__, original.__defaults__, original.__closure__)
    requested = []
    returned = []

    def recordWait(timeout=None):
        assert entered.is_set()
        requested.append(timeout)
        result = originalWait(timeout)
        returned.append(result)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(store.changed, "wait_for", localWaitFor.__get__(store.changed))
        patch.setattr(store.changed, "wait", recordWait)
        observeFiniteWait(patch, store, entered)
        assert store.waitForChange(0) is False
    assert requested == [0.02, 0.02 - 0.016]
    assert returned == [False, False]
    assert store.cursor == 0 and not store.events and not store.history
    assert store.changed.wait == originalWait and store.changed.wait_for == originalWaitFor
    assert original.__globals__["_time"] is originalClock


@pytest.mark.parametrize("when", ["before_wait", "idle", "cooldown", "published"])
def testCancellationRetiresAtOriginalLoopBoundary(monkeypatch, when):
    h = harness(monkeypatch)
    entry = Gate()
    originalWait = h.store.waitForChange
    returns = []

    def observeWait(cursor):
        if when == "before_wait":
            entry.sleep(0.02)
        returns.append(originalWait(cursor))
        return returns[-1]

    monkeypatch.setattr(h.store, "waitForChange", observeWait)
    if when == "idle":
        observeFiniteWait(monkeypatch, h.store, entry.entered)
    if when == "cooldown":
        closeScalar(h.store, 1)
        assert next(h.stream).cursor == 2
    if when == "published":
        probe = WaitProbe(monkeypatch, h.store)
    else:
        probe = None
    thread, delivery = inThread(lambda: next(h.stream))
    try:
        if when == "cooldown":
            assert h.gate.entered.wait(GUARD)
            assert h.gate.requested == [0.02]
        elif probe is not None:
            probe.registration()
        else:
            assert entry.entered.wait(GUARD)
        with h.store.lock:
            if when == "published":
                closeScalar(h.store, 1)
            h.context.cancelled.set()
        entry.release.set()
        h.gate.release.set()
        with pytest.raises(StopIteration):
            delivery.result(GUARD)
        if when == "idle":
            # The real timeout can legitimately expire before the controller
            # runs. Empty snapshots are allowed; no result or yield is created.
            assert all(snapshot.cursor == 0 and not snapshot.results and not snapshot.reset_required
                       for snapshot in h.snapshots)
            assert not h.gate.requested and h.store.cursor == 0
        else:
            assert len(h.snapshots) == 1
        if when in {"before_wait", "idle"}:
            assert returns and returns[-1] is False
        if probe is not None:
            assert probe.returned.get(timeout=GUARD) is True
    finally:
        entry.release.set()
        retire(h, thread, probe)


@pytest.mark.parametrize("action", ["release", "close"])
def testRealServiceDisposalNeedsNoCursorNotification(channel, monkeypatch, action):
    channel.runtime.jobRepository.create(JobRecord("job", "project", 1, "main", status="COMPLETED"))
    with channel.store.lock:
        channel.jobs["job"] = {"scopeIds": []}
    h = harness(monkeypatch, service=channel)
    entered = threading.Event()
    observeFiniteWait(monkeypatch, h.store, entered)
    thread, delivery = inThread(lambda: next(h.stream))
    try:
        assert entered.wait(GUARD)
        if action == "release":
            channel.release("job")
        else:
            channel.close()
            assert channel.closed and not channel.monitor.is_alive()
        assert h.store.cursor == 0 and "job" not in channel.jobs
        with pytest.raises(RpcAbort) as error:
            delivery.result(GUARD)
        assert error.value.code == grpc.StatusCode.NOT_FOUND
    finally:
        retire(h, thread)


def testBeginOnlyWakeThenClosureStillRequiresFullCooldown(monkeypatch):
    h = harness(monkeypatch)
    probe = WaitProbe(monkeypatch, h.store)
    thread, delivery = inThread(lambda: next(h.stream))
    secondThread = None
    try:
        probe.registration()
        key = beginScalar(h.store)
        started = delivery.result(GUARD)
        assert started.cursor == 1 and not started.results
        assert dict(started.latest_started_ordinals) == {"root": 1}
        thread.join(GUARD)
        secondThread, closed = inThread(lambda: next(h.stream))
        assert h.gate.entered.wait(GUARD)
        finishScalar(h.store, key)
        assert h.store.cursor == 2
        assert not closed.done() and len(h.snapshots) == 2
        assert h.gate.requested == [0.02]
        h.gate.release.set()
        assert closed.result(GUARD).results[0].identity.result_key == key
        assert len(h.snapshots) == 3
    finally:
        retire(h, secondThread or thread, probe)


@pytest.mark.parametrize("burstSize", [4, 40])
def testPostYieldCooldownPreservesBurstReplayAndOverflow(monkeypatch, burstSize):
    h = harness(monkeypatch)
    closeScalar(h.store, 1)
    first = next(h.stream)
    assert first.cursor == 2 and not h.gate.requested
    thread, delivery = inThread(lambda: next(h.stream))
    try:
        assert h.gate.entered.wait(GUARD)
        assert h.gate.requested == [0.02]
        for ordinal in range(2, burstSize + 2):
            closeScalar(h.store, ordinal)
        assert not delivery.done() and len(h.snapshots) == 1
        expected = h.store.snapshot("job", first.cursor, incremental=True)
        assert len(h.store.events) <= 32 and len(h.store.history) <= 32
        assert len(h.store.latest) == 1 and h.store.metadataBytes() <= h.store.metadataLimit
        h.gate.release.set()
        message = delivery.result(GUARD)
        assert message.cursor == expected["cursor"] == 2 * (burstSize + 1)
        assert message.reset_required == expected["reset"] == (burstSize == 40)
        assert [r.identity.result_ordinal for r in message.results] == (
            [2, 3, 4, 5] if burstSize == 4 else [41])
    finally:
        retire(h, thread)


@pytest.mark.parametrize("cursor, runtimeId, reset", [(0, "runtime", False),
    (1, "runtime", True), (10**6, "runtime", True), (82, "old-runtime", True)])
def testReconnectKeepsIdentityResetAndQuietLatest(monkeypatch, cursor, runtimeId, reset):
    h = harness(monkeypatch, request=pb.DisplayRequest(
        job_id="job", runtime_instance_id=runtimeId, after_cursor=cursor))
    closeScalar(h.store, 1, "quiet")
    for ordinal in range(1, 41):
        closeScalar(h.store, ordinal, "fast")
    message = next(h.stream)
    try:
        assert message.cursor == 82 and message.reset_required == reset
        assert {r.identity.result_scope_id: r.identity.result_ordinal for r in message.results} == {
            "quiet": 1, "fast": 40}
        assert not h.gate.requested
        # A health snapshot at the accepted cursor recovers a lost final body.
        recovery = h.adapter.Snapshot(pb.DisplayRequest(job_id="job", runtime_instance_id="runtime",
            after_cursor=82, replay=True), h.context)
        assert recovery.results == message.results
    finally:
        h.stream.close()


def testNotificationObservesPostTrimExpiryAtomically(monkeypatch):
    h = harness(monkeypatch)
    closeScalar(h.store, 1, "quiet", "q" * 128)
    h.store.metadataLimit = h.store.metadataBytes() + 16  # Lower test-only cap, never enlarge it.
    before = h.store.cursor
    h.stream.close()
    h.stream = h.adapter.Subscribe(pb.DisplayRequest(job_id="job", runtime_instance_id="runtime",
        after_cursor=before), h.context)
    probe = WaitProbe(monkeypatch, h.store)
    thread, delivery = inThread(lambda: next(h.stream))
    try:
        probe.registration()
        with h.store.lock:
            closeScalar(h.store, 1, "fast", "f" * 128)
        message = delivery.result(GUARD)
        assert probe.returned.get(timeout=GUARD) is True
        assert message.reset_required and dict(message.expired_scope_ordinals) == {"quiet": 1}
        assert [r.identity.result_scope_id for r in message.results] == ["fast"]
        assert h.store.metadataBytes() <= h.store.metadataLimit
        assert not h.store.events and not h.store.history
    finally:
        retire(h, thread, probe)


def loopState(server):
    async def read():
        return dict(server.active), len(server.cleanups)
    return asyncio.run_coroutine_threadsafe(read(), server.loop).result(GUARD)


def awaitRetirement(server):
    async def drained():
        while any(server.active.values()) or server.cleanups:
            await asyncio.sleep(0.001)
    asyncio.run_coroutine_threadsafe(drained(), server.loop).result(GUARD)


def testActualAioCancellationKeepsTwoAdmissionsUntilWaitersRetire(monkeypatch):
    service = SimpleNamespace(store=ResultStore(), jobs={"job": {}},
                              runtimeInstanceId="runtime", supportsNormalCapture=False)
    probe = WaitProbe(monkeypatch, service.store)
    retiring = queue.Queue()
    originalRetire = AioRuntimeServer._retire

    def observeRetire(self, category, token, *args, **kwargs):
        originalRetire(self, category, token, *args, **kwargs)
        if category == "display":
            retiring.put(token)

    monkeypatch.setattr(AioRuntimeServer, "_retire", observeRetire)
    server = AioRuntimeServer(rpc.RuntimeServiceServicer(), service)
    transport = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    calls = []
    try:
        stub = rpc.DisplayServiceStub(transport)
        calls = [stub.Subscribe(pb.DisplayRequest(job_id="job", runtime_instance_id="runtime"))
                 for _ in range(2)]
        for _ in calls:
            probe.registration()
        assert loopState(server)[0]["display"] == 2
        for call in calls:
            call.cancel()
        tokens = [retiring.get(timeout=GUARD) for _ in calls]
        assert all(token.cancelled.is_set() for token in tokens)
        assert loopState(server)[0]["display"] == 2
        with pytest.raises(grpc.RpcError) as error:
            next(stub.Subscribe(pb.DisplayRequest(job_id="job", runtime_instance_id="runtime"), timeout=GUARD))
        assert error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
        assert stub.Capabilities(pb.DisplayEmpty(), timeout=GUARD).protocol_version == "1.0"
        probe.close()
        awaitRetirement(server)
        assert loopState(server) == ({key: 0 for key in server.active}, 0)
        assert server.peak["display"] == 2 and not server.errors
    finally:
        probe.close()
        for call in calls:
            call.cancel()
        transport.close()
        server.close()
    assert not server.thread.is_alive()


def testActualAioCloseRetiresIdleSubscriptionWithRealTimeout(monkeypatch):
    service = SimpleNamespace(store=ResultStore(), jobs={"job": {}},
                              runtimeInstanceId="runtime", supportsNormalCapture=False)
    entered = threading.Event()
    observeFiniteWait(monkeypatch, service.store, entered)
    server = AioRuntimeServer(rpc.RuntimeServiceServicer(), service)
    transport = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    call = rpc.DisplayServiceStub(transport).Subscribe(pb.DisplayRequest(
        job_id="job", runtime_instance_id="runtime"))
    closed = False
    try:
        assert entered.wait(GUARD)
        server.close()
        closed = True
        assert not any(server.active.values()) and not server.cleanups and not server.errors
        assert not server.thread.is_alive() and service.store.cursor == 0
    finally:
        call.cancel()
        transport.close()
        if not closed:
            server.close()
