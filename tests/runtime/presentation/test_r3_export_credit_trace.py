"""Bounded lifecycle/ownership tests with tiny real spawn traffic, no benchmark."""
from contextlib import contextmanager
import hashlib
import importlib.util
import io
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


def testAdoptFinePhasesPreserveNativeStreamingOrderCountsAndPrivacy(tmp_path):
    from emo_master.apps.runtime.presentation.assets import AssetStore
    trace = credit_trace.ExportCreditTrace()
    owner = AssetStore(tmp_path / "assets")
    staging = tmp_path / "private-staging-name.png"
    data = (b"private-image-content" * 7000) + b"tail"
    staging.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    native_open, native_stat, native_replace = Path.open, Path.stat, Path.replace
    native_hash, native_collect = hashlib.sha256, AssetStore._collect
    native_lock, calls, blocks = owner.lock, [], []
    handles = []

    class File:
        def __init__(self, raw):
            self.raw = raw
            self.exits = 0

        def __enter__(self):
            calls.append(("enter",))
            assert self.raw.__enter__() is self.raw
            return self

        def __exit__(self, *args):
            calls.append(("exit",))
            self.exits += 1
            return self.raw.__exit__(*args)

        def read(self, size):
            calls.append(("read", size))
            block = self.raw.read(size)
            if block:
                blocks.append(block)
            return block

    class Digest:
        def __init__(self, raw):
            self.raw = raw

        def update(self, block):
            assert block is blocks[-1]
            calls.append(("update", len(block)))
            return self.raw.update(block)

        def hexdigest(self):
            calls.append(("hexdigest",))
            return self.raw.hexdigest()

    def open_file(path, *args, **kwargs):
        raw = native_open(path, *args, **kwargs)
        if path is staging and trace.current_adopt() is not None:
            calls.append(("open", args, kwargs))
            handle = File(raw)
            handles.append(handle)
            return handle
        return raw

    def stat_file(path, *args, **kwargs):
        if path is staging and trace.current_adopt() is not None:
            calls.append(("stat",))
        return native_stat(path, *args, **kwargs)

    def replace_file(path, *args, **kwargs):
        if path is staging and trace.current_adopt() is not None:
            calls.append(("replace",))
        return native_replace(path, *args, **kwargs)

    def hash_file(*args, **kwargs):
        raw = native_hash(*args, **kwargs)
        if trace.current_adopt() is not None:
            calls.append(("hash_create",))
            return Digest(raw)
        return raw

    def collect(asset):
        if trace.current_adopt() is not None:
            calls.append(("collect",))
        return native_collect(asset)

    with patches() as patch:
        patch(Path, "stat", stat_file)
        patch(Path, "open", open_file)
        patch(Path, "replace", replace_file)
        patch(hashlib, "sha256", hash_file)
        patch(AssetStore, "_collect", collect)
        with credit_trace.installed(trace, patch, ROOT):
            trace.local.metadata = trace.identity(task(), 1)
            image = owner.adopt(staging, "job", "result", {})
            assert trace.current_adopt() is None
            rows = list(trace.rows)
            # Even on an attributed export thread, unrelated I/O/hash/collect
            # outside the original adopt scope generates no fine evidence.
            owner.stats()
            (tmp_path / "outside").write_bytes(b"outside")
            assert hashlib.sha256(b"outside").hexdigest()
            assert trace.rows == rows
    assert owner.lock is native_lock and image["sha256"] == expected
    assert image["byteSize"] == len(data)
    assert owner.read("job", image["resourceId"]) == (data, expected)
    assert len(handles) == 1 and handles[0].exits == 1 and handles[0].raw.closed
    expected_calls = [("collect",), ("stat",), ("hash_create",), ("open", ("rb",), {}), ("enter",)]
    for offset in range(0, len(data), 65536):
        expected_calls += [("read", 65536), ("update", min(65536, len(data) - offset))]
    expected_calls += [("read", 65536), ("exit",), ("replace",), ("hexdigest",), ("hexdigest",)]
    assert calls == expected_calls
    stages = {row["stage"]: row for row in trace.rows}
    enclosing = stages["parent.asset_adopt"]
    for name in credit_trace.ADOPT_PHASE_COUNTS:
        row = stages[name]
        assert enclosing["start_ns"] <= row["start_ns"] <= row["end_ns"] <= enclosing["end_ns"]
    read, update = [stages["parent.asset_adopt." + phase] for phase in ("read", "hash_update")]
    for row in (read, update):
        aggregate = row["aggregate"]
        assert row["span_kind"] == "first_to_last_call_envelope"
        assert 0 <= aggregate["wall_max_ns"] <= aggregate["wall_sum_ns"] <= row["end_ns"] - row["start_ns"]
        assert aggregate["bytes"] == len(data) and aggregate["max_chunk_bytes"] == 65536
        assert aggregate["failures"] == 0
        assert 0 <= aggregate["thread_cpu_max_ns"] <= aggregate["thread_cpu_sum_ns"]
    assert read["aggregate"]["calls"] == len(blocks) + 1
    assert update["aggregate"]["calls"] == len(blocks)
    assert read["aggregate"]["empty_calls"] == 1 and update["aggregate"]["empty_calls"] == 0
    assert not trace.payload()["stage_coverage"]["missing_adopt_phases"]
    assert trace.save(tmp_path / "trace.json")["complete"]
    serialized = json.dumps(trace.payload())
    assert "private-staging-name" not in serialized and "private-image-content" not in serialized
    assert str(tmp_path) not in serialized
    owner.close()


@pytest.mark.parametrize("missing", list(credit_trace.ADOPT_PHASE_COUNTS))
def testMissingSuccessfulAdoptFinePhaseCannotReportComplete(tmp_path, missing):
    from emo_master.apps.runtime.presentation.assets import AssetStore
    trace = credit_trace.ExportCreditTrace()
    owner = AssetStore(tmp_path / "assets")
    staging = tmp_path / "staging"
    staging.write_bytes(b"data")
    try:
        with patches() as patch:
            with credit_trace.installed(trace, patch, ROOT):
                trace.local.metadata = trace.identity(task(), 1)
                owner.adopt(staging, "job", "result", {})
        assert trace.save(tmp_path / "complete.json")["complete"]
        trace.rows[:] = [row for row in trace.rows if row["stage"] != missing]
        assert trace.payload()["stage_coverage"]["missing_adopt_phases"]
        assert not trace.save(tmp_path / "incomplete.json")["complete"]
    finally:
        owner.close()


@pytest.mark.parametrize("fault", ["accumulate", "flush", "begin_adopt", "stream_proxy", "hash_proxy"])
def testAdoptObserverFailureKeepsNativeIntegrityAndClearsScope(tmp_path, monkeypatch, fault):
    from emo_master.apps.runtime.presentation.assets import AssetStore
    trace = credit_trace.ExportCreditTrace()
    owner = AssetStore(tmp_path / "assets")
    staging = tmp_path / "staging"
    data = b"integrity" * 20000
    staging.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()

    def broken(*args, **kwargs):
        raise RuntimeError("private fine observer fault")

    if fault in ("accumulate", "flush"):
        monkeypatch.setattr(credit_trace._AdoptScope, fault, broken)
    elif fault == "begin_adopt":
        monkeypatch.setattr(trace, fault, broken)
    else:
        monkeypatch.setattr(credit_trace, "_AdoptStream" if fault == "stream_proxy" else "_AdoptHash", broken)
    try:
        with patches() as patch:
            with credit_trace.installed(trace, patch, ROOT):
                trace.local.metadata = trace.identity(task(), 1)
                image = owner.adopt(staging, "job", "result", {})
                assert trace.current_adopt() is None
        assert owner.read("job", image["resourceId"]) == (data, expected)
        assert trace.disabled and trace.diagnostic_errors == 1
        assert not trace.save(tmp_path / "trace.json")["complete"]
        assert "private fine observer fault" not in json.dumps(trace.payload())
    finally:
        owner.close()


@pytest.mark.parametrize("observer_fault", [False, True])
@pytest.mark.parametrize("phase", ["read", "update", "exit"])
def testAdoptNativeFailurePropagatesAndContextExitsExactlyOnce(tmp_path, monkeypatch, phase, observer_fault):
    from emo_master.apps.runtime.presentation.assets import AssetStore
    trace = credit_trace.ExportCreditTrace()
    owner = AssetStore(tmp_path / "assets")
    staging = tmp_path / "staging"
    staging.write_bytes(b"data")
    native_open, native_hash = Path.open, hashlib.sha256
    calls, handles = [], []
    failure = OSError("original native failure")
    if observer_fault:
        def broken(*args, **kwargs):
            raise RuntimeError("private aggregate observer failure")
        monkeypatch.setattr(credit_trace._AdoptScope, "accumulate", broken)

    class File:
        def __init__(self, raw):
            self.raw = raw

        def __enter__(self):
            calls.append("enter")
            self.raw.__enter__()
            return self

        def __exit__(self, *args):
            calls.append("exit")
            result = self.raw.__exit__(*args)
            if phase == "exit":
                raise failure
            return result

        def read(self, *args, **kwargs):
            calls.append("read")
            if phase == "read":
                raise failure
            return self.raw.read(*args, **kwargs)

    class Digest:
        def update(self, block):
            calls.append("update")
            raise failure

    def open_file(path, *args, **kwargs):
        raw = native_open(path, *args, **kwargs)
        if path is staging:
            raw = File(raw)
            handles.append(raw)
        return raw

    def hash_file(*args, **kwargs):
        return Digest() if phase == "update" and trace.current_adopt() is not None else native_hash(*args, **kwargs)

    try:
        with patches() as patch:
            patch(Path, "open", open_file)
            patch(hashlib, "sha256", hash_file)
            with credit_trace.installed(trace, patch, ROOT):
                trace.local.metadata = trace.identity(task(), 1)
                with pytest.raises(OSError) as caught:
                    owner.adopt(staging, "job", "result", {})
                assert caught.value is failure and trace.current_adopt() is None
        assert calls.count("enter") == calls.count("exit") == 1
        assert len(handles) == 1 and handles[0].raw.closed
        assert not owner.items and staging.exists()
        if observer_fault:
            assert trace.disabled and trace.diagnostic_errors == 1
            assert "private aggregate observer failure" not in json.dumps(trace.payload())
            return
        assert not trace.disabled
        failed = next(row for row in trace.rows if row["stage"] == "parent.asset_adopt")
        assert failed["outcome"] == "OSError"
        if phase in ("read", "update"):
            failed = next(row for row in trace.rows
                          if row["stage"] == "parent.asset_adopt." + ("hash_update" if phase == "update" else phase))
            assert failed["outcome"] == "OSError" and failed["aggregate"]["failures"] == 1
    finally:
        owner.close()


def testAdoptProxiesKeepNoOwnerOrTraceCycleAndStillForward():
    from emo_master.apps.runtime.presentation.assets import AssetStore
    owner = AssetStore.__new__(AssetStore)
    trace = credit_trace.ExportCreditTrace()
    scope = credit_trace._AdoptScope(trace, owner, Path("private"), trace.identity(task(), 1), None)
    stream = credit_trace._AdoptStream(io.BytesIO(b"content"), scope)
    digest = credit_trace._AdoptHash(hashlib.sha256(), scope)
    owner_ref, trace_ref = weakref.ref(owner), weakref.ref(trace)
    del owner, trace
    assert owner_ref() is None and trace_ref() is None
    with stream as opened:
        assert opened is stream
        assert digest.update(opened.read(65536)) is None
    assert stream.closed and digest.hexdigest() == hashlib.sha256(b"content").hexdigest()


def testNoDiagnosticSerializationOrPostFingerprintBeforeOwnerRetirement(tmp_path, monkeypatch):
    class OwnerThread:
        alive = True

        def is_alive(self):
            return self.alive

    trace = credit_trace.ExportCreditTrace()
    thread = OwnerThread()
    pool = Pool({"thread": thread})
    trace.pool_id(pool)
    calls = []

    def fingerprint(root):
        calls.append(root)
        return {}
    monkeypatch.setattr(credit_trace, "fingerprints", fingerprint)
    with patches() as patch:
        with credit_trace.installed(trace, patch, ROOT):
            with pytest.raises(RuntimeError, match="owner retirement"):
                trace.save(tmp_path / "active.json")
    assert not (tmp_path / "active.json").exists()
    assert calls == [ROOT] and not trace.observer_retired and trace.source_after == {}
    thread.alive = False
    trace.restore()
    assert trace.observer_retired
    assert not trace.save(tmp_path / "retired.json")["complete"]


@pytest.mark.parametrize("observed", [False, True])
def testRealCollectorCannotReuseCreditDuringAdoptButCanAfterOriginalRelease(tmp_path, observed):
    """Controlled causality, not latency: events gate the original adoption."""
    import cv2
    import numpy as np
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    from emo_master.apps.runtime.presentation.exporter import ExportPool

    owner = prepared_service(tmp_path)
    trace = credit_trace.ExportCreditTrace()
    entered, resume, callback_returned, released = [threading.Event() for _ in range(4)]
    original_adopt = owner.assets.adopt
    callbacks, emissions, release_calls = [], [], []
    pixels = np.full((4, 4, 3), 43, np.uint8)
    native_store, native_store_lock, native_asset_lock = owner.store, owner.store.lock, owner.assets.lock

    def gated_adopt(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert resume.wait(5), "test did not release the adoption gate"
        return original_adopt(*args, **kwargs)

    def callback(descriptor, path, outcome):
        callbacks.append((descriptor, outcome))
        owner._exported(descriptor, path, outcome)
        callback_returned.set()

    @contextmanager
    def observer(patch):
        if observed:
            native_record = trace.record

            def record(stage, *args, **kwargs):
                result = native_record(stage, *args, **kwargs)
                if stage == "parent.credit_release":
                    release_calls.append(1)
                    released.set()
                return result
            patch(trace, "record", record)
            with credit_trace.installed(trace, patch, ROOT):
                yield
        else:
            yield

    try:
        with patches() as patch:
            with observer(patch):
                # Gate the currently installed forwarding method, then call it
                # once under the original service/store ownership lifecycle.
                original_adopt = owner.assets.adopt
                patch(owner.assets, "adopt", gated_adopt)
                pool = ExportPool(tmp_path / "staging", callback)
                try:
                    slot = pool.slots[0]
                    native_release = slot["free"].release

                    def release(*args, **kwargs):
                        result = native_release(*args, **kwargs)
                        release_calls.append(1)
                        released.set()
                        return result
                    # Events fire only AFTER the real release. The observed
                    # arm uses its completed boundary, avoiding hook conflicts.
                    if not observed:
                        patch(slot["free"], "release", release)

                    def emit(event):
                        emissions.append(event)
                        pool.submit(event["descriptor"])
                    collector = ResultCollector({"plan": '{"sources":{},"scopes":{}}',
                        "slots": [pool.descriptors()[0]]}, emit)

                    def item(key):
                        return {"identity": {"resultKey": key, "jobId": "job"}, "rawBytes": 0}

                    started = time.monotonic()
                    # The real .5 second export deadline is never changed.
                    owner.pending["result"]["deadline"] = started + .5
                    assert collector.image("image", pixels, item("result"))["pendingImage"]
                    submitted = time.monotonic()
                    assert entered.wait(10)
                    assert callbacks[0][1] == "AVAILABLE"
                    assert started + .5 <= callbacks[0][0]["deadline"] <= submitted + .5
                    assert not callback_returned.is_set() and not released.is_set()
                    assert slot["pending"] == {0} and slot["busy"]
                    rejected = collector.image("image", pixels + 1, item("busy"))
                    assert rejected["state"] == "UNAVAILABLE" and rejected["reasonCode"] == "BUDGET_EXCEEDED"
                    assert "busy" in rejected["reason"] and len(emissions) == 1
                    assert not slot["free"].acquire(False)
                    resume.set()
                    assert callback_returned.wait(10) and released.wait(10)
                    assert release_calls == [1]
                    # The release observer may signal while its enclosing slot
                    # lock is held; take that same lock for the postcondition.
                    with slot["lock"]:
                        assert not slot["pending"] and not slot["busy"]
                    first = owner.store.snapshot("job")["results"][0]
                    assert first.status == "COMPLETE" and first.sources[0].image is not None
                    image = first.sources[0].image
                    data, digest = owner.assets.read("job", image.resourceId)
                    assert hashlib.sha256(data).hexdigest() == digest == image.sha256
                    assert np.array_equal(cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR), pixels)
                    identity = first.identity.model_dump()
                    identity.update(resultKey="second", invocationId="second", resultOrdinal=2)
                    owner.store.begin({"identity": identity, "expected": ["image"]})
                    owner.pending["second"] = {"job": "job", "exports": {}, "deadline": time.monotonic() + .5,
                        "seal": {"sources": [{"sourceId": "image", "pendingImage": True}], "terminal": "COMPLETED"}}
                    callback_returned.clear()
                    released.clear()
                    assert collector.image("image", pixels + 2, item("second"))["pendingImage"]
                    assert len(emissions) == 2
                    assert callback_returned.wait(10) and released.wait(10)
                    assert [outcome for _, outcome in callbacks] == ["AVAILABLE", "AVAILABLE"]
                    assert release_calls == [1, 1] and not pool.errors
                    assert pool.stats["completed"] == 2 and pool.stats["timed_out"] == 0
                    assert owner.assets.read("job", image.resourceId) == (data, digest)
                finally:
                    resume.set()
                    pool.close()
        assert owner.store is native_store and owner.store.lock is native_store_lock
        assert owner.assets.lock is native_asset_lock and not owner.pending
        if observed:
            assert trace.observer_retired and trace.restored
            assert not trace.disabled
            assert trace.save(tmp_path / "trace.json")["complete"]
    finally:
        resume.set()
        owner.assets.close()
