"""Opt-in phase evidence from A18's existing nativeCounts calls only.

The wrapper returns the exact original snapshot and retains only primitives.
It adds no census, thread, sleep, GC or successful-test file I/O. The original
assertNoNewThreads predicate runs first; only its first native-identity failure
queries our own PID through the existing bounded helper. No file means no
captured failure, never an inferred acceptance result. Normal CI is unchanged.
"""
import heapq
import json
from pathlib import Path
import time

import pytest

from scripts.r3_native_thread_origins import collect, HELPER_SECONDS, LIMITATIONS, MAX_BYTES, MAX_THREADS


TARGET = ("tests/ui/presentation/test_retained_acceptance_lifecycle.py::"
          "testThousandNavigationsAndThirtyFloatingCyclesRetireNativeOwners")
MAX_CHECKPOINTS = 64
MAX_CHECKPOINT_IDS = 128
COUNT_KEYS = ("handles", "native_threads", "python_threads", "widgets", "windows")
BASELINES = ("pre_fixture_baseline", "warmed_fixture_baseline")
PHASES = (*BASELINES, "stable_initial", "navigation", "floating", "hidden_observers",
          "owners_closed", "fixture_released")
_MISSING = object()
_PENDING = pytest.StashKey()
_INVALID = pytest.StashKey()
EXPECTED_CHECKPOINTS = (*((phase, None) for phase in (*BASELINES, "stable_initial")),
    *(("navigation", index) for index in range(99, 1000, 100)),
    *(("floating", index) for index in range(30)),
    ("hidden_observers", None), ("owners_closed", None), ("fixture_released", None))
PHASE_LIMITATIONS = (
    "Checkpoint identity is unverified: numeric thread IDs can retire and be reused, "
    "including within this process. Query-time creation timestamps cannot prove "
    "checkpoint identity. Wall clocks can adjust; raw wall and monotonic readings "
    "are not corrected. First observation is only at an existing checkpoint, not "
    "thread creation time. Missing, truncated or unrecorded IDs mean unknown, not "
    "absence. Start modules are not creators, owners or causes. Recording adds "
    "bounded Python work between existing calls and is diagnostic, not acceptance."
)


def reading():
    return time.monotonic_ns(), time.time_ns()


class PhaseOwner:
    def __init__(self, module, output):
        self.module, self.output = module, output
        self.patches = []
        self.rows = []
        self.baselines = {}
        self.latest = None
        self.calls = 0
        self.dropped = 0
        self.errors = []
        self.captured = False
        self.record = None
        self.active = True
        self.retryAttempted = False

    def invalidate(self, reason):
        if reason not in self.errors and len(self.errors) < 16:
            self.errors.append(reason)

    def patch(self, name, replacement):
        # Record raw provenance before a setter that could mutate then raise.
        raw = vars(self.module).get(name, _MISSING)
        self.patches.append((name, raw, replacement))
        setattr(self.module, name, replacement)

    def sample(self, snapshot, phase, index, entered, exited):
        sequence = self.calls
        self.calls += 1
        self.latest = None
        if sequence >= len(EXPECTED_CHECKPOINTS) or (phase, index) != EXPECTED_CHECKPOINTS[sequence]:
            self.invalidate("checkpoint_sequence_unknown")
        if len(self.rows) >= MAX_CHECKPOINTS:
            self.dropped += 1
            self.invalidate("checkpoint_limit")
            return
        row = {"sample": sequence, "phase": phase if type(phase) is str and phase in PHASES else None,
               "index": index if type(index) is int else None,
               "entry_monotonic_ns": entered[0], "entry_unix_ns": entered[1],
               "exit_monotonic_ns": exited[0], "exit_unix_ns": exited[1],
               "counts": {key: snapshot[key] if type(snapshot.get(key)) is int else None
                          for key in COUNT_KEYS},
               "native_thread_ids": [], "native_ids_status": "unknown",
               "omitted_native_thread_count": None}
        ids = snapshot.get("native_thread_ids")
        if type(ids) in (set, frozenset) and all(type(tid) is int and tid > 0 for tid in ids):
            row["native_thread_ids"] = heapq.nsmallest(MAX_CHECKPOINT_IDS, ids)
            row["omitted_native_thread_count"] = max(0, len(ids) - MAX_CHECKPOINT_IDS)
            row["native_ids_status"] = "truncated" if len(ids) > MAX_CHECKPOINT_IDS else "complete"
        self.rows.append(row)
        # IDs refer only to snapshot dicts, never Python/Qt/thread lifetimes.
        # Prior temporary snapshots are not kept in an ID registry: their
        # dictionary IDs may be reused. Baselines stay live in the target.
        self.latest = (id(snapshot), sequence)
        if phase in BASELINES:
            if phase in self.baselines:
                self.invalidate("duplicate_baseline")
            else:
                self.baselines[phase] = (id(snapshot), sequence)

    def match(self, snapshot, *, baseline):
        candidates = self.baselines.values() if baseline else (self.latest,)
        matched = [value[1] for value in candidates if value is not None and value[0] == id(snapshot)]
        return matched[0] if len(matched) == 1 else None

    def capture(self, before, after):
        # This is called only after the original native-identity predicate fails.
        newIds = sorted(after["native_thread_ids"] - before["native_thread_ids"])
        if not newIds or self.captured:
            return
        self.captured = True
        failedAt = reading()
        beforeSample = self.match(before, baseline=True)
        afterSample = self.match(after, baseline=False)
        observations = []
        for tid in newIds[:MAX_THREADS]:
            first = next((row for row in self.rows if tid in row["native_thread_ids"]), None)
            precedingComplete = first is not None and first["sample"] > 0 and all(
                row["native_ids_status"] == "complete" for row in self.rows[:first["sample"]])
            observations.append({"native_thread_id": tid,
                "first_observed_sample": first["sample"] if first is not None else None,
                "earlier_checkpoint_absence": "observed" if precedingComplete else "unknown",
                "checkpoint_identity": "unverified"})
        self.record = {"schema_version": 2, "role": "diagnostic_only", "test": TARGET,
            "trigger": "original_assertNoNewThreads_failed", "failure_kind": "native_thread_ids",
            "failure_monotonic_ns": failedAt[0], "failure_unix_ns": failedAt[1],
            "before_sample": beforeSample, "after_sample": afterSample,
            "snapshot_match": "live_argument_dict_id" if beforeSample is not None and afterSample is not None else "unknown",
            "checkpoint_identity": "unverified", "new_native_thread_count": len(newIds),
            "selected_native_thread_ids": newIds[:MAX_THREADS],
            "omitted_native_thread_count": max(0, len(newIds) - MAX_THREADS),
            "checkpoint_limit": MAX_CHECKPOINTS, "checkpoint_native_id_limit": MAX_CHECKPOINT_IDS,
            "checkpoints": list(self.rows), "checkpoint_calls": self.calls,
            "omitted_checkpoint_count": self.dropped, "first_observations": observations,
            "helper_timeout_seconds": HELPER_SECONDS,
            "limitations": LIMITATIONS + " " + PHASE_LIMITATIONS,
            "evidence": collect(newIds), "observer_retired": False,
            "output_truncated": False}

    def install(self):
        originalCounts = self.module.nativeCounts
        originalAssert = self.module.assertNoNewThreads

        def counts(app, *, phase=None, index=None):
            entered = None
            if self.active:
                try:
                    entered = reading()
                except BaseException:
                    self.invalidate("entry_clock_unavailable")
            # Exactly one original call. Its return and exception are untouched.
            result = originalCounts(app, phase=phase, index=index)
            if self.active:
                try:
                    exited = reading()
                    self.sample(result, phase, index, entered or (None, None), exited)
                except BaseException:
                    self.latest = None
                    self.invalidate("checkpoint_unavailable")
            return result

        def check(before, after):
            try:
                return originalAssert(before, after)
            except AssertionError:
                try:
                    # The original function checks native ID inclusion first.
                    # A nonempty difference after it raises identifies that
                    # predicate without parsing pytest-rewritten exception text.
                    if self.active:
                        self.capture(before, after)
                except BaseException:
                    self.invalidate("failure_evidence_unavailable")
                raise
            finally:
                self.latest = None

        self.patch("nativeCounts", counts)
        self.patch("assertNoNewThreads", check)

    def retire(self):
        self.active = False
        remaining = []
        for name, raw, replacement in reversed(self.patches):
            current = vars(self.module).get(name, _MISSING)
            if current is replacement:
                try:
                    if raw is _MISSING:
                        delattr(self.module, name)
                    else:
                        setattr(self.module, name, raw)
                except BaseException:
                    self.invalidate("patch_restoration_failed")
                current = vars(self.module).get(name, _MISSING)
            if current is not raw:
                # Retain ownership for one retry; never claim raw restoration.
                remaining.append((name, raw, replacement))
                self.invalidate("patch_provenance_changed")
        self.patches = list(reversed(remaining))
        self.baselines.clear()
        self.latest = None
        return not self.patches

    def save(self):
        if self.record is None:
            return
        record = self.record
        record["observer_retired"] = not self.patches
        record["errors"] = list(self.errors)
        content = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
        if len(content.encode("ascii")) > MAX_BYTES:
            record["output_truncated"] = True
            # Keep every bounded row, especially both baselines. Make missing
            # identities explicit instead of dropping earlier samples silently.
            for row in record["checkpoints"]:
                count = row["omitted_native_thread_count"]
                row["omitted_native_thread_count"] = (count + len(row["native_thread_ids"]) if count is not None else None)
                row["native_thread_ids"] = []
                row["native_ids_status"] = "unknown_output_byte_limit"
            content = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
        if len(content.encode("ascii")) > MAX_BYTES:
            record["evidence"] = {"status": "unknown_output_byte_limit", "threads": []}
            content = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
        if len(content.encode("ascii")) > MAX_BYTES:
            self.invalidate("output_byte_limit")
            return
        with Path(self.output).open("x", encoding="ascii") as stream:
            stream.write(content)


def pytest_addoption(parser):
    parser.addoption("--native-thread-origins", metavar="NEW.json",
                     help="opt-in existing A18 checkpoints and post-failure Windows start modules")


def pytest_collection_modifyitems(config, items):
    output = config.getoption("native_thread_origins")
    if not output:
        return
    if sum(item.nodeid == TARGET for item in items) != 1:
        raise pytest.UsageError("Native thread diagnostics require exactly one selected A18 test")
    if Path(output).exists() or not Path(output).parent.is_dir():
        raise pytest.UsageError("Native thread diagnostic output must be new, in an existing directory")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    output = item.config.getoption("native_thread_origins")
    if not output or item.nodeid != TARGET:
        return (yield)
    owner = PhaseOwner(item.module, output)
    try:
        try:
            owner.install()
        except BaseException:
            owner.invalidate("patch_installation_failed")
            try:
                owner.retire()
            except BaseException:
                owner.active = False
                owner.invalidate("observer_retirement_failed")
        return (yield)
    finally:
        try:
            retired = owner.retire()
        except BaseException:
            retired = False
            owner.invalidate("observer_retirement_failed")
        if not retired:
            item.config.stash[_PENDING] = owner
        try:
            owner.save()
        except BaseException:
            # Observation never replaces the exact original test exception.
            owner.invalidate("output_unavailable")
        if owner.errors:
            item.config.stash[_INVALID] = list(owner.errors)


def pytest_sessionfinish(session):
    pending = session.config.stash.get(_PENDING, None)
    if pending is not None and not pending.retryAttempted:
        pending.retryAttempted = True
        try:
            retired = pending.retire()
        except BaseException:
            retired = False
        if retired:
            del session.config.stash[_PENDING]
    errors = session.config.stash.get(_INVALID, [])
    if pending is None and not errors:
        return
    print("A18_DIAGNOSTIC_INVALID: observer errors or unfinished restoration; "
          "no failure file must not be interpreted as a clean observation")
    # A retirement failure remains diagnostically invalid even after retry.
    # The already-written evidence never claims later cleanup was successful.
    if session.exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
