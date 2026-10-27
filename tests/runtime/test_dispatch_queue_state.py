from copy import deepcopy
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.frame_dispatch.runner import DispatchRunner
from emo_master.apps.runtime.workflow.cancellation import CancellationToken, CancellationRequested
from emo_master.core.workflow.dispatch_contracts import dispatchTargets
from tests.runtime.test_frame_dispatch import finish, group as group


def waitMode(group, timeout=0):
    node = next(n for n in group.document.workflows["main"].nodes if n.kind == "dispatch")
    node.params.update(queueFullPolicy="wait", waitTimeoutMs=timeout)
    return node


def assertCounts(snapshot):
    for slot in snapshot["slots"]:
        assert slot["acceptedTotal"] == sum(slot[key] for key in
            ("queued", "processing", "completedTotal", "failedTotal", "cancelledTotal"))


def testWaitAllTargetsThenAdmitOnceAcrossTransportEnvelopes(group):
    scheduler, request, frame = group
    waitMode(scheduler)
    scheduler.submit("source", request)
    finish(scheduler, "left")
    current = dict(request, dispatchSequence=2, requestId="first", sequence=2)
    first = scheduler.submit("source", current)
    assert first["status"] == "WAITING"
    assert first["blockedTargetSlotIds"] == ["right"]
    assert not scheduler.slots["left"].queue
    assert scheduler.highWater["source"] == 1
    assert len(scheduler.records) == 1
    assert scheduler.submit("source", dict(current, requestId="second", sequence=3)) == first
    state = scheduler.snapshot("right")
    assert state["slots"][1]["rejectedTotal"] == 0
    assert state["slots"][1]["waitingProducers"][0]["jobId"] == "source"
    assert state["slots"][1]["items"][0]["frames"][0]["frameId"] == frame["frameId"]
    assert "name" not in state["slots"][1]["items"][0]["frames"][0]
    finish(scheduler, "right")
    accepted = scheduler.submit("source", current)
    assert accepted["accepted"]
    assert scheduler.submit("source", dict(current, requestId="third")) == accepted
    assert not scheduler.waiting
    assertCounts(scheduler.snapshot())


def testWaitTimeoutAndUnavailableAreTerminal(group, monkeypatch):
    scheduler, request, _ = group
    waitMode(scheduler, 100)
    scheduler.submit("source", request)
    second = dict(request, dispatchSequence=2)
    scheduler.submit("source", second)
    started = scheduler.waiting["source"]["started"]
    monkeypatch.setattr("emo_master.apps.runtime.frame_dispatch.scheduler.time.monotonic", lambda: started + .11)
    with pytest.raises(DeviceError) as caught:
        scheduler.submit("source", second)
    assert caught.value.code == "E_DISPATCH_WAIT_TIMEOUT"
    assert not scheduler.waiting
    scheduler.workerRetired("left")
    with pytest.raises(DeviceError) as caught:
        scheduler.submit("source", second)
    assert caught.value.code == "E_DISPATCH_UNAVAILABLE"
    assertCounts(scheduler.snapshot())


def testCountersSurviveReceiptCleanupAndObservationHasNoOwnership(group):
    scheduler, request, frame = group
    token = scheduler.submit("source", request)
    store = scheduler.devices.frames
    owners = set(store.frames[frame["frameId"]].owners)
    state = scheduler.snapshot("left")
    assert "items" not in state["slots"][1]
    assert owners == store.frames[frame["frameId"]].owners
    state["slots"][0]["frames"] = []
    finish(scheduler, "left")
    finish(scheduler, "right")
    scheduler.join("source", dict(token=token, cycleId=request["cycleId"], planRevision=request["planRevision"]))
    assert not scheduler.records
    assert all(s["completedTotal"] == 1 for s in scheduler.snapshot()["slots"])
    scheduler.submit("source", dict(request, dispatchSequence=2))
    scheduler.next("left")
    scheduler.stop()
    scheduler.workerRetired("left")
    state = scheduler.snapshot()
    assert state["slots"][0]["failedTotal"] == 1
    assert state["slots"][1]["cancelledTotal"] == 1
    scheduler.workerRetired("left")
    assert scheduler.snapshot()["slots"][0]["failedTotal"] == 1
    assertCounts(state)


def testWaitingInputIdentityCannotChange(group):
    scheduler, request, _ = group
    waitMode(scheduler)
    scheduler.submit("source", request)
    scheduler.submit("source", dict(request, dispatchSequence=2))
    with pytest.raises(DeviceError, match="identity"):
        scheduler.submit("source", dict(request, dispatchSequence=3))


def testSkipFullFanoutKeepsAcquisitionFreeAndDoesNotRetainFrames(group):
    scheduler, request, frame = group
    node = waitMode(scheduler)
    node.params['queueFullPolicy'] = 'skip'
    scheduler.submit('source', request)
    finish(scheduler, 'left')
    owners = set(scheduler.devices.frames.frames[frame['frameId']].owners)
    second = dict(request, dispatchSequence=2)
    token = scheduler.submit('source', second)
    assert token['status'] == 'SKIPPED' and not token['accepted']
    assert all(value['status'] == 'SKIPPED' for value in token['targets'].values())
    assert not scheduler.slots['left'].queue and not scheduler.waiting
    assert scheduler.devices.frames.frames[frame['frameId']].owners == owners
    assert scheduler.submit('source', dict(second, requestId='repeat')) == token
    assert all(slot.skippedTotal == 1 and slot.rejectedTotal == 0 for slot in scheduler.slots.values())
    assert all(slot['skippedTotal'] == 1 for slot in scheduler.snapshot()['slots'])
    finish(scheduler, 'right')
    assert scheduler.submit('source', dict(request, dispatchSequence=3))['accepted']
    scheduler.workerRetired('left')
    with pytest.raises(DeviceError) as error:
        scheduler.submit('source', dict(request, dispatchSequence=4))
    assert error.value.code == 'E_DISPATCH_UNAVAILABLE'
    assertCounts(scheduler.snapshot())


def testRunnerSkipIsExplicitAndDoesNotPretendAdmission():
    token = dict(status='SKIPPED', accepted=False, targets={})
    devices = SimpleNamespace(retainFrames=True, encode=lambda value, context: {'value': value},
                              framework=lambda *a, **k: token)
    events = []
    runner = DispatchRunner(devices, lambda name, *a, **k: events.append(name))
    node = SimpleNamespace(kind='dispatch', nodeId='dispatch', params={'queueFullPolicy': 'skip'})
    context = SimpleNamespace(workflowId='main', cycleId='cycle', planRevision=1)
    assert runner.run(node, {}, context, CancellationToken())[0] == {'token': token}
    assert events == ['dispatch.skipped']


def testRunnerWaitDoesNotReencodeAndCancellationStopsPolling():
    cancellation = CancellationToken()
    calls = []
    encoded = []
    def framework(action, **fields):
        calls.append((action, deepcopy(fields)))
        if len(calls) == 2:
            cancellation.cancel()
        return {"status": "WAITING"}
    devices = SimpleNamespace(retainFrames=True, framework=framework,
        encode=lambda inputs, context: (encoded.append(inputs) or {"value": inputs}))
    runner = DispatchRunner(devices, lambda *a, **kw: None)
    node = SimpleNamespace(kind="dispatch", nodeId="dispatch", params={"queueFullPolicy": "wait"})
    context = SimpleNamespace(workflowId="main", cycleId="cycle", planRevision=1)
    with pytest.raises(CancellationRequested):
        runner.run(node, {"frame": 1}, context, cancellation)
    assert len(encoded) == 1 and len(calls) == 2
    assert calls[0] == calls[1]


@pytest.mark.parametrize("params", [{"queueFullPolicy": "drop"}, {"queueFullPolicy": []}, {"waitTimeoutMs": -1},
                                   {"waitTimeoutMs": True}, {"waitTimeoutMs": 1.5}])
def testRejectInvalidWaitSettings(params):
    with pytest.raises(ValueError):
        dispatchTargets(SimpleNamespace(params=params))


@pytest.mark.parametrize("kind,cancelled", [("framework_operation", True), ("framework_operation", False),
                                          ("device_action", True)])
def testPipeClosureDuringWaitCancellationDoesNotReplayOrHideDeviceUncertainty(monkeypatch, kind, cancelled):
    from threading import Event
    from emo_master.apps.runtime.devices import ipc
    cancel = Event()
    sent = []
    monkeypatch.setattr(ipc, "sendBytes", lambda *args: sent.append(args[1]))
    def closedPipe(*args):
        if cancelled:
            cancel.set()
        raise BrokenPipeError("closed during Stop")
    monkeypatch.setattr(ipc, "receiveBytes", closedPipe)
    proxy = ipc.DeviceProxy(SimpleNamespace(close=lambda: None), cancel)
    expected = CancellationRequested if kind == "framework_operation" and cancelled else DeviceError
    with pytest.raises(expected) as failure:
        proxy._call(kind, "dispatchSubmit" if kind == "framework_operation" else "capture")
    assert len(sent) == 1 and proxy.closed
    assert failure.value.outcome == "UNKNOWN"
    assert failure.value.code == ("E_CANCELLED" if expected is CancellationRequested else "E_DEVICE_OUTCOME_UNKNOWN")
