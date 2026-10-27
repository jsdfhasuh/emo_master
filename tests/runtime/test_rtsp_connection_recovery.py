from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import numpy as np
import pytest

from emo_master.apps.runtime.devices import rtsp
from emo_master.apps.runtime.devices.frame_store import copyFrame
from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.core.contracts.device_driver import DeviceError
from tests.runtime.rtsp_fixture import FakeCapture, definition, project, scan


@pytest.fixture
def manager():
    value = DeviceManager(scan().deviceTypes)
    value.load(project())
    value.authorize('job', ['camera'])
    try:
        yield value
    finally:
        value.retire('job')
        value.close()


def capture(manager, *, cycle='cycle', cancelled=lambda: False):
    return manager.execute('job', 'camera', 'capture', manager.definitions['camera'].defaults,
                           cycleId=cycle, cancelled=cancelled)


def waitFor(predicate):
    deadline = monotonic() + 2
    while not predicate() and monotonic() < deadline:
        sleep(.005)
    assert predicate()


@pytest.mark.parametrize('phase', ['connect', 'read'])
def testTransientFailureReconnectsAndReturnsOnlyFreshFrame(manager, monkeypatch, phase):
    broken = FakeCapture(opened=phase != 'connect')
    broken.frames.put(None)
    healthy = FakeCapture(automatic=True)
    instances = iter((broken, healthy))
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', lambda: next(instances))
    result = capture(manager)
    assert copyFrame(result).shape == (5, 7, 3)
    assert broken.released.is_set() and not healthy.released.is_set()
    assert not manager.executors['camera'].quarantined
    assert len([call for call in broken.calls + healthy.calls if call[0] == 'open']) == 2
    for native in (broken, healthy):
        assert len({call[1] for call in native.calls}) == 1


def testEstablishedStreamReconnectsWithoutReplacingPreviousFrame(manager, monkeypatch):
    broken, healthy = FakeCapture(), FakeCapture()
    broken.put(7)
    healthy.put(9)
    instances = iter((broken, healthy))
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', lambda: next(instances))
    generation = manager.executors['camera'].generation
    first = capture(manager, cycle='before-disconnect')
    broken.frames.put(None)
    waitFor(lambda: manager.drivers['camera'].snapshot()['lastErrorCode'] == 'E_CAMERA_READ')
    second = capture(manager, cycle='after-disconnect')
    assert np.all(copyFrame(first) == 7) and np.all(copyFrame(second) == 9)
    assert second['acquisition']['sequence'] > first['acquisition']['sequence']
    assert second['acquisition']['capturedAt'] >= first['acquisition']['capturedAt']
    assert manager.executors['camera'].generation == generation
    assert broken.released.is_set() and not manager.executors['camera'].quarantined


@pytest.mark.parametrize('phase', ['connect', 'read'])
def testExhaustedRecoveryCanRetryOnNextRequest(manager, monkeypatch, phase):
    available = Event()
    instances = []

    def factory():
        native = FakeCapture(opened=available.is_set() or phase != 'connect', automatic=available.is_set())
        if not available.is_set():
            native.frames.put(None)
        instances.append(native)
        return native

    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', factory)
    with pytest.raises(DeviceError) as caught:
        capture(manager)
    assert caught.value.code == ('E_CAMERA_CONNECT' if phase == 'connect' else 'E_CAMERA_READ')
    assert len(instances) == 4
    assert not manager.executors['camera'].quarantined
    available.set()
    result = capture(manager, cycle='after-network-returned')
    assert copyFrame(result).shape == (5, 7, 3)
    assert len(instances) == 5 and all(item.released.is_set() for item in instances[:-1])


def testConnectionTestRetriesWithoutReadingFrames(monkeypatch):
    broken, healthy = FakeCapture(opened=False), FakeCapture()
    instances = iter((broken, healthy))
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', lambda: next(instances))
    manager = DeviceManager(scan().deviceTypes)
    try:
        assert manager.testConnection(definition())['connected']
        assert broken.released.is_set() and healthy.released.is_set()
        assert not broken.readStarted.is_set() and not healthy.readStarted.is_set()
        assert not manager.drivers and not manager.frames.frames
    finally:
        manager.close()


def testInvalidFrameIsNotRetried(manager, monkeypatch):
    broken = FakeCapture()
    broken.frames.put(np.zeros((5, 7), np.uint8))
    calls = []
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', lambda: calls.append(1) or broken)
    with pytest.raises(DeviceError) as caught:
        capture(manager)
    assert caught.value.code == 'E_CAMERA_FORMAT'
    assert calls == [1] and manager.executors['camera'].quarantined


@pytest.mark.parametrize('action', ['cancel', 'disconnect'])
def testStopDuringBackoffDoesNotReconnect(manager, monkeypatch, action):
    manager.definitions['camera'].policies['recoveryBackoffMs'] = 1000
    broken = FakeCapture()
    broken.frames.put(None)
    calls = []
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', lambda: calls.append(1) or broken)
    cancelled = Event()
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(capture, manager, cancelled=cancelled.is_set)
        waitFor(lambda: manager.drivers['camera'].snapshot()['recovery']['state'] == 'BACKOFF')
        if action == 'cancel':
            cancelled.set()
        else:
            executor, operation = manager.beginControl('camera', manager.executors['camera'].generation, 'disconnect')
        with pytest.raises(DeviceError) as caught:
            pending.result(2)
        assert caught.value.code == 'E_DEVICE_CANCELLED'
        if action == 'disconnect':
            executor.wait(operation)
            with pytest.raises(DeviceError) as rejected:
                capture(manager)
            assert rejected.value.code == 'E_DEVICE_PAUSED'
        waitFor(lambda: manager.executors['camera'].active is None)
    assert calls == [1] and broken.released.is_set()
    assert not manager.executors['camera'].quarantined
    assert not manager.lifecycles['camera'].connected
    assert manager.drivers['camera'].snapshot()['recovery']['state'] == 'CANCELLED'
    assert not manager.frames.frames and not manager.frames.reservations


def testReconnectWaitsForPreviousDecoderRelease(manager, monkeypatch):
    broken, healthy = FakeCapture(), FakeCapture(automatic=True)
    broken.frames.put(None)
    entered, release = Event(), Event()
    originalRelease = broken.release

    def heldRelease():
        entered.set()
        assert release.wait(3)
        originalRelease()

    broken.release = heldRelease
    instances = []

    def factory():
        if instances:
            assert broken.released.is_set()
        value = healthy if instances else broken
        instances.append(value)
        return value

    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', factory)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(capture, manager)
        try:
            assert entered.wait(1)
            assert instances == [broken] and not pending.done()
        finally:
            release.set()
        assert copyFrame(pending.result(2)).shape == (5, 7, 3)
    assert instances == [broken, healthy]


@pytest.mark.parametrize('phase', ['connect', 'read'])
def testRecoveryDeadlineDoesNotQuarantineReleasedSession(manager, monkeypatch, phase):
    manager.definitions['camera'].policies.update(recoveryTimeoutMs=20, recoveryBackoffMs=50)
    native = FakeCapture(opened=phase == 'read')
    native.frames.put(None)
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', lambda: native)
    with pytest.raises(DeviceError) as caught:
        capture(manager)
    assert caught.value.code == ('E_DEVICE_RECOVERY_TIMEOUT' if phase == 'connect' else 'E_CAMERA_CONNECT_TIMEOUT')
    assert native.released.is_set()
    assert not manager.executors['camera'].quarantined
    assert not manager.lifecycles['camera'].connected
