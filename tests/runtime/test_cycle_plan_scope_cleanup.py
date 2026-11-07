"""Cycle cleanup keeps the primary device receipt across the real Pipe proxy."""
from contextlib import ExitStack, contextmanager
import json
import multiprocessing
import os
from threading import Event, Thread
import time
from types import SimpleNamespace

import numpy as np
import pytest

from emo_master.apps.runtime.communication.pipe_io import receiveBytes, sendBytes
from emo_master.apps.runtime.devices.ipc import DeviceProxy, MAX_BYTES
from emo_master.apps.runtime.project_config.cycle_plan import CyclePlanSession
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.core.contracts.device_driver import DeviceError


def _cycle(request):
    return dict(ok=True, result=dict(cycleId="cycle-" + str(request["sequence"]), ownsCycle=True,
        planRevision=request["sequence"], documentRevision=1,
        deviceScope={"cycleId": str(request["sequence"])},
        deviceIdentities={"camera": {"deviceGeneration": str(request["sequence"])}}))


def _cleanupFailure():
    return dict(ok=False, code="E_DEVICE_RELEASE_PENDING", message="cycle release failed",
                outcome="NOT_EXECUTED", diagnostics={"receipt": "cleanup"})


@contextmanager
def _proxyChannel(respond):
    parent, child = multiprocessing.get_context("spawn").Pipe()
    cancellation, stopped = Event(), Event()
    proxy = DeviceProxy(child, cancellation)
    requests, errors = [], []

    def serve():
        try:
            while not stopped.is_set():
                if not parent.poll(.02):
                    continue
                request = json.loads(receiveBytes(parent, MAX_BYTES, stopped.is_set, time.monotonic() + 2))
                requests.append(request)
                reply = respond(request)
                if reply is None:
                    return  # The actual peer closes without replying to this request.
                sendBytes(parent, json.dumps(reply).encode(), stopped.is_set, time.monotonic() + 2)
        except (OSError, EOFError) as error:
            if not stopped.is_set():
                errors.append(error)
        except BaseException as error:
            errors.append(error)
        finally:
            parent.close()

    thread = Thread(target=serve, name="test-cycle-peer", daemon=True)
    thread.start()
    try:
        yield proxy, cancellation, requests
    finally:
        stopped.set()
        proxy.close()
        thread.join(3)
        assert not thread.is_alive(), "cycle peer did not exit"
        assert not errors, errors


def _session(proxy, cancellation):
    executor = SimpleNamespace(deviceScope={}, deviceIdentities={})
    runner = SimpleNamespace(operatorExecutor=executor, compiledProject=object())
    return CyclePlanSession(runner, proxy, object(), None, {}, (), 1, cancellation.is_set)


@pytest.mark.parametrize("depth", [1, 2])
def testFrameWriteEOFKeepsOriginalUnknownReceiptThroughNestedCleanup(depth):
    def respond(request):
        if request["action"] == "beginCycle":
            return _cycle(request)
        assert request["action"] == "frameBeginWrite"
        return None

    with _proxyChannel(respond) as (proxy, cancellation, requests):
        plans = _session(proxy, cancellation)
        primary = []
        with pytest.raises(DeviceError) as failure:
            with ExitStack() as scopes:
                context = RunContext.root("job", "main")
                for level in range(depth):
                    context = scopes.enter_context(plans.scope(context,
                        parentCycleId=context.cycleId if level else None))
                try:
                    proxy.encode(np.zeros((2, 2, 3), dtype=np.uint8), context)
                except DeviceError as error:
                    primary.append(error)
                    raise
        assert failure.value is primary[0]
        assert failure.value.code == "E_DEVICE_OUTCOME_UNKNOWN"
        assert failure.value.outcome == "UNKNOWN"
        cause = failure.value.__cause__
        # Windows reports ERROR_BROKEN_PIPE (109) before or after the
        # overlapped read starts; POSIX reports EOF for the same peer close.
        assert isinstance(cause, EOFError) or (
            os.name == "nt" and isinstance(cause, OSError)
            and (cause.errno == 109 or getattr(cause, "winerror", None) == 109))
        diagnostics = failure.value.diagnostics
        assert diagnostics["transportError"] == type(cause).__name__
        assert diagnostics["requestId"] == requests[-1]["requestId"]
        assert diagnostics["action"] == "frameBeginWrite" and diagnostics["phase"] == "receive"
        assert diagnostics["cycleId"] == "cycle-" + str(depth)
        assert [item["cycleId"] for item in diagnostics["cycleCleanup"]] == [
            "cycle-" + str(level) for level in range(depth, 0, -1)]
        assert all(item["code"] == "E_DEVICE_CANCELLED" and item["outcome"] == "NOT_EXECUTED"
                   for item in diagnostics["cycleCleanup"])
        assert proxy.closed and not cancellation.is_set()
        assert proxy.sequence == len(requests) == depth + 1  # Cleanup never replays the interrupted write.
        assert plans.revision == 1
        assert plans.runner.operatorExecutor.deviceScope == {"cycleId": "1"}
        print("frame_write_eof_receipt=" + json.dumps(dict(depth=depth,
            code=failure.value.code, outcome=failure.value.outcome, diagnostics=diagnostics), sort_keys=True))


def testDeviceFailureAndRequestIdentitySurviveCleanupErrorReceipt():
    def respond(request):
        if request["action"] == "beginCycle":
            return _cycle(request)
        if request["action"] == "endCycle":
            return _cleanupFailure()
        return dict(ok=False, code="E_DEVICE_OUTCOME_UNKNOWN", message="native write receipt unavailable",
                    outcome="UNKNOWN", diagnostics={"requestId": request["requestId"], "receipt": "primary"})

    with _proxyChannel(respond) as (proxy, cancellation, requests):
        primary = []
        with pytest.raises(DeviceError) as failure:
            with _session(proxy, cancellation).scope(RunContext.root("job", "main")) as context:
                try:
                    proxy.call("write", deviceId="device", params={}, cycleId=context.cycleId)
                except DeviceError as error:
                    primary.append(error)
                    raise
        assert failure.value is primary[0]
        assert failure.value.code == "E_DEVICE_OUTCOME_UNKNOWN" and failure.value.outcome == "UNKNOWN"
        assert failure.value.diagnostics["requestId"] == requests[1]["requestId"]
        assert failure.value.diagnostics["receipt"] == "primary"
        assert failure.value.diagnostics["cycleCleanup"][0]["code"] == "E_DEVICE_RELEASE_PENDING"
        assert [request["action"] for request in requests] == ["beginCycle", "write", "endCycle"]


def testCleanupErrorWithoutPrimaryStillPropagates():
    with _proxyChannel(lambda request: _cycle(request) if request["action"] == "beginCycle"
                       else _cleanupFailure()) as (proxy, cancellation, requests):
        with pytest.raises(DeviceError) as failure:
            with _session(proxy, cancellation).scope(RunContext.root("job", "main")):
                pass
        assert failure.value.code == "E_DEVICE_RELEASE_PENDING"
        assert failure.value.outcome == "NOT_EXECUTED"
        assert failure.value.diagnostics == {"receipt": "cleanup"}
        assert [request["action"] for request in requests] == ["beginCycle", "endCycle"]


@pytest.mark.parametrize("primary", [ValueError("body failed"), KeyboardInterrupt("body interrupted")])
def testExpectedCleanupFailureCannotReplacePrimaryException(primary):
    with _proxyChannel(lambda request: _cycle(request) if request["action"] == "beginCycle"
                       else _cleanupFailure()) as (proxy, cancellation, _requests):
        with pytest.raises(type(primary)) as failure:
            with _session(proxy, cancellation).scope(RunContext.root("job", "main")):
                raise primary
        assert failure.value is primary


@pytest.mark.parametrize("cleanup", [RuntimeError("unexpected cleanup bug"), KeyboardInterrupt("cleanup interrupted")])
def testUnexpectedCleanupExceptionsAreNotSuppressed(monkeypatch, cleanup):
    with _proxyChannel(_cycle) as (proxy, cancellation, _requests):
        framework = proxy.framework

        def endWithUnexpectedError(action, **fields):
            if action == "endCycle":
                raise cleanup
            return framework(action, **fields)

        monkeypatch.setattr(proxy, "framework", endWithUnexpectedError)
        primary = DeviceError("E_DEVICE_OUTCOME_UNKNOWN", "primary", outcome="UNKNOWN")
        with pytest.raises(type(cleanup)) as failure:
            with _session(proxy, cancellation).scope(RunContext.root("job", "main")):
                raise primary
        assert failure.value is cleanup and cleanup.__context__ is primary


def testNormalCancellationKeepsItsReceiptAndSkipsCleanup():
    with _proxyChannel(_cycle) as (proxy, cancellation, requests):
        primary = CancellationRequested("execution cancelled")
        with pytest.raises(CancellationRequested) as failure:
            with _session(proxy, cancellation).scope(RunContext.root("job", "main")):
                cancellation.set()
                raise primary
        assert failure.value is primary
        assert [request["action"] for request in requests] == ["beginCycle"]


@pytest.mark.parametrize("hasPrimary", [False, True])
def testStopRacingCleanupPreservesPrimaryAndOtherwisePropagates(monkeypatch, hasPrimary):
    with _proxyChannel(_cycle) as (proxy, cancellation, requests):
        framework = proxy.framework

        def stopAtCleanup(action, **fields):
            if action == "endCycle":
                cancellation.set()
            return framework(action, **fields)

        monkeypatch.setattr(proxy, "framework", stopAtCleanup)
        primary = DeviceError("E_DEVICE_OUTCOME_UNKNOWN", "primary", outcome="UNKNOWN", diagnostics={"requestId": "primary"})
        with pytest.raises(DeviceError if hasPrimary else CancellationRequested) as failure:
            with _session(proxy, cancellation).scope(RunContext.root("job", "main")):
                if hasPrimary:
                    raise primary
        if hasPrimary:
            assert failure.value is primary and failure.value.outcome == "UNKNOWN"
            assert failure.value.diagnostics["requestId"] == "primary"
        else:
            assert failure.value.code == "E_CANCELLED"
        assert [request["action"] for request in requests] == ["beginCycle"]
