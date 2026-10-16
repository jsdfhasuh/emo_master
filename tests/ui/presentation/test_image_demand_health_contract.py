"""A real health deadline clears the GUI without discarding reusable pixels."""
from collections import deque
import json
import threading
import time

import grpc

from emo_master.apps.runtime.presentation.rpc import DisplayRpc
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.renderer import RuntimePages
from tests.runtime.presentation.test_image_demand_client import demandBackend, idle
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.ui.presentation.test_image_demand_views import imageReady
from tests.ui.presentation.test_real import until


def testOneHealthDeadlineClearsShownImageThenReusesSameResult(qtApp, tmp_path, monkeypatch):
    armed = threading.Event()
    serverEntered = threading.Event()
    releaseServer = threading.Event()
    serverReturned = threading.Event()
    allowRecovery = threading.Event()
    timeline = deque(maxlen=16)
    rpcTimeouts = deque(maxlen=8)
    selected = {}
    originalServerSnapshot = DisplayRpc.Snapshot

    def mark(stage):
        timeline.append((stage, time.monotonic_ns()))

    def gatedServerSnapshot(self, request, context):
        matches = (request.runtime_instance_id, request.job_id) == selected.get('identity')
        if armed.is_set() and matches and not serverEntered.is_set():
            serverEntered.set()
            mark('server_entered')
            try:
                # Leave the real RPC unanswered beyond its unchanged 0.5s
                # client deadline. This is a latch, not a timing sleep.
                assert releaseServer.wait(20), 'test server release latch deadline'
                return originalServerSnapshot(self, request, context)
            finally:
                mark('server_returned')
                serverReturned.set()
        return originalServerSnapshot(self, request, context)

    monkeypatch.setattr(DisplayRpc, 'Snapshot', gatedServerSnapshot)
    with demandBackend(tmp_path, monkeypatch, count=1) as backend:
        session = DisplaySession(backend.address, backend.jobId, imageDemand=True)
        hub = DisplayHub(session)
        window = RuntimePages(backend.project.presentation, hub=hub)
        window.show()
        try:
            assert waitForTerminal(backend.runtime, backend.jobId).status == 'COMPLETED'
            until(qtApp, lambda: imageReady(window) and session.stream is not None and idle(session))
            initial = session.readSnapshot()
            scope = initial.scopes['root']
            key = scope.result.identity.resultKey
            pixels = scope.images['image']
            image = window.widgets['overview']['overview-image'][1]
            qtKey = image.image.cacheKey()
            assert initial.connection == 'CONNECTED' and scope.result.identity.resultOrdinal == 1
            assert window.displayed['root'].result == scope.result and pixels.shape == (120, 160, 3)
            assert not session.errors
            counts = backend.calls['ReadAsset'], session.stats['decoded'], hub.conversions
            lifecycleCalls = tuple(backend.calls[name] for name in ('StartJob', 'Start', 'StopJob', 'Subscribe'))
            selected['identity'] = initial.runtimeInstanceId, initial.jobId
            healthThread = session.threads[1]
            originalClientSnapshot = session.stub.Snapshot
            faultInjected = False
            recoveryRecorded = False

            def controlledHealthSnapshot(request, *args, **kwargs):
                nonlocal faultInjected, recoveryRecorded
                matches = (request.runtime_instance_id, request.job_id) == selected['identity']
                if threading.current_thread() is not healthThread or not matches:
                    return originalClientSnapshot(request, *args, **kwargs)
                assert kwargs.get('timeout') == .5
                rpcTimeouts.append(kwargs['timeout'])
                if not faultInjected:
                    faultInjected = True
                    mark('health_rpc_started')
                    armed.set()
                    try:
                        return originalClientSnapshot(request, *args, **kwargs)
                    except grpc.RpcError as error:
                        assert error.code() == grpc.StatusCode.DEADLINE_EXCEEDED
                        mark('client_deadline')
                        raise  # The production health loop publishes the error.
                # Observation control only: do not let a successful next health
                # call erase the injected failure before the GUI assertions.
                # This harness wait is outside the real RPC's 0.5s deadline.
                assert allowRecovery.wait(20), 'test recovery latch deadline'
                reply = originalClientSnapshot(request, *args, **kwargs)
                if not recoveryRecorded:
                    recoveryRecorded = True
                    mark('health_rpc_recovered')
                return reply

            monkeypatch.setattr(session.stub, 'Snapshot', controlledHealthSnapshot)
            assert serverEntered.wait(20), 'health did not enter the one-shot server latch'
            until(qtApp, lambda: session.readSnapshot().connection == 'DEADLINE_EXCEEDED')
            assert not serverReturned.is_set()
            window.hide()
            assert session._wantedImages == frozenset()
            window.show()
            failed = session.readSnapshot()
            assert window.lastView.connection == failed.connection == 'DEADLINE_EXCEEDED'
            assert not window.displayed and image.image.isNull()
            assert 'DEADLINE_EXCEEDED' in image.message and 'DEADLINE_EXCEEDED' in window.status.text()
            assert failed.scopes['root'].result == scope.result
            assert failed.scopes['root'].images['image'] is pixels
            assert session._wantedImages == {'image'} and idle(session)
            assert (backend.calls['ReadAsset'], session.stats['decoded'], hub.conversions) == counts
            mark('gui_cleared')

            mark('server_released')
            releaseServer.set()
            assert serverReturned.wait(20), 'cancelled server work did not return'
            mark('recovery_allowed')
            allowRecovery.set()
            # Keep the existing GUI deadline. Recovery is the ordinary health
            # Snapshot and hub submission, without a forced refresh or new job.
            until(qtApp, lambda: session.readSnapshot().connection == 'CONNECTED' and imageReady(window))
            recovered = session.readSnapshot()
            assert recovered.generation == initial.generation
            assert recovered.scopes['root'].result == window.displayed['root'].result == scope.result
            assert recovered.scopes['root'].images['image'] is pixels
            assert image.key == key and image.image.cacheKey() == qtKey
            assert (backend.calls['ReadAsset'], session.stats['decoded'], hub.conversions) == counts
            assert tuple(backend.calls[name] for name in ('StartJob', 'Start', 'StopJob', 'Subscribe')) == lifecycleCalls
            assert [code for code, _detail in session.errors] == ['DEADLINE_EXCEEDED']
            assert len(rpcTimeouts) >= 2 and all(timeout == .5 for timeout in rpcTimeouts)
            mark('gui_recovered')
        finally:
            # Never close a worker while it is still held by this test.
            releaseServer.set()
            allowRecovery.set()
            window.close()
            session.close()
        assert all(not thread.is_alive() for thread in session.threads)
        assert not session._imageConsumers and not session._scheduled and session.pending.empty()
        assert not hub.windows and not hub.timer.isActive() and not hub.cache
        until(qtApp, lambda: not any(backend.server.active.values()) and not backend.server.cleanups)
        assert backend.presentation.assets.stats()['lease_handles'] == 0
        assert backend.presentation.assets.stats()['readers'] == 0
        assert not backend.runtime._closed
        mark('resources_retired')
        assert [stage for stage, _stamp in timeline] == [
            'health_rpc_started', 'server_entered', 'client_deadline', 'gui_cleared',
            'server_released', 'server_returned', 'recovery_allowed', 'health_rpc_recovered',
            'gui_recovered', 'resources_retired']
        firstStamp = timeline[0][1]
        print('HEALTH_HIDE_SHOW_EVIDENCE ' + json.dumps({
            'resultKey': key, 'rpc_timeout_seconds': list(rpcTimeouts),
            'counts_before_and_after': {'ReadAsset': counts[0], 'decoded': counts[1], 'conversions': counts[2]},
            'timeline_ms': [(stage, round((stamp - firstStamp) / 1e6, 3)) for stage, stamp in timeline],
            'client_threads_retired': True, 'server_admissions_retired': True,
            'scope': 'one real health deadline; failure observation held by a test-only recovery latch'},
            ensure_ascii=False, sort_keys=True))
