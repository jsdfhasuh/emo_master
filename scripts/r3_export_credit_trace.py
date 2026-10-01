"""Default-off, bounded parent-side export-credit lifecycle diagnostics.

Install explicitly for one serial trial before creating its ExportPool. This
module never installs itself, changes a child descriptor/reply, or acquires a
runtime lock. Parent methods are forwarded exactly once. The original service,
store, lock, semaphore and worker-side semaphore methods keep their identities.
Only the parent's semaphore.release attribute is temporarily observed.

The callback-to-adopt interval includes store-lock waiting AND Python work; it
is not an exact lock-wait measurement. No optimization or ownership change is
implemented here. Install after any benchmark ExportPool subclass is selected.
"""
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
import threading
import time
import weakref


ROW_LIMIT = 6000
HOOK_LIMIT = 128
POOL_LIMIT = 4
TRACE_BYTES = 12 * 1024 * 1024
SOURCE_FILES = (
    "scripts/r3_export_credit_trace.py", "scripts/r3_measure.py",
    "scripts/r3_policy_measure.py",
    "src/emo_master/apps/runtime/presentation/exporter.py",
    "src/emo_master/apps/runtime/presentation/service.py",
    "src/emo_master/apps/runtime/presentation/store.py",
    "src/emo_master/apps/runtime/presentation/assets.py",
    "src/emo_master/apps/runtime/presentation/collector.py",
)
SUCCESS_STAGES = (
    "parent.export_submit", "parent.task_dequeued", "parent.pipe_send",
    "parent.pipe_poll", "parent.pipe_recv", "parent.export_callback",
    "parent.credit_release",
)
_MISSING = object()


def fingerprints(root):
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            if (root / name).is_file() else None for name in SOURCE_FILES}


def _clocks():
    cpu = getattr(time, "thread_time_ns", None)
    return time.perf_counter_ns(), cpu() if cpu is not None else None


class _Forward:
    """An instance method wrapper must not retain its own owner in a cycle."""
    def __init__(self, owner, method):
        if getattr(method, "__self__", None) is owner and hasattr(method, "__func__"):
            self.owner = weakref.ref(owner)
            self.function = method.__func__
            self.method = None
        else:
            # Semaphore.release is assigned a native SemLock bound method;
            # its bound owner is the SemLock, not the Semaphore being patched.
            self.owner = self.function = None
            self.method = method

    def __call__(self, *args, **kwargs):
        if self.method is not None:
            return self.method(*args, **kwargs)
        owner = self.owner()
        if owner is None:
            raise ReferenceError("observed method owner retired")
        return self.function(owner, *args, **kwargs)


class ExportCreditTrace:
    def __init__(self, row_limit=ROW_LIMIT, hook_limit=HOOK_LIMIT):
        if not 0 < row_limit <= ROW_LIMIT or not 0 < hook_limit <= HOOK_LIMIT:
            raise ValueError("export-credit trace limits exceed their budgets")
        self.row_limit, self.hook_limit = row_limit, hook_limit
        self.rows = []
        self.counters = Counter()
        self.lock = threading.RLock()
        self.local = threading.local()
        self.pools = weakref.WeakKeyDictionary()
        self.pool_sequence = 0
        self.hooks = []
        self.disabled = False
        self.diagnostic_errors = 0
        self.last_diagnostic_error = None
        self.observer_calls = self.observer_cpu_ns = self.observer_wall_ns = 0
        self.source_before, self.source_after = {}, {}
        self.restored = False
        self.observer_retired = False

    def observe(self, operation, /, *args, default=None, **kwargs):
        """Only observer work goes here; never retry an observed operation."""
        if self.disabled:
            return default
        start = None
        try:
            start = _clocks()
            return operation(*args, **kwargs)
        except Exception as error:
            self.disabled = True
            self.diagnostic_errors += 1
            self.last_diagnostic_error = type(error).__name__[:80]
            return default
        finally:
            # Accounting failure also disables evidence, not runtime behavior.
            try:
                end = _clocks()
                with self.lock:
                    self.observer_calls += 1
                    if start is not None:
                        self.observer_wall_ns += end[0] - start[0]
                        if start[1] is not None and end[1] is not None:
                            self.observer_cpu_ns += end[1] - start[1]
            except Exception as error:
                if not self.disabled:
                    self.diagnostic_errors += 1
                    self.last_diagnostic_error = type(error).__name__[:80]
                self.disabled = True

    def stamp(self):
        """Take a boundary directly, before any observer lock/accounting work."""
        if self.disabled:
            return None
        try:
            return _clocks()
        except Exception as error:
            self.disabled = True
            self.diagnostic_errors += 1
            self.last_diagnostic_error = type(error).__name__[:80]
            return None

    def count(self, key, amount=1):
        with self.lock:
            self.counters[key] += amount

    def pool_id(self, pool):
        with self.lock:
            if pool not in self.pools:
                if self.pool_sequence >= POOL_LIMIT:
                    self.counters["pool_overflow"] += 1
                    return None
                self.pool_sequence += 1
                self.pools[pool] = self.pool_sequence
            return self.pools[pool]

    def identity(self, task, pool_id):
        if not isinstance(task, dict) or pool_id is None:
            self.count("invalid_identity")
            return None
        details = {"job_id": task.get("jobId"), "result_key": task.get("key"),
                   "source_id": task.get("sourceId")}
        if any(not isinstance(value, str) or not 0 < len(value) <= 160
               for value in details.values()):
            self.count("invalid_identity")
            return None
        slot, lane = task.get("slot"), task.get("lane", 0)
        if type(slot) is not int or slot not in (0, 1) or type(lane) is not int or lane not in (0, 1):
            self.count("invalid_identity")
            return None
        return {**details, "pool_id": pool_id, "slot": slot, "lane": lane}

    def record(self, stage, start, end, metadata, outcome="OK", **fields):
        if metadata is None:
            return
        row = {"stage": stage, "start_ns": start[0], "end_ns": end[0],
               "elapsed_ms": (end[0] - start[0]) / 1e6,
               "thread_cpu_ns": end[1] - start[1] if start[1] is not None and end[1] is not None else None,
               "thread_id": threading.get_ident(), "outcome": outcome,
               **metadata, **fields}
        with self.lock:
            self.counters["spans_completed"] += 1
            if len(self.rows) < self.row_limit:
                self.rows.append(row)
            else:
                self.counters["dropped_rows"] += 1

    def call(self, stage, metadata, operation, /, *args, fields=None, **kwargs):
        if self.disabled or metadata is None:
            return operation(*args, **kwargs)
        start = self.stamp()
        outcome = "OK"
        try:
            return operation(*args, **kwargs)
        except BaseException as error:
            outcome = type(error).__name__[:80]
            raise
        finally:
            # Sample operation boundaries before any observer lock is entered.
            end = self.stamp()
            if start is not None and end is not None:
                self.observe(self.record, stage, start, end, metadata, outcome, **(fields or {}))

    def current(self):
        return getattr(self.local, "metadata", None)

    def _set_task(self, task, pool_id, stamp):
        self.local.metadata = self.identity(task, pool_id)
        if stamp is not None:
            self.record("parent.task_dequeued", stamp, stamp, self.current())

    def _patch_instance(self, owner, name, factory):
        # Keep raw attribute provenance: inherited methods must be deleted on
        # restoration, not restored as newly assigned bound instance methods.
        with self.lock:
            self.hooks[:] = [item for item in self.hooks if item[0]() is not None]
            if any(ref() is owner and attr == name for ref, attr, *_ in self.hooks):
                return
            if len(self.hooks) >= self.hook_limit:
                self.counters["hook_overflow"] += 1
                self.disabled = True
                return
            previous = vars(owner).get(name, _MISSING)
            wrapper = factory(_Forward(owner, getattr(owner, name)))
            wrapper._r3_credit_original = previous
            entry = (weakref.ref(owner), name, weakref.ref(wrapper))
            self.hooks.append(entry)
            setattr(owner, name, wrapper)
            self.counters["hook_peak"] = max(self.counters["hook_peak"], len(self.hooks))

    def hook_connection(self, pool, slot):
        pool_id = self.pool_id(pool)
        ref = weakref.ref(self)
        for name in ("send", "poll", "recv"):
            def factory(original, stage="parent.pipe_" + name):
                def observed(*args, **kwargs):
                    trace = ref()
                    if trace is None or trace.disabled:
                        return original(*args, **kwargs)
                    details = trace.observe(trace.current)
                    if details is None or details["pool_id"] != pool_id:
                        return original(*args, **kwargs)
                    return trace.call(stage, details, original, *args, **kwargs)
                return observed
            self._patch_instance(slot["connection"], name, factory)

    def hook_slot(self, pool, slot):
        pool_id, slot_index, ref = self.pool_id(pool), slot["index"], weakref.ref(self)

        def queue_factory(original):
            def get(*args, **kwargs):
                trace = ref()
                if trace is not None:
                    trace.observe(setattr, trace.local, "metadata", None)
                task = original(*args, **kwargs)
                if trace is not None:
                    stamp = trace.stamp()
                    trace.observe(trace._set_task, task, pool_id, stamp)
                return task
            return get
        self._patch_instance(slot["queue"], "get", queue_factory)
        for lane, credit in enumerate(slot["laneFree"]):
            def release_factory(original, lane_index=lane):
                def release(*args, **kwargs):
                    trace = ref()
                    details = trace.observe(trace.current) if trace is not None else None
                    if trace is None or trace.disabled:
                        return original(*args, **kwargs)
                    if (details is None or (details["pool_id"], details["slot"], details["lane"])
                            != (pool_id, slot_index, lane_index)):
                        return trace.call("parent.credit_release_unattributed",
                            {"pool_id": pool_id, "slot": slot_index, "lane": lane_index,
                             "release_reason": "outside_dequeued_export"}, original, *args, **kwargs)
                    # The end boundary proves that the original release returned.
                    # A failed release is never labeled as released/reusable.
                    return trace.call("parent.credit_release", details, original, *args, **kwargs)
                return release
            self._patch_instance(credit, "release", release_factory)

    def restore(self):
        # Must run even when observe() has disabled itself. No runtime object is
        # retained just for restoration and no live owner is joined here.
        failed = []
        try:
            alive = sum(bool(slot.get("thread") and slot["thread"].is_alive())
                        for pool in list(self.pools) for slot in pool.slots)
            if alive:
                self.count("owners_active_at_exit", alive)
        except Exception:
            alive = 1
            self.count("owner_retirement_unverified")
        for ref, name, wrapper_ref in reversed(self.hooks):
            owner, wrapper = ref(), wrapper_ref()
            if owner is None:
                continue
            try:
                if wrapper is None or vars(owner).get(name, _MISSING) is not wrapper:
                    self.count("restore_conflict")
                elif wrapper._r3_credit_original is _MISSING:
                    delattr(owner, name)
                else:
                    setattr(owner, name, wrapper._r3_credit_original)
            except Exception as error:
                failed.append((ref, name, wrapper_ref))
                self.disabled = True
                self.diagnostic_errors += 1
                self.last_diagnostic_error = type(error).__name__[:80]
        self.hooks[:] = reversed(failed)
        self.restored = not self.hooks and not self.counters.get("restore_conflict")
        self.observer_retired = self.restored and not alive

    def payload(self):
        with self.lock:
            rows, counters = list(self.rows), dict(self.counters)
        coverage, missing, incomplete = {}, Counter(), Counter()
        for row in rows:
            if "result_key" not in row:
                continue
            key = tuple(row.get(name) for name in ("pool_id", "job_id", "result_key", "source_id", "slot", "lane"))
            item = coverage.setdefault(key, {"stages": Counter(), "available": False})
            item["stages"][row["stage"]] += row["outcome"] == "OK"
            if row["stage"] == "parent.export_callback" and row.get("export_outcome") == "AVAILABLE":
                item["available"] = True
        for item in coverage.values():
            if item["stages"]["parent.export_submit"]:
                for stage in ("parent.task_dequeued", "parent.export_callback"):
                    if item["stages"][stage] != 1:
                        incomplete[stage + "_expected_one"] += 1
                if not (item["stages"]["parent.credit_release"] or item["stages"]["parent.export_quarantine"]):
                    incomplete["release_or_quarantine_missing"] += 1
            if item["available"]:
                for stage in SUCCESS_STAGES:
                    if item["stages"][stage] != 1:
                        missing[stage + "_expected_one"] += 1
        return {"role": "export_credit", "pid": os.getpid(), "row_limit": self.row_limit,
                "hook_limit": self.hook_limit, "pool_limit": POOL_LIMIT,
                "rows": rows, "counters": counters,
                "dropped_rows": counters.get("dropped_rows", 0),
                "instrumentation_disabled": self.disabled, "diagnostic_errors": self.diagnostic_errors,
                "last_diagnostic_error_type": self.last_diagnostic_error,
                "restored": self.restored, "observer_retired": self.observer_retired,
                "pending_instance_hooks": len(self.hooks),
                "observer_cost": {"calls": self.observer_calls, "wall_ns": self.observer_wall_ns,
                    "thread_cpu_ns": self.observer_cpu_ns,
                    "interpretation": "Observer callbacks only; excludes forwarding and clock/accounting overhead. Inclusive when nested; not total instrumentation cost."},
                "stage_coverage": {"observed_exports": len(coverage),
                    "admitted_exports": sum(bool(item["stages"]["parent.export_submit"]) for item in coverage.values()),
                    "incomplete_admitted_stages": dict(incomplete),
                    "available_callbacks": sum(item["available"] for item in coverage.values()),
                    "missing_available_stages": dict(missing)},
                "source_before": self.source_before, "source_after": self.source_after,
                "source_complete": (set(self.source_before) == set(SOURCE_FILES)
                    and set(self.source_after) == set(SOURCE_FILES)
                    and all(self.source_before.values()) and all(self.source_after.values())),
                "source_unchanged": bool(self.source_before) and self.source_before == self.source_after,
                "clock": "Parent process perf_counter_ns; thread_time_ns where supported",
                "interpretation": "Spans are inclusive, include nested observer work, and must not be summed. Boundaries are sampled directly before observer bookkeeping; use ABBA controls for perturbation. Callback entry to adopt entry includes store-lock wait and Python work, not exact lock wait. "
                    "Pipe poll returning does not alone prove a valid reply. Credit release is observed only when the original parent release is called; only OK proves it returned. "
                    "Quarantine/reap spans do not imply a later credit release. No child protocol, semaphore acquire, Condition, service/store/lock identity or owner disposal is changed. "
                    "Thread CPU may be coarsely quantized on Windows; no GIL ownership is observed. Missing rows/identity/coverage never imply success.",
                "performance_verdict": "NOT_EVALUATED"}

    def save(self, destination):
        payload = self.payload()
        content = json.dumps(payload, separators=(",", ":")).encode()
        if len(content) > TRACE_BYTES:
            raise RuntimeError("export-credit trace file budget exceeded")
        destination.write_bytes(content)
        failures = {key: value for key, value in payload["counters"].items()
                    if value and any(part in key for part in ("overflow", "invalid", "conflict", "dropped", "unverified", "active_at_exit"))}
        complete = (not self.disabled and not self.diagnostic_errors and not failures
                    and self.observer_retired and payload["source_complete"] and payload["source_unchanged"]
                    and not payload["stage_coverage"]["missing_available_stages"]
                    and not payload["stage_coverage"]["incomplete_admitted_stages"])
        return {"enabled": True, "file": destination.name, "complete": bool(complete),
                "observer_retired": self.observer_retired, "rows": len(payload["rows"]),
                "dropped_rows": payload["dropped_rows"],
                "source_complete": payload["source_complete"], "source_unchanged": payload["source_unchanged"],
                "diagnostic_errors": self.diagnostic_errors, "counter_failures": failures,
                "stage_coverage": payload["stage_coverage"], "observer_cost": payload["observer_cost"],
                "performance_verdict": "NOT_EVALUATED"}


@contextmanager
def installed(trace, patch, root):
    """Outer ``patch`` owns class restoration; this scope restores instances.

    The caller must finish its original pool/service close calls inside this
    scope. This helper does not stop, wait for, or dispose any runtime owner.
    """
    from emo_master.apps.runtime.presentation import exporter, service
    from emo_master.apps.runtime.presentation.assets import AssetStore
    from emo_master.apps.runtime.presentation.store import ResultStore

    ref = weakref.ref(trace)
    trace.source_before = trace.observe(fingerprints, root, default={})
    pool_type = service.ExportPool
    original_spawn = pool_type._spawn

    def spawn(pool, slot):
        active = ref()
        details = active.observe(active.current) if active is not None else None
        if active is None:
            result = original_spawn(pool, slot)
        else:
            result = active.call("parent.export_spawn", details, original_spawn, pool, slot)
            active.observe(active.hook_connection, pool, slot)
        return result
    # Only patch a class that owns _spawn, preserving inherited provenance even
    # with the benchmark's derived MeasuredPool selected by the caller.
    spawn_owner = next(cls for cls in pool_type.__mro__ if "_spawn" in vars(cls))
    patch(spawn_owner, "_spawn", spawn)
    original_run, original_submit = exporter.ExportPool._run, exporter.ExportPool.submit

    def run(pool, slot):
        active = ref()
        if active is not None:
            active.observe(active.hook_slot, pool, slot)
        del active
        try:
            return original_run(pool, slot)
        finally:
            active = ref()
            if active is not None:
                active.observe(setattr, active.local, "metadata", None)

    def submit(pool, task):
        active = ref()
        if active is None:
            return original_submit(pool, task)
        pool_id = active.observe(active.pool_id, pool)
        details = active.observe(active.identity, task, pool_id)
        return active.call("parent.export_submit", details, original_submit, pool, task)
    patch(exporter.ExportPool, "_run", run)
    patch(exporter.ExportPool, "submit", submit)

    def wrap(owner, name, stage):
        original = getattr(owner, name)

        def observed(*args, **kwargs):
            active = ref()
            if active is None:
                return original(*args, **kwargs)
            return active.call(stage, active.observe(active.current), original, *args, **kwargs)
        patch(owner, name, observed)

    original_exported = service.PresentationService._exported

    def exported(owner, task, path, outcome):
        active = ref()
        if active is None:
            return original_exported(owner, task, path, outcome)
        details = active.observe(active.current)
        # Only a queue-dequeued task on the owning export thread is attributed.
        # A direct callback or unrelated thread must not fabricate a lifecycle.
        fields = {"export_outcome": outcome if outcome in ("AVAILABLE", "EXPORT_FAILED", "EXPORT_TIMEOUT") else "OTHER"}
        return active.call("parent.export_callback", details, original_exported,
                           owner, task, path, outcome, fields=fields)
    patch(service.PresentationService, "_exported", exported)
    for owner, name, stage in (
            (AssetStore, "adopt", "parent.asset_adopt"),
            (ResultStore, "close", "parent.result_close"),
            (service.PresentationService, "_finish", "parent.result_finish"),
            (service.PresentationService, "_retain", "parent.asset_retain"),
            (exporter.ExportPool, "_reap", "parent.export_reap"),
            (exporter.ExportPool, "_quarantine", "parent.export_quarantine")):
        wrap(owner, name, stage)
    try:
        yield trace
    finally:
        trace.restore()
        trace.source_after = trace.observe(fingerprints, root, default={})
