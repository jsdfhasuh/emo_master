"""Exact native forwarding, bounded metadata, and retryable wrapper retirement."""
from contextlib import contextmanager
import importlib.util
import json
import multiprocessing
from multiprocessing.shared_memory import SharedMemory
from multiprocessing.synchronize import Semaphore
from pathlib import Path
import weakref

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("r3_capture_credit_trace", ROOT / "scripts" / "r3_capture_credit_trace.py")
assert SPEC is not None and SPEC.loader is not None
capture_trace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture_trace)


@contextmanager
def patches():
    entries = []
    missing = object()

    def patch(owner, name, value):
        entries.append((owner, name, vars(owner).get(name, missing)))
        setattr(owner, name, value)
    try:
        yield patch
    finally:
        for owner, name, previous in reversed(entries):
            if previous is missing:
                delattr(owner, name)
            else:
                setattr(owner, name, previous)


def image_item(ordinal=1):
    return {"identity": {"runtimeInstanceId": "runtime", "jobId": "job",
                         "resultKey": "result-" + str(ordinal), "resultOrdinal": ordinal}, "rawBytes": 0}


def configuration(credit, memory_name="never-record-this-shared-memory-name"):
    return {"plan": '{"scopes": {}, "sources": {}}', "versions": {},
            "slots": [{"index": 3, "lane": 1, "capacity": 12, "offset": 16,
                       "free": credit, "name": memory_name}], "imageLaneBySource": {"image": 1}}


def acquire_only(collector, key, value, item):
    return collector.config["slots"][0]["free"].acquire(False)


def _spawn_capture_probe(credit, memory_name, connection):
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    trace = capture_trace.CaptureCreditTrace()
    original_acquire, original_image = credit.acquire, ResultCollector.image
    try:
        with capture_trace.installed(trace, ROOT):
            collector = ResultCollector(configuration(credit, memory_name), lambda event: None)
            pixels = np.zeros((2, 2, 3), dtype=np.uint8)
            assert collector.image("image", pixels, image_item())["pendingImage"]
            assert collector.image("image", pixels, image_item(2))["state"] == "UNAVAILABLE"
        connection.send({"payload": trace.payload(), "native_before": type(original_acquire.__self__).__name__,
                         "native_restored": credit.acquire is original_acquire,
                         "class_restored": ResultCollector.image is original_image})
    finally:
        connection.close()


class RestorableSemaphore(Semaphore):
    def __init__(self, context):
        super().__init__(1, ctx=context)
        self.native_acquire = self.acquire
        self.reject_restore = False

    def __setattr__(self, name, value):
        if name == "acquire" and getattr(self, "reject_restore", False) and value is self.native_acquire:
            raise OSError("private injected restore fault")
        super().__setattr__(name, value)


def testImportAndConstructionAreDefaultOff():
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    original_init, original_image = ResultCollector.__init__, ResultCollector.image
    trace = capture_trace.CaptureCreditTrace()
    assert ResultCollector.__init__ is original_init and ResultCollector.image is original_image
    assert not trace.hooks and not trace.rows and not trace.installed
    assert "lock" not in vars(trace)


def testNativeSemLockForwardsExactlyOnceAndRestoresRawAttribute(monkeypatch, tmp_path):
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    credit = multiprocessing.get_context("spawn").BoundedSemaphore(1)
    original_acquire, original_release = credit.acquire, credit.release
    assert original_acquire.__self__ is credit._semlock
    calls = []
    forward = capture_trace._Forward.__call__

    def count_native(self, *args, **kwargs):
        if self.method is original_acquire:
            calls.append((args, dict(kwargs)))
        return forward(self, *args, **kwargs)
    monkeypatch.setattr(capture_trace._Forward, "__call__", count_native)
    monkeypatch.setattr(ResultCollector, "image", acquire_only)
    trace = capture_trace.CaptureCreditTrace()
    with capture_trace.installed(trace, ROOT):
        collector = ResultCollector(configuration(credit), lambda event: None)
        assert collector.config["slots"][0]["free"] is credit
        assert credit.release is original_release and credit._semlock is original_acquire.__self__
        value = np.zeros((2, 2, 3), dtype=np.uint8)
        assert collector.image("image", value, image_item()) is True
        assert collector.image("image", value, image_item(2)) is False
        assert calls == [((False,), {}), ((False,), {})]
        assert not credit.acquire(False)  # Later cleanup/readers are unscoped.
        assert len(trace.rows) == 2 and len(calls) == 3
    assert credit.acquire is original_acquire and credit.release is original_release
    assert ResultCollector.image is acquire_only
    summary = trace.save(tmp_path / "capture-credit.json")
    assert summary["complete"] and summary["observer_retired"]
    assert summary["actual_attempts"] == {"attempts": 2, "accepted": 1, "refused": 1, "failed": 0, "invalid_return": 0}
    assert summary["identity_counts"]["recorded_image_identities"] == 2
    assert [row["acquired"] for row in trace.rows] == [True, False]
    for ordinal, row in enumerate(trace.rows, 1):
        assert row["stage"] == "producer.capture_credit_acquire"
        assert row["result_ordinal"] == ordinal and row["result_key"] == "result-" + str(ordinal)
        assert (row["slot"], row["lane"], row["capacity"], row["offset"], row["raw_bytes"]) == (3, 1, 12, 16, 12)
        assert row["acquire_args"] == [False] and row["acquire_kwargs"] == {}
        assert row["end_ns"] >= row["start_ns"] and row["outcome"] == "OK"
    payload = json.loads((tmp_path / "capture-credit.json").read_text())
    assert payload["summary"] == summary and payload["performance_verdict"] == "NOT_EVALUATED"
    encoded = json.dumps(payload)
    assert "never-record-this-shared-memory-name" not in encoded and "shape" not in encoded
    assert "provenance" not in encoded and str(tmp_path) not in encoded
    original_release()


def testActualCollectorRecordsNativeSuccessRefusalAfterSpawn(tmp_path):
    context = multiprocessing.get_context("spawn")
    credit = context.BoundedSemaphore(1)
    original_acquire, original_release = credit.acquire, credit.release
    memory = SharedMemory(create=True, size=28)
    parent, child = context.Pipe()
    process = context.Process(target=_spawn_capture_probe, args=(credit, memory.name, child))
    try:
        process.start()
        child.close()
        assert parent.poll(15)
        result = parent.recv()
        process.join(5)
        assert process.exitcode == 0
        assert result["native_before"] == "SemLock" and result["native_restored"] and result["class_restored"]
        rows = result["payload"]["rows"]
        assert len(rows) == 2 and [row["acquired"] for row in rows] == [True, False]
        assert result["payload"]["observer_retired"]
        assert credit.acquire is original_acquire and credit.release is original_release
        assert not credit.acquire(False)
        assert memory.name not in json.dumps(result)
        credit.release()
    finally:
        if process.is_alive():
            process.terminate()
        process.join(5)
        process.close()
        parent.close()
        child.close()
        memory.close()
        memory.unlink()


def testNestedBaselineWrappersRestoreInExactOrder(monkeypatch):
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    order = []

    def original(collector, key, value, item):
        order.append("native-image")
        return acquire_only(collector, key, value, item)
    monkeypatch.setattr(ResultCollector, "image", original)
    credit = multiprocessing.get_context("spawn").BoundedSemaphore(1)
    native = credit.acquire
    trace = capture_trace.CaptureCreditTrace()
    with patches() as outer_patch:
        # The policy workflow wrapper belongs to a different method/class. An
        # outer harmless class attribute still demonstrates restoration order.
        outer_patch(ResultCollector, "diagnostic_policy_marker", True)
        with capture_trace.installed(trace, ROOT):
            observer_image = ResultCollector.image
            with patches() as baseline_patch:
                def baseline(collector, key, value, item):
                    order.append("baseline-enter")
                    try:
                        return observer_image(collector, key, value, item)
                    finally:
                        order.append("baseline-exit")
                baseline_patch(ResultCollector, "image", baseline)
                collector = ResultCollector(configuration(credit), lambda event: None)
                assert collector.image("image", np.zeros((2, 2, 3), dtype=np.uint8), image_item())
            assert ResultCollector.image is observer_image
            assert credit.acquire is not native
        assert ResultCollector.image is original and credit.acquire is native
        assert trace.observer_retired and ResultCollector.diagnostic_policy_marker
    assert "diagnostic_policy_marker" not in vars(ResultCollector)
    assert order == ["baseline-enter", "native-image", "baseline-exit"] and len(trace.rows) == 1
    credit.release()


@pytest.mark.parametrize("fault", ["record", "attempt_metadata", "enter_image", "hook_collector", "clock"])
def testObserverFaultNeverChangesNativeOutcome(monkeypatch, fault, tmp_path):
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    monkeypatch.setattr(ResultCollector, "image", acquire_only)
    credit = multiprocessing.get_context("spawn").BoundedSemaphore(1)
    native = credit.acquire
    trace = capture_trace.CaptureCreditTrace()

    def broken(*args, **kwargs):
        raise RuntimeError("private observer error text")
    with capture_trace.installed(trace, ROOT):
        collector = ResultCollector(configuration(credit), lambda event: None)
        if fault == "clock":
            monkeypatch.setattr(capture_trace, "_clocks", broken)
        else:
            monkeypatch.setattr(trace, fault, broken)
        pixels = np.zeros((2, 2, 3), dtype=np.uint8)
        assert collector.image("image", pixels, image_item()) is True
        assert collector.image("image", pixels, image_item(2)) is False
    assert credit.acquire is native and trace.disabled and trace.observer_retired
    summary = trace.save(tmp_path / "capture-credit.json")
    assert not summary["complete"] and summary["diagnostic_errors"] == 1
    assert "private observer error text" not in json.dumps(trace.payload())
    credit.release()


def testNativeExceptionAndArgumentsRemainUnchanged(monkeypatch):
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    calls = []
    expected = ValueError("native error remains identical")

    class Credit:
        def acquire(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise expected
    credit = Credit()

    def bad_acquire(collector, key, value, item):
        return credit.acquire(False, timeout=0.125)
    monkeypatch.setattr(ResultCollector, "image", bad_acquire)
    trace = capture_trace.CaptureCreditTrace()
    with capture_trace.installed(trace, ROOT):
        collector = ResultCollector(configuration(credit), lambda event: None)
        with pytest.raises(ValueError) as actual:
            collector.image("image", np.zeros((2, 2, 3), dtype=np.uint8), image_item())
        assert actual.value is expected
    assert calls == [((False,), {"timeout": 0.125})]
    assert "acquire" not in vars(credit) and trace.active_scopes == 0
    assert trace.rows[0]["acquired"] is None and trace.rows[0]["outcome"] == "ValueError"
    assert trace.rows[0]["acquire_args"] == [False] and trace.rows[0]["acquire_kwargs"] == {"timeout": 0.125}
    assert trace.payload()["actual_attempts"]["failed"] == 1


def testRawNativeRestorationFailureIsRetryableAndInvalid(monkeypatch, tmp_path):
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    monkeypatch.setattr(ResultCollector, "image", acquire_only)
    credit = RestorableSemaphore(multiprocessing.get_context("spawn"))
    native = credit.acquire
    trace = capture_trace.CaptureCreditTrace()
    with capture_trace.installed(trace, ROOT):
        collector = ResultCollector(configuration(credit), lambda event: None)
        assert collector.image("image", np.zeros((2, 2, 3), dtype=np.uint8), image_item())
        credit.reject_restore = True
    assert not trace.restored and not trace.observer_retired and len(trace.hooks) == 1
    assert not credit.acquire(False)  # Broken restoration does not break native calls.
    credit.reject_restore = False
    trace.restore()
    assert credit.acquire is native and trace.restored and trace.observer_retired
    assert trace.disabled and trace.diagnostic_errors == 1
    assert not trace.save(tmp_path / "capture-credit.json")["complete"]
    credit.release()


def testInheritedProvenanceExternalConflictAndWeakOwners(monkeypatch):
    from emo_master.apps.runtime.presentation.collector import ResultCollector

    class Credit:
        def acquire(self, block=True):
            return False
    trace = capture_trace.CaptureCreditTrace()
    credit = Credit()
    collector = ResultCollector(configuration(credit), lambda event: None)
    trace.hook_collector(collector)
    assert "acquire" in vars(credit)
    trace.restore()
    assert "acquire" not in vars(credit)
    trace.hook_collector(collector)
    credit_ref, collector_ref = weakref.ref(credit), weakref.ref(collector)
    del credit, collector
    assert credit_ref() is None and collector_ref() is None  # No gc.collect.
    trace.restore()
    assert trace.observer_retired
    credit = Credit()
    collector = ResultCollector(configuration(credit), lambda event: None)
    trace.hook_collector(collector)
    wrapper = credit.acquire
    def replacement(block):
        return "external"
    credit.acquire = replacement
    trace.restore()
    assert credit.acquire is replacement and not trace.observer_retired and trace.hooks
    credit.acquire = wrapper
    trace.restore()
    assert "acquire" not in vars(credit) and trace.observer_retired
    assert trace.counters["restore_conflict"] == 1
    trace.hook_collector(collector)
    trace_ref = weakref.ref(trace)
    del trace
    assert trace_ref() is None and credit.acquire(False) is False


def testHookRegistryDoesNotRetainDistinctNativeMethodOwner():
    from emo_master.apps.runtime.presentation.collector import ResultCollector

    class NativeOwner:
        def acquire(self, block=True):
            return False

    class Credit:
        pass
    trace = capture_trace.CaptureCreditTrace()
    native = NativeOwner()
    credit = Credit()
    credit.acquire = native.acquire
    collector = ResultCollector(configuration(credit), lambda event: None)
    native_ref, credit_ref = weakref.ref(native), weakref.ref(credit)
    trace.hook_collector(collector)
    del native, credit, collector
    assert credit_ref() is None and native_ref() is None
    trace.restore()
    assert trace.observer_retired


@pytest.mark.parametrize("field,value", [("index", True), ("lane", -1), ("capacity", "12"), ("offset", 1.25)])
def testInvalidSlotSchemaCannotFabricateRows(monkeypatch, field, value, tmp_path):
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    monkeypatch.setattr(ResultCollector, "image", acquire_only)
    credit = multiprocessing.get_context("spawn").BoundedSemaphore(1)
    trace = capture_trace.CaptureCreditTrace()
    with capture_trace.installed(trace, ROOT):
        config = configuration(credit)
        config["slots"][0][field] = value
        collector = ResultCollector(config, lambda event: None)
        assert collector.image("image", np.zeros((2, 2, 3), dtype=np.uint8), image_item())
    assert not trace.rows and trace.counters["invalid_slot_schema"] == 1
    assert trace.payload()["actual_attempts"]["accepted"] == 1
    assert not trace.save(tmp_path / "invalid.json")["complete"]
    credit.release()


def testAttemptUsesCurrentConfigAndBoundsRowsAndIdentity(monkeypatch, tmp_path):
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    monkeypatch.setattr(ResultCollector, "image", acquire_only)
    credit = multiprocessing.get_context("spawn").BoundedSemaphore(1)
    trace = capture_trace.CaptureCreditTrace(row_limit=2)
    with capture_trace.installed(trace, ROOT):
        config = configuration(credit)
        collector = ResultCollector(config, lambda event: None)
        config["slots"][0].update(index=9, lane=4, capacity=24, offset=32)
        pixels = np.zeros((2, 2, 3), dtype=np.uint8)
        for ordinal in range(1, 4):
            assert collector.image("image", pixels, image_item(ordinal)) is (ordinal == 1)
        invalid = image_item(4)
        invalid["identity"]["resultKey"] = "x" * 161
        assert collector.image("image", pixels, invalid) is False
    assert len(trace.rows) == 2 and trace.counters["dropped_rows"] == 1
    assert trace.rows[0]["slot"] == 9 and trace.rows[0]["capacity"] == 24
    assert trace.counters["invalid_image_identity"] == 1
    summary = trace.save(tmp_path / "bounded.json")
    assert not summary["complete"] and summary["actual_attempts"]["attempts"] == 4
    assert (tmp_path / "bounded.json").stat().st_size < capture_trace.TRACE_BYTES
    credit.release()
    for kwargs in ({"row_limit": 513}, {"row_limit": True}, {"hook_limit": 129}, {"hook_limit": 0}):
        with pytest.raises(ValueError, match="budgets"):
            capture_trace.CaptureCreditTrace(**kwargs)


def testByteBudgetAndHookBudgetNeverGrowPastLimits(monkeypatch, tmp_path):
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    monkeypatch.setattr(ResultCollector, "image", acquire_only)
    credit = multiprocessing.get_context("spawn").BoundedSemaphore(1)
    trace = capture_trace.CaptureCreditTrace()
    monkeypatch.setattr(capture_trace, "ROW_BYTES", 1)
    with capture_trace.installed(trace, ROOT):
        collector = ResultCollector(configuration(credit), lambda event: None)
        assert collector.image("image", np.zeros((2, 2, 3), dtype=np.uint8), image_item())
    assert not trace.rows and trace.row_bytes == 0 and trace.counters["dropped_rows"] == 1
    assert not trace.save(tmp_path / "byte-budget.json")["complete"]
    credit.release()
    limited = capture_trace.CaptureCreditTrace(hook_limit=1)
    native = credit.acquire
    with capture_trace.installed(limited, ROOT):
        collector = ResultCollector(configuration(credit), lambda event: None)
        assert collector.image("image", np.zeros((2, 2, 3), dtype=np.uint8), image_item())
    assert limited.disabled and limited.counters["hook_overflow"] == 1
    assert credit.acquire is native and limited.observer_retired
    credit.release()


def testNativeErrorSurvivesConcurrentObserverRecordError(monkeypatch):
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    native_error = RuntimeError("product error")

    class Credit:
        def acquire(self, block=True):
            raise native_error
    monkeypatch.setattr(ResultCollector, "image", acquire_only)
    trace = capture_trace.CaptureCreditTrace()

    def broken_record(*args, **kwargs):
        raise ValueError("observer error")
    with capture_trace.installed(trace, ROOT):
        collector = ResultCollector(configuration(Credit()), lambda event: None)
        monkeypatch.setattr(trace, "record", broken_record)
        with pytest.raises(RuntimeError) as actual:
            collector.image("image", np.zeros((2, 2, 3), dtype=np.uint8), image_item())
        assert actual.value is native_error
    assert trace.disabled and trace.observer_retired
