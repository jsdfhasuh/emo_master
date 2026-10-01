"""Bounded lifecycle/ownership tests with tiny real spawn traffic, no benchmark."""
from contextlib import contextmanager
import importlib.util
import json
import multiprocessing
from pathlib import Path
import queue
import threading
import time
import weakref

import pytest


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("r3_export_credit_trace", ROOT / "scripts" / "r3_export_credit_trace.py")
assert SPEC is not None and SPEC.loader is not None
credit_trace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(credit_trace)


@contextmanager
def patches():
    changes = []
    missing = object()

    def patch(owner, name, value):
        changes.append((owner, name, vars(owner).get(name, missing)))
        setattr(owner, name, value)
    try:
        yield patch
    finally:
        for owner, name, value in reversed(changes):
            if value is missing:
                delattr(owner, name)
            else:
                setattr(owner, name, value)


def task():
    return {"slot": 0, "lane": 0, "shape": [2, 2, 3], "sourceId": "image",
            "key": "result", "jobId": "job", "frozenAt": time.monotonic(),
            "provenance": {"frameIdentity": "frame", "coordinateSpaceId": "space", "trust": "unknown"}}


def prepared_service(root):
    from emo_master.apps.runtime.presentation.assets import AssetStore
    from emo_master.apps.runtime.presentation.service import PresentationService
    from emo_master.apps.runtime.presentation.store import ResultStore
    owner = PresentationService.__new__(PresentationService)
    owner.store, owner.assets = ResultStore(), AssetStore(root / "assets")
    owner.jobs = {"job": {"credits": threading.Semaphore(0)}}
    owner.pending = {"result": {"job": "job", "exports": {}, "deadline": time.monotonic() + 15,
        "seal": {"sources": [{"sourceId": "image", "pendingImage": True}], "terminal": "COMPLETED"}}}
    owner.store.begin({"identity": {"runtimeInstanceId": "runtime", "jobId": "job",
        "resultScopeId": "scope", "invocationId": "invocation", "resultKey": "result",
        "resultOrdinal": 1, "executionRevision": "a" * 64, "capturePlanRevision": "b" * 64,
        "mode": "runtime"}, "expected": ["image"]})
    return owner


def _spawn_credit_probe(credit, pipe):
    try:
        acquired = credit.acquire(timeout=2)
        if acquired:
            credit.release()
        pipe.send((acquired, type(credit.release).__name__, type(credit.release.__self__).__name__))
    finally:
        pipe.close()


def _broken_encoder(connection, memory_name):
    connection.send("ready")
    connection.recv()
    connection.send("corrupt")
    connection.close()


class Pool:
    def __init__(self, slot):
        self.slots = [slot]


def testFiniteRowsIdentityPrivacyAndIncompleteAdmittedCoverage(tmp_path):
    trace = credit_trace.ExportCreditTrace(row_limit=2)
    details = trace.identity(task(), 1)
    trace.call("parent.export_submit", details, lambda: None)
    trace.call("parent.export_callback", details, lambda: None, fields={"export_outcome": "AVAILABLE"})
    trace.call("dropped", details, lambda: None)
    assert trace.identity({**task(), "sourceId": "x" * 161}, 1) is None
    assert trace.identity({**task(), "slot": True}, 1) is None
    payload = trace.payload()
    assert len(payload["rows"]) == 2 and payload["dropped_rows"] == 1
    assert payload["stage_coverage"]["admitted_exports"] == 1
    assert payload["stage_coverage"]["incomplete_admitted_stages"]["release_or_quarantine_missing"] == 1
    assert payload["stage_coverage"]["missing_available_stages"]["parent.credit_release_expected_one"] == 1
    assert "provenance" not in json.dumps(payload) and "shape" not in json.dumps(payload)
    assert "frozenAt" not in json.dumps(payload)
    assert not trace.save(tmp_path / "trace.json")["complete"]
    with pytest.raises(ValueError, match="budgets"):
        credit_trace.ExportCreditTrace(row_limit=credit_trace.ROW_LIMIT + 1)


@pytest.mark.parametrize("fault", ["record", "clock"])
def testObserverFailureForwardsOnceAndPreservesActualException(monkeypatch, fault):
    trace = credit_trace.ExportCreditTrace()
    details = trace.identity(task(), 1)
    calls = []

    def broken(*args, **kwargs):
        raise RuntimeError("private observer text")

    if fault == "record":
        monkeypatch.setattr(trace, "record", broken)
    else:
        monkeypatch.setattr(credit_trace, "_clocks", broken)

    def operation():
        calls.append(1)
        raise ValueError("real operation error")
    with pytest.raises(ValueError, match="real operation error"):
        trace.call("test", details, operation)
    assert calls == [1] and trace.disabled and trace.diagnostic_errors == 1
    assert "private observer text" not in json.dumps(trace.payload())


def testInstanceRestorationPreservesRawProvenanceAndDoesNotRetainOwners():
    class Connection:
        def send(self, value):
            return value

        def poll(self, timeout=0):
            return False

        def recv(self):
            return "reply"

    trace = credit_trace.ExportCreditTrace()
    connection = Connection()
    slot = {"connection": connection, "index": 0}
    pool = Pool(slot)
    trace.hook_connection(pool, slot)
    assert connection.send("one") == "one"
    assert all(name in vars(connection) for name in ("send", "poll", "recv"))
    trace.restore()
    assert not any(name in vars(connection) for name in ("send", "poll", "recv"))
    trace.hook_connection(pool, slot)
    owner_ref, pool_ref = weakref.ref(connection), weakref.ref(pool)
    del slot, connection, pool
    # Refcount collection is enough: no gc.collect is needed to break cycles.
    assert owner_ref() is None and pool_ref() is None
    trace.restore()
    assert trace.observer_retired


def testHookBudgetDisablesEvidenceWithoutAddingHooks():
    trace = credit_trace.ExportCreditTrace(hook_limit=1)
    # Weak-referenceable actual queue instances also have inherited methods.
    first, second = queue.Queue(), queue.Queue()
    trace._patch_instance(first, "qsize", lambda original: lambda: original())
    trace._patch_instance(second, "qsize", lambda original: lambda: original())
    assert trace.disabled and trace.counters["hook_overflow"] == 1
    assert "qsize" not in vars(second) and first.qsize() == 0
    trace.restore()
    assert "qsize" not in vars(first)


def testActualSpawnUsesNativeUnwrappedSemaphoreAndParentRestoresIdentity():
    context = multiprocessing.get_context("spawn")
    credit, other = context.BoundedSemaphore(1), context.BoundedSemaphore(1)
    original_release, original_acquire = credit.release, credit.acquire
    trace = credit_trace.ExportCreditTrace()
    slot = {"index": 0, "queue": queue.Queue(), "laneFree": [credit, other]}
    pool = Pool(slot)
    trace.hook_slot(pool, slot)
    assert credit.acquire is original_acquire
    assert credit.release is not original_release
    parent, child = context.Pipe()
    process = context.Process(target=_spawn_credit_probe, args=(credit, child))
    try:
        process.start()
        child.close()
        assert parent.poll(10)
        acquired, method_type, bound_owner = parent.recv()
        assert acquired and method_type == "builtin_function_or_method" and bound_owner == "SemLock"
        process.join(5)
        assert process.exitcode == 0
        assert not trace.rows  # Child release never ran the parent wrapper.
        assert credit.acquire(False) and not credit.acquire(False)
        trace.local.metadata = trace.identity(task(), 1)
        credit.release()
        assert len(trace.rows) == 1 and trace.rows[0]["stage"] == "parent.credit_release"
        assert trace.rows[0]["outcome"] == "OK"
        with pytest.raises(ValueError):
            credit.release()  # Original BoundedSemaphore rejection still applies.
        assert trace.rows[-1]["outcome"] == "ValueError"
    finally:
        if process.is_alive():
            process.terminate()
        process.join(5)
        process.close()
        parent.close()
        child.close()
        trace.restore()
    assert credit.release is original_release and credit.acquire is original_acquire
    assert "get" not in vars(slot["queue"])


@pytest.mark.parametrize("fault", [None, "record", "identity", "hook_slot"])
def testRealExportCallbackPreservesIdentityOwnershipAndRetirement(tmp_path, fault):
    from emo_master.apps.runtime.presentation.exporter import ExportPool
    from emo_master.apps.runtime.presentation.service import PresentationService
    trace = credit_trace.ExportCreditTrace()
    original_exported, original_spawn = PresentationService._exported, ExportPool._spawn
    owner = prepared_service(tmp_path)
    original_store, original_lock, original_assets = owner.store, owner.store.lock, owner.assets
    condition = threading.Condition(original_lock)
    completed = threading.Event()
    raw_methods = []

    def callback(descriptor, path, outcome):
        owner._exported(descriptor, path, outcome)
        completed.set()

    with patches() as patch:
        with credit_trace.installed(trace, patch, ROOT):
            pool = ExportPool(tmp_path / "staging", callback)
            try:
                slot = pool.slots[0]
                # Pool startup completes before _run's observer necessarily runs.
                until = time.monotonic() + 3
                while "get" not in vars(slot["queue"]) and time.monotonic() < until:
                    time.sleep(.001)
                for entry in pool.slots:
                    for credit in entry["laneFree"]:
                        raw_methods.append((credit, getattr(credit.release, "_r3_credit_original", credit.release)))
                if fault:
                    def broken(*args, **kwargs):
                        raise RuntimeError("private observer fault")
                    setattr(trace, fault, broken)
                    if fault == "hook_slot":
                        trace.observe(trace.hook_slot, pool, slot)
                slot["memory"].buf[:12] = b"\x11" * 12
                assert slot["free"].acquire(False)
                pool.submit(task())
                assert completed.wait(10)
            finally:
                pool.close()
        assert all(credit.release is raw for credit, raw in raw_methods)
    assert original_exported is PresentationService._exported and original_spawn is ExportPool._spawn
    assert owner.store is original_store and owner.store.lock is original_lock
    assert condition._lock is original_lock and owner.assets is original_assets
    result = owner.store.snapshot("job")["results"][0]
    assert result.status == "COMPLETE" and result.sources[0].image is not None
    assert not owner.pending and pool.stats["completed"] == 1
    assert slot["free"].acquire(False) and not slot["free"].acquire(False)
    slot["free"].release()
    owner.assets.close()
    summary = trace.save(tmp_path / "credit.json")
    assert summary["observer_retired"]
    if fault:
        assert trace.disabled and summary["diagnostic_errors"] == 1 and not summary["complete"]
        assert "private observer fault" not in json.dumps(trace.payload())
        return
    assert summary["complete"] and summary["source_complete"] and summary["source_unchanged"]
    rows = {row["stage"]: row for row in trace.rows}
    expected = set(credit_trace.SUCCESS_STAGES) | {"parent.asset_adopt", "parent.result_finish",
        "parent.result_close", "parent.asset_retain"}
    assert expected <= set(rows)
    assert all(row["source_id"] == "image" for row in trace.rows)
    assert rows["parent.pipe_recv"]["end_ns"] <= rows["parent.export_callback"]["start_ns"]
    assert rows["parent.export_callback"]["start_ns"] <= rows["parent.asset_adopt"]["start_ns"]
    assert rows["parent.asset_adopt"]["end_ns"] <= rows["parent.result_finish"]["start_ns"]
    assert rows["parent.export_callback"]["end_ns"] <= rows["parent.credit_release"]["start_ns"]
    assert summary["stage_coverage"]["admitted_exports"] == 1
    assert summary["stage_coverage"]["available_callbacks"] == 1
    assert not summary["stage_coverage"]["missing_available_stages"]


def testFailedReapQuarantinesWithoutAnyFalseRelease(tmp_path):
    from emo_master.apps.runtime.presentation.exporter import ExportPool
    trace = credit_trace.ExportCreditTrace()
    owner, completed = prepared_service(tmp_path), threading.Event()

    def callback(descriptor, path, outcome):
        owner._exported(descriptor, path, outcome)
        completed.set()

    with patches() as patch:
        with credit_trace.installed(trace, patch, ROOT):
            pool = ExportPool(tmp_path / "staging", callback, worker=_broken_encoder)
            original_reap = pool._reap

            def fail_reap(slot):
                raise OSError("injected owner cannot reap")
            pool._reap = fail_reap
            try:
                slot = pool.slots[0]
                assert slot["free"].acquire(False)
                pool.submit(task())
                assert completed.wait(10)
            finally:
                pool._reap = original_reap
                pool.close()
    rows = trace.rows
    assert any(row["stage"] == "parent.export_quarantine" and row["outcome"] == "OK" for row in rows)
    assert not any(row["stage"] == "parent.credit_release" for row in rows)
    callback_row = next(row for row in rows if row["stage"] == "parent.export_callback")
    assert callback_row["export_outcome"] == "EXPORT_FAILED"
    assert owner.store.snapshot("job")["results"][0].status == "INCOMPLETE"
    assert not trace.payload()["stage_coverage"]["incomplete_admitted_stages"]
    owner.assets.close()


def testRowsAndWrappersDoNotKeepTraceAlive():
    trace = credit_trace.ExportCreditTrace()
    queue_owner = queue.Queue()
    slot = {"index": 0, "queue": queue_owner,
            "laneFree": [threading.Semaphore(1), threading.Semaphore(1)]}
    pool = Pool(slot)
    trace.hook_slot(pool, slot)
    trace_ref = weakref.ref(trace)
    del trace
    assert trace_ref() is None
    queue_owner.put(task())
    assert queue_owner.get_nowait()["jobId"] == "job"
    assert slot["laneFree"][0].acquire(False)
    slot["laneFree"][0].release()


def testFailedOwnedRestorationCanRetryAndExternalReplacementIsPreserved():
    class Connection:
        def __init__(self):
            self.fail_delete = False

        def send(self, value):
            return value

        def __delattr__(self, name):
            if name == "send" and self.fail_delete:
                raise OSError("injected restoration failure")
            object.__delattr__(self, name)

    trace = credit_trace.ExportCreditTrace()
    connection = Connection()
    trace._patch_instance(connection, "send", lambda original: lambda value: original(value))
    connection.fail_delete = True
    trace.restore()
    assert len(trace.hooks) == 1 and not trace.restored and not trace.observer_retired
    assert connection.send("once") == "once"
    connection.fail_delete = False
    trace.restore()
    assert not trace.hooks and trace.restored and trace.observer_retired
    assert "send" not in vars(connection)
    assert trace.disabled and trace.diagnostic_errors == 1
    trace._patch_instance(connection, "send", lambda original: lambda value: original(value))
    def replacement(value):
        return "external", value
    connection.send = replacement
    trace.restore()
    assert connection.send is replacement and not trace.restored
    assert trace.counters["restore_conflict"] == 1


def testMeasuredPoolReceiverWorkIsIncludedAndInheritedClassProvenanceRestored(tmp_path):
    from scripts import r3_measure as base
    from emo_master.apps.runtime.presentation import service
    trace = credit_trace.ExportCreditTrace()
    owner, completed = prepared_service(tmp_path), threading.Event()
    receiver_spans = []
    raw_receiver = base.TraceReceiver.recv
    raw_spawn = base.MeasuredPool._spawn

    def receiver(connection):
        start = time.perf_counter_ns()
        try:
            return raw_receiver(connection)
        finally:
            receiver_spans.append((start, time.perf_counter_ns()))

    def callback(descriptor, path, outcome):
        owner._exported(descriptor, path, outcome)
        completed.set()

    previous_traces = dict(base.ENCODER_TRACES)
    base.ENCODER_TRACES.clear()
    try:
        with patches() as patch:
            patch(service, "ExportPool", base.MeasuredPool)
            patch(base.TraceReceiver, "recv", receiver)
            with credit_trace.installed(trace, patch, ROOT):
                pool = service.ExportPool(tmp_path / "staging", callback)
                try:
                    slot = pool.slots[0]
                    assert isinstance(slot["connection"], base.TraceReceiver)
                    assert "recv" in vars(slot["connection"])
                    slot["memory"].buf[:12] = b"\x22" * 12
                    assert slot["free"].acquire(False)
                    pool.submit(task())
                    assert completed.wait(10)
                finally:
                    pool.close()
            assert all("recv" not in vars(slot["connection"]) for slot in pool.slots
                       if slot["connection"] is not None)
        assert base.MeasuredPool._spawn is raw_spawn and base.TraceReceiver.recv is raw_receiver
        assert "_run" not in vars(base.MeasuredPool)
        assert len(receiver_spans) == 1
        span = next(row for row in trace.rows if row["stage"] == "parent.pipe_recv")
        assert span["start_ns"] <= receiver_spans[0][0] <= receiver_spans[0][1] <= span["end_ns"]
        assert any(row["stage"] == "export.file_write" for batch in base.ENCODER_TRACES.values()
                   for row in batch["rows"])
        assert trace.save(tmp_path / "credit.json")["complete"]
    finally:
        owner.assets.close()
        base.ENCODER_TRACES.clear()
        base.ENCODER_TRACES.update(previous_traces)
