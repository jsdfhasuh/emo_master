"""Default-off, bounded observation of the worker's original capture acquire.

Only an explicit ``installed(trace, root)`` scope installs anything. The native
Semaphore and its pickle protocol are unchanged: its actual ``acquire`` instance
attribute is temporarily wrapped, after worker construction/unpickling. The
original call receives the exact arguments once. No lock, credit query, extra
acquire/release, owner wait, pixel bytes, shared-memory name or path is recorded.
Save only after the original worker body and wrapper restoration have finished.
"""
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
import weakref


ROW_LIMIT = 512
HOOK_LIMIT = 128
TRACE_BYTES = 1024 * 1024
ROW_BYTES = TRACE_BYTES - 64 * 1024
SOURCE_FILES = (
    "scripts/r3_capture_credit_trace.py", "scripts/r3_measure.py",
    "scripts/r3_policy_measure.py",
    "src/emo_master/apps/runtime/presentation/collector.py",
)
_MISSING = object()
_MAX_INTEGER = (1 << 63) - 1


def fingerprints(root):
    root = Path(root)
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            if (root / name).is_file() else None for name in SOURCE_FILES}


def _clocks():
    cpu = getattr(time, "thread_time_ns", None)
    return time.perf_counter_ns(), cpu() if cpu is not None else None


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value <= _MAX_INTEGER


def _identifier(value):
    return isinstance(value, str) and 0 < len(value.encode("utf-8")) <= 160


class _Forward:
    """Keep inherited Python methods weak; native SemLock is a distinct owner."""
    def __init__(self, owner, method):
        if getattr(method, "__self__", None) is owner and hasattr(method, "__func__"):
            self.owner, self.function = weakref.ref(owner), method.__func__
            self.method = None
        else:
            self.owner = self.function = None
            self.method = method

    def __call__(self, *args, **kwargs):
        if self.method is not None:
            return self.method(*args, **kwargs)
        owner = self.owner()
        if owner is None:
            raise ReferenceError("observed method owner retired")
        return self.function(owner, *args, **kwargs)


class CaptureCreditTrace:
    def __init__(self, row_limit=ROW_LIMIT, hook_limit=HOOK_LIMIT):
        if (not _integer(row_limit, 1) or row_limit > ROW_LIMIT
                or not _integer(hook_limit, 1) or hook_limit > HOOK_LIMIT):
            raise ValueError("capture-credit trace limits exceed their budgets")
        self.row_limit, self.hook_limit = row_limit, hook_limit
        self.rows, self.hooks = [], []
        self.counters = Counter()
        self.local = threading.local()
        self.disabled = False
        self.diagnostic_errors = 0
        self.last_diagnostic_error = None
        self.observer_calls = self.observer_wall_ns = self.observer_cpu_ns = 0
        self.observer_wall_max_ns = self.observer_cpu_max_ns = 0
        self.row_bytes = self.active_scopes = 0
        self.source_before, self.source_after = {}, {}
        self.restored = self.observer_retired = False
        self.installed = False

    def _error(self, error):
        self.disabled = True
        self.diagnostic_errors += 1
        self.last_diagnostic_error = type(error).__name__[:80]

    def observe(self, operation, /, *args, default=None, **kwargs):
        """Guard observer work only; native calls must never enter this guard."""
        if self.disabled:
            return default
        start = None
        try:
            start = _clocks()
            return operation(*args, **kwargs)
        except Exception as error:
            self._error(error)
            return default
        finally:
            try:
                end = _clocks()
                self.observer_calls += 1
                if start is not None:
                    wall = end[0] - start[0]
                    self.observer_wall_ns += wall
                    self.observer_wall_max_ns = max(self.observer_wall_max_ns, wall)
                    if start[1] is not None and end[1] is not None:
                        cpu = end[1] - start[1]
                        self.observer_cpu_ns += cpu
                        self.observer_cpu_max_ns = max(self.observer_cpu_max_ns, cpu)
            except Exception as error:
                if not self.disabled:
                    self._error(error)

    def stamp(self):
        if self.disabled:
            return None
        try:
            return _clocks()
        except Exception as error:
            self._error(error)
            return None

    def _patch(self, owner, name, factory):
        # Raw provenance is retained, but never retain the Semaphore or an
        # inherited bound Python method. Native acquire owns a distinct SemLock.
        self.hooks[:] = [hook for hook in self.hooks if hook[0]() is not None]
        if any(ref() is owner and attr == name for ref, attr, *_ in self.hooks):
            return
        if len(self.hooks) >= self.hook_limit:
            self.counters["hook_overflow"] += 1
            self.disabled = True
            return
        previous = vars(owner).get(name, _MISSING)
        if previous is not _MISSING and getattr(previous, "__self__", None) is owner:
            # A user-assigned bound method cannot retain exact raw identity and
            # also avoid retaining its owner. Native SemLock does not hit this.
            self.counters["invalid_owner_bound_attribute"] += 1
            self.disabled = True
            return
        wrapper = factory(_Forward(owner, getattr(owner, name)))
        wrapper._r3_capture_original = previous
        # The registry must not prolong even the distinct native SemLock's
        # lifetime after its Semaphore and installed wrapper disappear.
        previous_ref = None if previous is _MISSING else weakref.ref(previous)
        entry = (weakref.ref(owner), name, weakref.ref(wrapper), previous_ref)
        self.hooks.append(entry)  # A partially failing setattr remains retryable.
        setattr(owner, name, wrapper)
        self.restored = self.observer_retired = False
        self.counters["hook_peak"] = max(self.counters["hook_peak"], len(self.hooks))

    def hook_collector(self, collector):
        slots = collector.config.get("slots", [])
        if not isinstance(slots, (tuple, list)) or len(slots) > self.hook_limit:
            self.counters["invalid_slots"] += 1
            self.disabled = True
            return
        trace_ref = weakref.ref(self)
        for slot in slots:
            credit = slot["free"]
            credit_ref = weakref.ref(credit)

            def factory(original, credit_ref=credit_ref):
                def acquire(*args, **kwargs):
                    trace = trace_ref()
                    if trace is None or trace.disabled or getattr(trace.local, "scope", None) is None:
                        return original(*args, **kwargs)
                    details = trace.observe(trace.attempt_metadata, credit_ref, args, kwargs)
                    start = trace.stamp()
                    outcome, accepted = "OK", None
                    try:
                        result = original(*args, **kwargs)
                        accepted = result if type(result) is bool else None
                        return result
                    except BaseException as error:
                        outcome = type(error).__name__[:80]
                        raise
                    finally:
                        end = trace.stamp()
                        trace.observe(trace.record, details, start, end, outcome, accepted)
                return acquire
            self._patch(credit, "acquire", factory)

    def enter_image(self, collector, key, value, item):
        import numpy as np
        self.hook_collector(collector)
        identity = item.get("identity", {}) if isinstance(item, dict) else {}
        names = {"runtime_id": "runtimeInstanceId", "job_id": "jobId",
                 "result_key": "resultKey"}
        metadata = {name: identity.get(source) for name, source in names.items()}
        metadata.update(source_id=key, result_ordinal=identity.get("resultOrdinal"),
                        raw_bytes=int(value.nbytes) if isinstance(value, np.ndarray) else None)
        valid = (all(_identifier(metadata[name]) for name in (*names, "source_id"))
                 and _integer(metadata["result_ordinal"], 1) and _integer(metadata["raw_bytes"], 1))
        if not valid:
            self.counters["invalid_image_identity"] += 1
            metadata = None
        previous = getattr(self.local, "scope", None)
        scope = {"collector": weakref.ref(collector), "metadata": metadata, "previous": previous}
        self.local.scope = scope
        self.active_scopes += 1
        self.counters["image_calls"] += 1
        return scope

    def leave_image(self, scope):
        # Cleanup runs even if evidence failed midway through the native image.
        try:
            if getattr(self.local, "scope", None) is not scope:
                self.counters["scope_conflict"] += 1
            self.local.scope = scope["previous"]
            self.active_scopes -= 1
        except Exception as error:
            self._error(error)

    def attempt_metadata(self, credit_ref, args, kwargs):
        scope = self.local.scope
        collector, credit = scope["collector"](), credit_ref()
        if collector is None or credit is None or scope["metadata"] is None:
            self.counters["invalid_attempt_identity"] += 1
            return None
        # Read the actual current configuration at the acquire boundary. The
        # observer never reads a semaphore/quarantine value or shared memory.
        matches = [slot for slot in collector.config.get("slots", []) if slot.get("free") is credit]
        if len(matches) != 1:
            self.counters["invalid_slot_identity"] += 1
            return None
        slot = matches[0]
        details = dict(scope["metadata"], slot=slot.get("index"), lane=slot.get("lane", 0),
                       capacity=slot.get("capacity", 8 * 1024 * 1024), offset=slot.get("offset", 0))
        if not all(_integer(details[name], 1 if name == "capacity" else 0)
                   for name in ("slot", "lane", "capacity", "offset")):
            self.counters["invalid_slot_schema"] += 1
            return None
        def timeout(value):
            return value is None or (type(value) in (float, int) and math.isfinite(value)
                                     and abs(value) <= _MAX_INTEGER)
        if (len(args) > 2 or (args and type(args[0]) is not bool)
                or (len(args) == 2 and not timeout(args[1]))
                or any(key not in ("block", "timeout") for key in kwargs)
                or ("block" in kwargs and type(kwargs["block"]) is not bool)
                or ("timeout" in kwargs and not timeout(kwargs["timeout"]))):
            self.counters["invalid_acquire_arguments"] += 1
            return None
        details.update(acquire_args=list(args), acquire_kwargs=dict(kwargs))
        return details

    def record(self, details, start, end, outcome, accepted):
        self.counters["attempts"] += 1
        category = "failed" if outcome != "OK" else "accepted" if accepted is True else "refused" if accepted is False else "invalid_return"
        self.counters[category] += 1
        if details is None or start is None or end is None:
            self.counters["unattributed_attempts"] += 1
            return
        if (not all(_integer(stamp[0]) and (stamp[1] is None or _integer(stamp[1])) for stamp in (start, end))
                or end[0] < start[0]
                or (start[1] is not None and end[1] is not None and end[1] < start[1])):
            self.counters["invalid_clock_schema"] += 1
            return
        row = dict(details, stage="producer.capture_credit_acquire", start_ns=start[0], end_ns=end[0],
                   elapsed_ms=(end[0] - start[0]) / 1e6,
                   thread_cpu_ns=end[1] - start[1] if start[1] is not None and end[1] is not None else None,
                   outcome=outcome, acquired=accepted)
        size = len(json.dumps(row, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) + 1
        if len(self.rows) >= self.row_limit or self.row_bytes + size > ROW_BYTES:
            self.counters["dropped_rows"] += 1
            return
        self.rows.append(row)
        self.row_bytes += size

    def restore(self):
        failed = []
        for ref, name, wrapper_ref, previous_ref in reversed(self.hooks):
            owner, wrapper = ref(), wrapper_ref()
            if owner is None:
                continue
            try:
                current = vars(owner).get(name, _MISSING)
                previous = _MISSING if previous_ref is None else previous_ref()
                if previous is not None and current is previous:
                    continue
                if wrapper is None or current is not wrapper or previous is None:
                    self.counters["restore_conflict"] += 1
                    failed.append((ref, name, wrapper_ref, previous_ref))
                    continue
                if previous is _MISSING:
                    delattr(owner, name)
                else:
                    setattr(owner, name, previous)
            except Exception as error:
                failed.append((ref, name, wrapper_ref, previous_ref))
                self._error(error)
        self.hooks[:] = reversed(failed)
        self.restored = not self.hooks
        self.observer_retired = self.restored and self.active_scopes == 0

    def payload(self):
        counts = self.counters
        identities = {tuple(row[name] for name in ("runtime_id", "job_id", "result_key", "result_ordinal", "source_id"))
                      for row in self.rows}
        source_complete = (set(self.source_before) == set(SOURCE_FILES)
                           and set(self.source_after) == set(SOURCE_FILES)
                           and all(self.source_before.values()) and all(self.source_after.values()))
        return {"role": "capture_credit", "schema_version": 1, "pid": os.getpid(),
                "row_limit": self.row_limit, "hook_limit": self.hook_limit, "trace_bytes_limit": TRACE_BYTES,
                "rows": list(self.rows), "counters": dict(counts), "dropped_rows": counts["dropped_rows"],
                "actual_attempts": {name: counts[name] for name in ("attempts", "accepted", "refused", "failed", "invalid_return")},
                "identity_counts": {"recorded_image_identities": len(identities),
                    "recorded_results": len({key[:4] for key in identities}),
                    "recorded_sources": len({key[4] for key in identities}),
                    "recorded_jobs": len({key[:2] for key in identities}),
                    "unattributed_attempts": counts["unattributed_attempts"]},
                "instrumentation_disabled": self.disabled, "diagnostic_errors": self.diagnostic_errors,
                "last_diagnostic_error_type": self.last_diagnostic_error,
                "restored": self.restored, "observer_retired": self.observer_retired,
                "pending_hooks": len(self.hooks), "active_image_scopes": self.active_scopes,
                "observer_cost": {"calls": self.observer_calls, "wall_ns": self.observer_wall_ns,
                    "thread_cpu_ns": self.observer_cpu_ns, "max_wall_ns": self.observer_wall_max_ns,
                    "max_thread_cpu_ns": self.observer_cpu_max_ns,
                    "interpretation": "Observer callbacks only; excludes native forwarding and clock/accounting overhead. Maxima bound observed callback samples, not total perturbation; nested callbacks are inclusive."},
                "source_before": dict(self.source_before), "source_after": dict(self.source_after),
                "source_complete": bool(source_complete),
                "source_unchanged": bool(self.source_before) and self.source_before == self.source_after,
                "clock": "Worker perf_counter_ns; thread_time_ns where supported",
                "interpretation": "acquired false means the original acquire returned False; it does not identify a prior owner. Success means only acquire returned True, not image publication or export success. Match runtime/job/result/source/slot/lane externally. Actual-attempt counters cover observed scoped calls only; disabled, invalid or dropped evidence never implies coverage. Out-of-scope cleanup acquires forward unrecorded. No performance verdict or GIL/owner inference is made; use controlled ABBA runs for perturbation.",
                "performance_verdict": "NOT_EVALUATED"}

    def save(self, destination):
        payload = self.payload()
        destination = Path(destination)
        failures = {key: value for key, value in payload["counters"].items()
                    if value and any(part in key for part in ("invalid", "unattributed", "overflow", "dropped", "conflict"))}
        complete = (self.installed and not self.disabled and not self.diagnostic_errors and not failures
                    and self.observer_retired and payload["source_complete"] and payload["source_unchanged"]
                    and sum(payload["actual_attempts"][key] for key in ("accepted", "refused", "failed")) == len(self.rows))
        summary = {"enabled": True, "file": destination.name, "complete": bool(complete),
                "rows": len(self.rows), "dropped_rows": payload["dropped_rows"],
                "actual_attempts": payload["actual_attempts"], "identity_counts": payload["identity_counts"],
                "diagnostic_errors": self.diagnostic_errors, "counter_failures": failures,
                "instrumentation_disabled": self.disabled, "restored": self.restored,
                "observer_retired": self.observer_retired, "source_complete": payload["source_complete"],
                "source_unchanged": payload["source_unchanged"], "observer_cost": payload["observer_cost"],
                "performance_verdict": "NOT_EVALUATED"}
        payload["summary"] = summary
        content = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(content) > TRACE_BYTES:
            raise RuntimeError("capture-credit trace file budget exceeded")
        destination.write_bytes(content)
        return summary


@contextmanager
def installed(trace, root):
    """Enclose base.measuredJob, including its own nested patch restoration.

    This scope owns class and instance provenance. It never closes or waits for
    runtime owners. ``restore()`` remains callable if a restoration failed.
    """
    from emo_master.apps.runtime.presentation.collector import ResultCollector
    trace_ref = weakref.ref(trace)
    trace.source_before = trace.observe(fingerprints, root, default={})

    def init_factory(original):
        def initialize(collector, *args, **kwargs):
            result = original(collector, *args, **kwargs)
            active = trace_ref()
            if active is not None:
                active.observe(active.hook_collector, collector)
            return result
        return initialize

    def image_factory(original):
        def image(collector, key, value, item):
            active = trace_ref()
            scope = active.observe(active.enter_image, collector, key, value, item) if active is not None else None
            try:
                return original(collector, key, value, item)
            finally:
                if scope is not None:
                    active.leave_image(scope)
        return image

    try:
        trace.observe(trace._patch, ResultCollector, "__init__", init_factory)
        trace.observe(trace._patch, ResultCollector, "image", image_factory)
        trace.installed = True
        yield trace
    finally:
        trace.restore()
        # Fingerprinting must still run when an earlier observer fault disabled
        # row collection. A failure remains evidence-only and does not escape.
        try:
            trace.source_after = fingerprints(root)
        except Exception as error:
            trace._error(error)
