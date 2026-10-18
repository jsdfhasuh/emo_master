from threading import Event

import numpy as np
import pytest

from emo_master.apps.runtime.devices.executor import DeviceExecutor, DeviceError
from emo_master.apps.runtime.devices.frame_store import FrameStore, copyFrame


def testFifoCancellationAndIndependentDevice():
    first, other = DeviceExecutor("first"), DeviceExecutor("other")
    entered, release = Event(), Event()
    calls = []
    def held(*args):
        entered.set()
        assert release.wait(3)
        calls.append(1)
    try:
        a = first.submit("a", held)
        assert entered.wait(1)
        b = first.submit("b", lambda *_: calls.append(2))
        c = first.submit("c", lambda *_: calls.append(3))
        first.cancel(b)
        assert other.wait(other.submit("d", lambda *_: 4)) == 4
        with pytest.raises(DeviceError) as error:
            first.wait(b)
        assert error.value.code == "E_DEVICE_CANCELLED"
        release.set()
        first.wait(a)
        first.wait(c)
        assert calls == [1, 3]
    finally:
        release.set()
        first.close()
        other.close()


def testTimeoutDoesNotReleaseNativeOwnerAndCloseIsRetryable():
    executor = DeviceExecutor("native")
    entered, release, nextEntered = Event(), Event(), Event()
    def native(*args):
        entered.set()
        release.wait(3)
    try:
        active = executor.submit("job", native, operationTimeout=.02)
        assert entered.wait(1)
        following = executor.submit("other", lambda *_: nextEntered.set())
        with pytest.raises(DeviceError) as error:
            executor.wait(active)
        assert error.value.code == "E_DEVICE_OPERATION_TIMEOUT" and error.value.outcome == "UNKNOWN"
        assert not nextEntered.is_set()
        with pytest.raises(DeviceError, match="not exited"):
            executor.close(timeout=.01)
        assert following.future.done()
        with pytest.raises(DeviceError) as cancelled:
            following.future.result()
        assert cancelled.value.code == "E_DEVICE_CANCELLED"
        release.set()
    finally:
        release.set()
        executor.close()


def testQueueExpiryDoesNotExecuteAndDoesNotQuarantine():
    executor = DeviceExecutor("queue")
    entered, release = Event(), Event()
    try:
        a = executor.submit("a", lambda *_: (entered.set(), release.wait(3)))
        assert entered.wait(1)
        b = executor.submit("b", lambda *_: pytest.fail("expired request ran"), waitTimeout=.01)
        with pytest.raises(DeviceError) as error:
            executor.wait(b)
        assert error.value.code == "E_DEVICE_WAIT_TIMEOUT"
        assert not executor.quarantined
        release.set()
        executor.wait(a)
    finally:
        release.set()
        executor.close()


def testUnhealthyFailureRejectsFollowingOperation():
    executor = DeviceExecutor("broken")
    def fail(*args):
        raise DeviceError("E_NATIVE", "native state unknown", unhealthy=True, outcome="UNKNOWN")
    try:
        with pytest.raises(DeviceError):
            executor.wait(executor.submit("a", fail))
        with pytest.raises(DeviceError, match="quarantined"):
            executor.submit("b", lambda *_: None)
    finally:
        executor.close()


@pytest.mark.parametrize("shape", [(5, 7), (5, 7, 3)])
def testOwnedOriginalCopyAndLeases(shape):
    store = FrameStore(maxFrames=1, maxBytes=200)
    source = np.arange(np.prod(shape), dtype=np.uint8).reshape(shape)[:, ::-1]
    source.setflags(write=False)
    reservation = store.reserve("source", source.nbytes)
    descriptor = store.publish(reservation, source, cycleId="cycle", generation="generation", requestId="request")
    other = store.retain(descriptor["frameId"], "source", "consumer")
    np.testing.assert_array_equal(copyFrame(descriptor), source)
    copyFrame(descriptor).fill(0)
    store.release(descriptor["frameId"], "source")
    with pytest.raises(DeviceError, match="capacity"):
        store.reserve("new", 1)
    with pytest.raises(DeviceError, match="readers"):
        store.close()
    np.testing.assert_array_equal(copyFrame(other), source)
    store.retireOwner("consumer")
    store.close()
    assert not store.frames


def testFrameByteBudgetAndDistinctIds():
    store = FrameStore(maxFrames=3, maxBytes=4)
    reservation = store.reserve("owner", 4)
    with pytest.raises(DeviceError):
        store.reserve("second", 1)
    store.discardReservation(reservation)
    ids = []
    for _ in range(2):
        descriptor = store.publish(store.reserve("owner", 1), np.zeros((1, 1), np.uint8),
                                   cycleId="same", generation="reset", requestId="request")
        ids.append(descriptor["frameId"])
        store.retireOwner("owner")
    assert len(set(ids)) == 2
    store.close()
