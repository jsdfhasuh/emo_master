from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event, get_ident
from time import monotonic, sleep, time

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.runtime.devices import rtsp
from emo_master.apps.runtime.devices.frame_store import copyFrame
from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.core.contracts.device_driver import DeviceError, DeviceOperationContext
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.rtsp_capture.operator import RtspCaptureOperator
from tests.runtime.rtsp_fixture import FakeCapture, definition, project, scan


def waitFor(check, timeout=2):
    until = monotonic() + timeout
    while monotonic() < until:
        result = check()
        if result:
            return result
        sleep(.005)
    raise AssertionError('condition did not become true')


def capture(driver, *, timeout=1, cancelled=lambda: False, **params):
    return driver.execute('capture', dict(driver.definition.defaults, **params), cancelled, monotonic() + timeout,
        arguments={}, operationContext=DeviceOperationContext(ownerId='job', cycleId='cycle', planRevision=1))


@pytest.fixture
def driver(monkeypatch):
    native = FakeCapture()
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', lambda: native)
    driver = rtsp.ManagedRtsp(definition())
    yield driver, native
    driver.close(monotonic() + 2)


def testRegistrationConstructionAndValidationNeverConnect(monkeypatch):
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', lambda: pytest.fail('unexpected connection'))
    result = scan()
    assert not result.rejectedDeviceTypes
    assert 'vision.io.rtsp_capture' in result.activeOperators
    assert 'vision.io.rtsp_camera' in result.activeOperators
    from emo_master.apps.runtime.devices.drivers import BUILTINS, validateDriver
    declaration = result.deviceTypes['device.camera.rtsp']
    assert validateDriver(declaration) is rtsp.ManagedRtsp
    assert declaration.driverEntry in BUILTINS
    d = rtsp.ManagedRtsp(definition())
    assert d.physicalIdentity(d.definition).startswith('rtsp://')
    assert not d.snapshot()['ownsResources']
    d.dispose()


def testConnectionTestDoesNotReadAndDecoderOwnsItsRelease(driver):
    d, native = driver
    assert d.testConnection(d.definition.defaults, lambda: False, monotonic() + 1) == {'connected': True}
    assert not native.readStarted.wait(.03)
    assert d.snapshot()['state'] == 'CONNECTED'
    d.close(monotonic() + 1)
    assert native.released.is_set()
    assert [item[0] for item in native.calls] == ['open', 'release']
    assert len({item[1] for item in native.calls}) == 1
    assert native.calls[0][1] != get_ident()
    assert not d.snapshot()['ownsResources']


def testSlowConsumerGetsLatestFrameAndOriginalReceiveTime(driver):
    d, native = driver
    d.connect({}, lambda: False, monotonic() + 1)
    native.put(1)
    first = capture(d)
    for value in range(2, 21):
        native.put(value)
    waitFor(lambda: d.sequence == 20)
    sleep(.025)
    before = time()
    second = capture(d)
    assert np.all(first.image == 1) and np.all(second.image == 20)
    assert second.metadata['sequence'] == 20
    assert second.metadata['capturedAt'] < before
    assert second.metadata['receivedAgeMs'] >= 20
    assert d.snapshot()['cachedFrames'] == 1
    assert len([call for call in native.calls if call[0] == 'open']) == 1
    with pytest.raises(DeviceError) as caught:
        capture(d, timeout=.04)
    assert caught.value.code == 'E_CAMERA_FRAME_TIMEOUT'


def testStaleUnconsumedFrameIsNotReturned(driver):
    d, native = driver
    d.connect({}, lambda: False, monotonic() + 1)
    native.put(1)
    capture(d)
    native.put(2)
    waitFor(lambda: d.sequence == 2)
    sleep(.03)
    with pytest.raises(DeviceError) as caught:
        capture(d, timeout=.04, maxFrameAgeMs=10)
    assert caught.value.code == 'E_CAMERA_FRAME_TIMEOUT'
    native.put(3)
    result = capture(d, colorMode='grayscale', maxFrameAgeMs=10)
    assert result.image.shape == (5, 7) and np.all(result.image == 3)


def testReadFailureClearsCacheAndSanitizesNativeError(driver):
    d, native = driver
    d.connect({}, lambda: False, monotonic() + 1)
    native.put(1)
    capture(d)
    native.frames.put(cv2.error('rtsp://fixture:fixture-password@private/live?fixture-token'))
    waitFor(lambda: d.snapshot()['state'] == 'FAILED')
    with pytest.raises(DeviceError) as caught:
        capture(d)
    assert caught.value.code == 'E_CAMERA_READ' and caught.value.unhealthy
    assert d.snapshot()['connectionState'] == 'FAULTED'
    assert not d.snapshot()['cachedFrames']
    assert 'fixture-password' not in str(caught.value) + json.dumps(d.snapshot())
    assert 'fixture-token' not in str(caught.value) + json.dumps(d.snapshot())


def testCancellationDuringFrameWaitDoesNotStopSharedReader(driver):
    d, native = driver
    d.connect({}, lambda: False, monotonic() + 1)
    stop = Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(capture, d, cancelled=stop.is_set)
        assert native.readStarted.wait(1)
        stop.set()
        with pytest.raises(DeviceError) as caught:
            running.result(1)
    assert caught.value.code == 'E_DEVICE_CANCELLED'
    assert not native.released.is_set()
    native.put(7)
    assert np.all(capture(d).image == 7)


def testStopWaitsForNativeExitAndNeverReleasesOnCallerThread(driver):
    d, native = driver
    d.connect({}, lambda: False, monotonic() + 1)
    native.put(1)
    capture(d)
    waitFor(lambda: len([c for c in native.calls if c[0] == 'read']) == 2)
    with pytest.raises(DeviceError) as caught:
        d.close(monotonic() + .005)
    assert caught.value.code == 'E_DEVICE_RELEASE_PENDING'
    assert d.snapshot()['ownsResources']
    assert not native.released.is_set()
    native.put(2)
    d.close(monotonic() + 1)
    assert not d.snapshot()['ownsResources']
    assert len({call[1] for call in native.calls}) == 1


def testCancellationBeforeOpenDoesNotCreateDecoder(driver):
    d, native = driver
    with pytest.raises(DeviceError) as caught:
        d.connect({}, lambda: True, monotonic() + 1)
    assert caught.value.code == 'E_DEVICE_CANCELLED'
    assert not native.calls


def testCancelledOpenReleasesAfterNativeOpenReturns(monkeypatch):
    native = FakeCapture()
    entered, releaseOpen, cancel = Event(), Event(), Event()
    original = native.open
    def blocked(*args):
        entered.set()
        assert releaseOpen.wait(2)
        return original(*args)
    native.open = blocked
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', lambda: native)
    d = rtsp.ManagedRtsp(definition())
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            operation = pool.submit(d.connect, {}, cancel.is_set, monotonic() + 1)
            assert entered.wait(1)
            cancel.set()
            with pytest.raises(DeviceError) as caught:
                operation.result(1)
            assert caught.value.code == 'E_DEVICE_CANCELLED'
            assert d.snapshot()['ownsResources']
    finally:
        releaseOpen.set()
        d.close(monotonic() + 2)
    assert native.released.is_set() and not native.readStarted.is_set()


@pytest.mark.parametrize('opened', [False, 'exception'])
def testOpenFailureIsActionableAndDoesNotLeakUrl(monkeypatch, opened):
    native = FakeCapture(opened=opened)
    if opened == 'exception':
        def fail(*_args):
            raise cv2.error('fixture-password fixture-token')
        native.open = fail
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', lambda: native)
    d = rtsp.ManagedRtsp(definition())
    try:
        with pytest.raises(DeviceError) as caught:
            d.connect({}, lambda: False, monotonic() + 1)
        assert caught.value.code == 'E_CAMERA_CONNECT'
        assert 'fixture-password' not in str(caught.value)
        assert native.released.wait(1)
    finally:
        d.close(monotonic() + 1)


def testEnvironmentPasswordAndSecretFreeIdentity(driver, monkeypatch):
    d, native = driver
    d.definition.connection.update(username='fixture user', password='unused', passwordEnv='RTSP_TEST_SECRET',
                                   rtspUrl='rtsp://camera.invalid/live?token=fixture-token')
    monkeypatch.setenv('RTSP_TEST_SECRET', 'fixture@password')
    d.connect({}, lambda: False, monotonic() + 1)
    assert 'fixture%20user:fixture%40password@' in native.calls[0][2]
    identity = d.physicalIdentity(d.definition)
    assert 'fixture' not in identity
    d.close(monotonic() + 1)
    monkeypatch.delenv('RTSP_TEST_SECRET')
    with pytest.raises(DeviceError) as caught:
        d.connect({}, lambda: False, monotonic() + 1)
    assert caught.value.code == 'E_DEVICE_PARAMETERS'
    assert len([call for call in native.calls if call[0] == 'open']) == 1


def testOversizedDecodedFrameCannotEnterStore(driver):
    d, native = driver
    d.connect({}, lambda: False, monotonic() + 1)
    native.frames.put(np.zeros((100, 100, 3), np.uint8))
    with pytest.raises(DeviceError) as caught:
        capture(d)
    assert caught.value.code == 'E_CAMERA_FRAME_SIZE'
    assert not d.snapshot()['cachedFrames']


def testNewProjectRoundtripAndStrictCompilation(tmp_path):
    registry = scan().activeOperators
    document = project()
    assert document.schemaVersion == '2.7' and document.devices.schemaVersion == '1.0'
    path = tmp_path / 'managed-rtsp.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    restored = loadProjectDocument(path)
    assert restored == document
    WorkflowCompiler(operatorRegistry=registry, deviceTypes=scan().deviceTypes).compile(restored)
    old = project(legacy=True)
    WorkflowCompiler(operatorRegistry=registry).compile(old)
    oldPorts = registry['vision.io.rtsp_camera'].manifest.outputPorts
    assert registry['vision.io.rtsp_capture'].manifest.outputPorts == oldPorts


def testOperatorRequiresManagedRuntimeAndKeepsCapturedTimestamp():
    operator = RtspCaptureOperator()
    assert operator.executeNode({}, {}, {})['error']['code'] == 'E_DEVICE_RUNTIME_UNAVAILABLE'
    assert operator.validateParams({'rtspUrl': 'rtsp://camera.invalid/live'})
    image = np.ones((5, 7, 3), np.uint8)
    descriptor = dict(frameId='f', acquisition=dict(capturedAt=1234.5, sourceId='camera'))
    slots = []
    def proxy(slot):
        slots.append(slot)
        return image, descriptor
    result = operator.executeNode({}, {}, {'captureDevice': proxy})
    assert slots == ['camera']
    assert result['outputs']['image'] is image
    assert result['outputs']['capturedAt'] == 1234.5
    assert result['outputs']['frame']['coordinateSpace']['imageWidth'] == 7


def testManagerPublishesAcquisitionMetadataAndSharedOwners(monkeypatch):
    instances = []
    def factory():
        native = FakeCapture(automatic=True)
        instances.append(native)
        return native
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', factory)
    manager = DeviceManager(scan().deviceTypes)
    try:
        manager.load(project())
        assert not instances
        for owner in ('a', 'b'):
            manager.authorize(owner, ['camera'])
        params = manager.definitions['camera'].defaults
        first = manager.execute('a', 'camera', 'capture', params, cycleId='a')
        manager.retire('a')
        second = manager.execute('b', 'camera', 'capture', params, cycleId='b')
        assert len(instances) == 1 and not instances[0].released.is_set()
        assert second['acquisition']['sequence'] > first['acquisition']['sequence']
        assert copyFrame(second).shape == (5, 7, 3)
        manager.retire('b')
        assert not manager.frames.frames and not manager.frames.reservations
    finally:
        manager.close()
    assert instances[0].released.is_set()


def testConnectionTestThroughManagerDoesNotAcquire(monkeypatch):
    native = FakeCapture()
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', lambda: native)
    manager = DeviceManager(scan().deviceTypes)
    try:
        result = manager.testConnection(definition())
        assert result['connected']
        assert native.released.is_set() and not native.readStarted.is_set()
        assert not manager.drivers and not manager.frames.frames
    finally:
        manager.close()


def testIdleReleaseAndNextUseReconnect(monkeypatch):
    instances = []
    def factory():
        native = FakeCapture(automatic=True)
        instances.append(native)
        return native
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', factory)
    document = project()
    document.devices.definitions['camera'].policies['idleTimeoutMs'] = 30
    manager = DeviceManager(scan().deviceTypes)
    try:
        manager.load(document)
        manager.authorize('job', ['camera'])
        params = manager.definitions['camera'].defaults
        manager.execute('job', 'camera', 'capture', params, cycleId='first')
        waitFor(lambda: not manager.lifecycles['camera'].connected)
        assert instances[0].released.is_set()
        manager.execute('job', 'camera', 'capture', params, cycleId='second')
        assert len(instances) == 2
        manager.retire('job')
    finally:
        manager.close()


def testReadFailureCanBeRecoveredWithoutRestartingRuntime(monkeypatch):
    broken, healthy = FakeCapture(), FakeCapture(automatic=True)
    instances = iter((broken, healthy))
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', lambda: next(instances))
    manager = DeviceManager(scan().deviceTypes)
    try:
        manager.load(project())
        manager.authorize('first', ['camera'])
        params = manager.definitions['camera'].defaults
        broken.frames.put(None)
        with pytest.raises(DeviceError) as caught:
            manager.execute('first', 'camera', 'capture', params, cycleId='first')
        assert caught.value.code == 'E_CAMERA_READ'
        assert manager.executors['camera'].quarantined
        manager.retire('first')
        generation = manager.executors['camera'].generation
        manager.recover('camera', generation)
        assert broken.released.is_set()
        assert manager.executors['camera'].generation != generation
        manager.authorize('second', ['camera'])
        descriptor = manager.execute('second', 'camera', 'capture', params, cycleId='second')
        assert copyFrame(descriptor).shape == (5, 7, 3)
        manager.retire('second')
    finally:
        manager.close()


def testNativeFfmpegDecoderRunsAndClosesOnReaderThread(tmp_path, monkeypatch):
    path = tmp_path / 'local-decoder.avi'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 20, (32, 24))
    assert writer.isOpened()
    for _ in range(30):
        writer.write(np.full((24, 32, 3), 80, np.uint8))
    writer.release()
    factory = cv2.VideoCapture
    threads = []
    class LocalDecoder:
        def __init__(self):
            self.native = factory()
        def open(self, _url, backend, options):
            threads.append(get_ident())
            return self.native.open(str(path), backend, options)
        def read(self):
            threads.append(get_ident())
            sleep(.01)
            return self.native.read()
        def release(self):
            threads.append(get_ident())
            self.native.release()
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', LocalDecoder)
    d = rtsp.ManagedRtsp(definition(connection={'maxFrameBytes': 4096}))
    try:
        d.connect({}, lambda: False, monotonic() + 2)
        result = capture(d)
        assert result.image.shape == (24, 32, 3)
        assert np.max(np.abs(result.image.astype(int) - 80)) <= 2
    finally:
        d.close(monotonic() + 2)
    assert len(set(threads)) == 1 and threads[0] != get_ident()


def testTwoSpawnedJobsUseParentRuntimeDecoder(tmp_path, monkeypatch):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.runtime.runtime_test_utils import waitForTerminal
    instances = []
    def factory():
        native = FakeCapture(automatic=True)
        instances.append(native)
        return native
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', factory)
    path = tmp_path / 'managed-rtsp.emoproj'
    saveProject(path, project().model_dump(mode='json'))
    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    try:
        reply = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert reply.ok, reply.message
        assert not instances
        starts = [service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId, workflow_id='main'), None)
                  for _ in range(2)]
        assert all(reply.ok for reply in starts), starts
        for reply in starts:
            result = waitForTerminal(service, reply.job_id, timeoutSeconds=30)
            assert result.status == 'COMPLETED', result
        def retired():
            service.jobSupervisor.finishRetirements()
            return not service.deviceManager.workers
        waitFor(retired, timeout=5)
        assert len(instances) == 1
        assert not service.deviceManager.frames.frames and not service.deviceManager.frames.reservations
    finally:
        service.close()
    assert instances[0].released.is_set()


def testSpawnedWhileReusesConnectionBeyondFrameCapacityAndStops(tmp_path, monkeypatch):
    from emo_master.apps.designer.state.workflow_store import WorkflowStore
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    native = FakeCapture(automatic=True)
    opens = []
    def factory():
        opens.append(1)
        return native
    monkeypatch.setattr(rtsp.cv2, 'VideoCapture', factory)
    payload = project().model_dump(mode='json')
    body = payload['workflows'].pop('main')
    body['outputs'], body['edges'] = {}, []
    body['nodes'][-1]['inputPorts'] = {}
    main = WorkflowStore().toPayload()['workflows']['main']
    main['nodes'].append(dict(nodeId='loop', kind='loop', loop=dict(contractVersion=2, mode='while',
        bodyWorkflowId='body', conditionMode='globalVariable', conditionVariableId='run',
        unlimited=True, maxIterations=1, timeoutMs=0)))
    payload['workflows'] = dict(main=main, body=body)
    payload['workflowOrder'] = ['main', 'body']
    payload['globalVariables']['run'] = dict(name='Run', type='boolean', initialValue=True, kind='constant', lifetime='job')
    payload['devices']['definitions']['camera']['policies']['idleTimeoutMs'] = 500
    path = tmp_path / 'while-rtsp.emoproj'
    saveProject(path, payload)
    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        driver = service.deviceManager.drivers['camera']
        started = service.StartJob(pb.StartJobRequest(workflow_id='main'), None)
        assert started.ok, started.message
        waitFor(lambda: driver.delivered >= 40, timeout=20)
        assert len(opens) == 1
        assert len(service.deviceManager.frames.frames) <= 1
        assert service.StopJob(pb.StopJobRequest(job_id=started.job_id, mode='graceful'), None).ok
        waitFor(lambda: not service.jobSupervisor.ownsJobResources(started.job_id), timeout=20)
        record = service.jobRepository.get(started.job_id)
        assert (record.status, record.errorCode) in {
            ('ABORTED', 'E_CANCELLED'), ('FAILED', 'E_DEVICE_CANCELLED'), ('FAILED', 'E_DEVICE_OUTCOME_UNKNOWN')}, record
        waitFor(lambda: native.released.is_set())
        assert not service.deviceManager.frames.frames and not service.deviceManager.frames.reservations
        assert not driver.snapshot()['ownsResources']
    finally:
        service.close()
