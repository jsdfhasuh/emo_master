"""Deterministic startup faults; real workload coverage is recorded separately."""
from concurrent.futures import ThreadPoolExecutor
import copy
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from scripts import r3_policy_measure as measure


def evidence():
    return {"job": "job", "phases": {"first_input_start_ns": 100},
            "timestamps": {"observation_start_ns": 10}, "startup_readiness": {
                "protocol": measure.STARTUP_PROTOCOL, "job_id": "job", "budget_seconds": 10,
                "worker_wait_ns": 20, "subscriptions_ready_ns": [30, 40],
                "windows_painted_ns": [50, 60], "clients_ready_ns": 70,
                "gate_opened_ns": 80, "worker_continue_ns": 90}}


@pytest.mark.parametrize("field,value", [
    ("job_id", "wrong"), ("protocol", "old"), ("budget_seconds", 11),
    ("worker_wait_ns", None), ("worker_continue_ns", 101), ("worker_continue_ns", 79),
    ("gate_opened_ns", 5), ("clients_ready_ns", 81),
    ("subscriptions_ready_ns", [30]), ("subscriptions_ready_ns", None),
    ("windows_painted_ns", [50, None]), ("windows_painted_ns", [50, 81]),
    ("worker_wait_ns", True),
])
def testReadinessRequiresEveryPartyBeforeFirstInput(field, value):
    payload = evidence()
    assert measure.startupComplete(payload)
    payload["startup_readiness"][field] = value
    assert not measure.startupComplete(payload)


@pytest.mark.parametrize("stamp", [None, True, 0, 81])
def testObservationMustAlreadyBeRunning(stamp):
    payload = evidence()
    payload["timestamps"]["observation_start_ns"] = stamp
    assert not measure.startupComplete(payload)


def testGateHoldsOriginalPaceBeforeItsClockAndRestoresWrapper(tmp_path, monkeypatch):
    from examples.p2_pace import Pace
    original = Pace.executeNode
    gate = (Event(), SimpleNamespace(value=0), SimpleNamespace(value=0))
    entered = Event()
    originalCalls = []
    pace = Pace()
    pace.initOperator(None)

    def execute(self, *args, **kwargs):
        originalCalls.append(self.index)
        return original(self, *args, **kwargs)

    def job(*_):
        entered.set()
        for _ in range(2):
            pace.executeNode({}, {"initialDelay": 0}, None)

    monkeypatch.setattr(Pace, "executeNode", execute)
    monkeypatch.setattr(measure.base, "measuredJob", job)
    monkeypatch.setenv("EMO_R3_TRACE_DIR", str(tmp_path))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(measure.measuredJob, None, Event(), None, inputGate=gate)
        try:
            assert entered.wait(2)
            assert not future.done() and not originalCalls and not hasattr(pace, "origin")
        finally:
            gate[0].set()
        future.result(timeout=3)
    assert originalCalls == [0, 1] and Pace.executeNode is execute
    assert 0 < gate[1].value <= gate[2].value <= pace.origin * 1e9


@pytest.mark.parametrize("cancelled", [False, True])
def testCancelledOrExpiredGateNeverRunsPace(tmp_path, monkeypatch, cancelled):
    from examples.p2_pace import Pace
    original = Pace.executeNode
    gate = (Event(), SimpleNamespace(value=0), SimpleNamespace(value=0))
    cancel = Event()
    if cancelled:
        cancel.set()
    monkeypatch.setattr(measure, "STARTUP_SECONDS", 0)
    monkeypatch.setenv("EMO_R3_TRACE_DIR", str(tmp_path))
    pace = Pace()
    pace.initOperator(None)
    monkeypatch.setattr(measure.base, "measuredJob", lambda *_: pace.executeNode({}, {}, None))
    with pytest.raises(RuntimeError if cancelled else TimeoutError):
        measure.measuredJob(None, cancel, None, inputGate=gate)
    assert pace.index == 0 and not hasattr(pace, "origin")
    assert gate[1].value > 0 and gate[2].value == 0
    assert Pace.executeNode is original


@pytest.mark.parametrize("fault", [None, "client", "runtime", "job", "stream", "records", "errors", "paint", "hidden", "subscription", "worker"])
def testParentNeverOpensGateWithMissingOrMismatchedObserver(monkeypatch, fault):
    startup = measure.StartupReadiness()
    startup.job = "job"
    contexts = [object(), object()]
    startup.observeSubscription("wrong", object())
    startup.observeSubscription("job", contexts[0])
    startup.observeSubscription("job", contexts[0])
    assert len(startup.subscriptions) == 1
    if fault != "subscription":
        startup.observeSubscription("job", contexts[1])
    startup.paints = {0: 1} if fault == "paint" else {0: 1, 1: 2}
    startup.inputGate[1].value = 0 if fault == "worker" else 1
    props = dict(jobId="job", instanceId="runtime", compatible=True, connection="CONNECTED",
                 stream=object(), records=[], errors=[])
    clients = [SimpleNamespace(lock=Lock(), **copy.copy(props)) for _ in range(2)]
    if fault in {"client", "runtime", "job", "stream", "records", "errors"}:
        field, value = {"client": ("compatible", False), "runtime": ("instanceId", "wrong"),
                        "job": ("jobId", "wrong"), "stream": ("stream", None),
                        "records": ("records", ["early"]), "errors": ("errors", ["failed"])}[fault]
        setattr(clients[1], field, value)
    windows = [SimpleNamespace(isVisible=lambda: fault != "hidden") for _ in range(2)]
    clock = iter([0, 0, 11])
    monkeypatch.setattr(measure.time, "monotonic", lambda: next(clock))
    if fault:
        with pytest.raises(TimeoutError, match="startup readiness incomplete"):
            startup.release(SimpleNamespace(processEvents=lambda: None), clients, windows, "runtime")
        assert not startup.inputGate[0].is_set() and startup.openedNs is None
    else:
        startup.release(SimpleNamespace(processEvents=lambda: None), clients, windows, "runtime")
        assert startup.inputGate[0].is_set()
        assert startup.openedNs >= max(startup.subscriptions.values())


def testWindowMustActuallyPaint(retainedQtApplication):
    import time
    from PySide2.QtWidgets import QWidget
    window = QWidget()
    startup = measure.StartupReadiness()
    startup.watchWindow(window, 0)
    assert not startup.paints
    try:
        window.show()
        deadline = time.monotonic() + 2
        while not startup.paints and time.monotonic() < deadline:
            retainedQtApplication.processEvents()
            time.sleep(.001)
        assert startup.paints[0] > 0 and window.isVisible()
    finally:
        measure.retireGui([window], None, retainedQtApplication)
