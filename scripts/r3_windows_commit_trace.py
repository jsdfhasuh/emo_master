"""One-attempt DIAGNOSTIC ONLY. Never import this as a production component.

Raw paths, QPCs and process/thread/file identities are runner-private. Only
public_summary(), built from constants and numeric aggregates, may reach logs.
"""
from __future__ import annotations

import ctypes
import json
import ntpath
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import weakref

import pytest

BASE = "655eeac84d0602aa670c76af67679509782bbd7f"
NONCE = "diagnostic: one Windows kernel trace 655eeac f2b07937"
TARGETS = (
    "tests/runtime/presentation/test_image_demand_client.py::testZeroDemandKeepsFormalResultsAndResumesFinalCapturedResult[presentation]",
    "tests/runtime/presentation/test_multi_capture.py::testLegacyReaderIgnoringExpiryUsesResetToFenceDelayedDecode",
)
CASE_IDS = ("zero_demand_presentation", "legacy_reader_reset")
STATES = ("running", "ready", "blocked", "unknown")
MAX_CALLS = 512
MAX_STORES = 32
MAX_PRIVATE = 4 * 1024 * 1024
MAX_RECORDS = 8_000_000
MAX_DECODED = 768 * 1024 * 1024
MAX_ETL = 1024 * 1024 * 1024
MAX_DIRECTORY = 2 * 1024 * 1024 * 1024
MAX_SUMMARY = 256 * 1024
# Reserve stop + cancel time inside the approved 30-minute collection budget.
STOP_AFTER = 27 * 60
STOP_SECONDS = 90
CANCEL_SECONDS = 15
PARSE_SECONDS = 120
RUN_SECONDS = 30 * 60
OTHER_PLUGINS = (
    "scripts.r3_sqlite_failure_diagnostics", "scripts.r3_sqlite_stop_diagnostics",
    "scripts.r3_sqlite_capture_diagnostics", "scripts.r3_a18_thread_diagnostics",
)
_RUN = None


class Invalid(Exception):
    """Constant reason only; never expose caught exception text."""


def qpc():
    value = ctypes.c_longlong()
    if not ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(value)):
        raise Invalid("clock_unavailable")
    return value.value


def frequency():
    value = ctypes.c_longlong()
    if not ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(value)) or value.value <= 0:
        raise Invalid("clock_unavailable")
    return value.value


def private_write(path, value):
    content = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    if len(content.encode()) > MAX_PRIVATE:
        raise Invalid("observer_quota")
    temporary = path.with_suffix(".pending")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(content)
    temporary.replace(path)


class Phases:
    """Use the existing native-connection adapter, with just one timing layer.

    The adapter calls connectionCall once per native operation. Non-boundary
    operations are forwarded without a timing probe. No SQL or autocommit reads.
    """
    details = True

    def __init__(self, owner, store):
        self.owner, self.store = owner, store
        self.serial = self.call_id = 0

    def connectionToken(self):
        with self.owner.lock:
            self.serial += 1
            return self.serial

    def _disable(self, _error):
        self.owner.invalid = True

    def connectionCall(self, phase, operation, connection, context, *args, **kwargs):
        row = None
        ident = None
        try:
            with self.owner.lock:
                self.call_id += 1
                ident = self.call_id
                context["call_id"] = ident
                selected = self.owner.active and phase in ("commit", "context_exit")
                if selected and len(self.owner.calls) >= MAX_CALLS:
                    self.owner.omitted += 1
                    selected = False
                if selected:
                    row = {"store": self.store, "tid": threading.get_native_id(),
                           "phase": phase, "begin": qpc(), "end": None, "failed": False}
                    self.owner.calls.append(row)
        except BaseException:
            self.owner.invalid = True
        try:
            return operation(*args, **kwargs), ident
        except BaseException:
            if row is not None:
                row["failed"] = True
            raise
        finally:
            if row is not None:
                try:
                    row["end"] = qpc()
                except BaseException:
                    self.owner.invalid = True

    def call(self, _phase, operation, *args, **kwargs):
        return operation(*args, **kwargs)


class Owner:
    def __init__(self, case, store_type):
        self.case, self.store_type = case, store_type
        self.original = vars(store_type)["_connect"]
        from types import FunctionType
        if (type(self.original) is not FunctionType or self.original.__code__.co_name != "_connect"
                or Path(self.original.__code__.co_filename).resolve() != Path(__file__).resolve().parents[1] / "src/emo_master/apps/runtime/context/sqlite_store.py"):
            raise Invalid("observer_incomplete")
        self.lock = threading.Lock()
        self.stores = weakref.WeakKeyDictionary()
        self.paths = []
        self.calls = []
        self.active = True
        self.invalid = False
        self.omitted = 0
        self.begin = qpc()
        self.end = None
        self.outcomes = {key: "pending" for key in ("setup", "call", "teardown")}

    def install(self):
        from scripts.r3_sqlite_phases import _Connection
        owner = self

        def connect(store, *args, **kwargs):
            # Invoke original exactly once even if diagnostic bookkeeping fails.
            native = owner.original(store, *args, **kwargs)
            if not owner.active or type(store) is not owner.store_type:
                return native
            try:
                with owner.lock:
                    phases = owner.stores.get(store)
                    if phases is None:
                        if len(owner.paths) >= MAX_STORES:
                            owner.invalid = True
                            return native
                        path = os.fspath(store.dbPath)
                        if not isinstance(path, str) or len(path) > 4096 or not ntpath.isabs(path):
                            owner.invalid = True
                            return native
                        token = len(owner.paths)
                        owner.paths.append(path)
                        phases = Phases(owner, token)
                        owner.stores[store] = phases
                import sqlite3
                if type(native) is not sqlite3.Connection:
                    owner.invalid = True
                    return native
                return _Connection(native, phases)
            except BaseException:
                owner.invalid = True
                return native
        self.replacement = connect
        self.store_type._connect = connect

    def retire(self):
        self.active = False
        if vars(self.store_type).get("_connect") is self.replacement:
            self.store_type._connect = self.original
        else:
            self.invalid = True
        try:
            self.end = qpc()
        except BaseException:
            self.invalid = True
        self.stores.clear()

    def record(self):
        with self.lock:
            return {"case": self.case, "begin": self.begin, "end": self.end,
                    "paths": list(self.paths), "calls": [dict(x) for x in self.calls],
                    "invalid": self.invalid, "omitted": self.omitted,
                    "retired": not self.active, "outcomes": dict(self.outcomes)}


def pytest_addoption(parser):
    parser.addoption("--windows-commit-observer", default=None)


def pytest_configure(config):
    global _RUN
    output = config.getoption("windows_commit_observer")
    if not output:
        return
    import pytest
    if os.name != "nt" or _RUN is not None or any(config.pluginmanager.hasplugin(p) for p in OTHER_PLUGINS) or any(getattr(p, "__name__", None) in OTHER_PLUGINS for p in config.pluginmanager.get_plugins()):
        raise pytest.UsageError("kernel trace observer unsupported or overlapping")
    path = Path(output)
    if path.exists() or not path.parent.is_dir():
        raise pytest.UsageError("kernel trace output must be new")
    _RUN = {"path": path, "pid": os.getpid(), "frequency": frequency(), "begin": qpc(),
            "owners": [], "active": None, "collected": [], "exit": None, "finished": False, "invalid": False}
    _save_run()


def _save_run():
    run = _RUN
    private_write(run["path"], {key: run[key] for key in
        ("pid", "frequency", "begin", "collected", "exit", "finished", "invalid")} |
        {"cases": [owner.record() for owner in run["owners"]]})


def pytest_collection_modifyitems(config, items):
    if _RUN is not None:
        _RUN["collected"] = [TARGETS.index(item.nodeid) for item in items if item.nodeid in TARGETS]
        if _RUN["collected"] != [0, 1]:
            import pytest
            raise pytest.UsageError("original target collection changed")
        _save_run()


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    if _RUN is None or item.nodeid not in TARGETS:
        return (yield)
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    try:
        owner = Owner(TARGETS.index(item.nodeid), SqliteStore)
        _RUN["owners"].append(owner)
        _RUN["active"] = owner
        owner.install()
    except BaseException:
        _RUN["invalid"] = True
        if "owner" in locals():
            owner.invalid = True
            try:
                owner.retire()
            except BaseException:
                pass
        _RUN["active"] = None
        return (yield)
    try:
        return (yield)
    finally:
        try:
            owner.retire()
        except BaseException:
            owner.invalid = True
        _RUN["active"] = None
        try:
            _save_run()
            if [o.case for o in _RUN["owners"]] == [0, 1] and all(not o.active for o in _RUN["owners"]):
                (_RUN["path"].parent / "targets-complete.private").write_bytes(b"1")
        except BaseException:
            owner.invalid = True


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if _RUN is not None and _RUN["active"] is not None and item.nodeid in TARGETS:
        owner = _RUN["active"]
        owner.outcomes[report.when] = report.outcome
        if report.failed:
            # Exclusive one-byte signal only. Never replace original failure.
            try:
                signal = _RUN["path"].parent / "target.failed"
                if not signal.exists():
                    signal.write_bytes(b"1")
                _save_run()
            except BaseException:
                owner.invalid = True
    return report


def pytest_sessionfinish(session, exitstatus):
    if _RUN is not None:
        _RUN["exit"] = int(exitstatus)
        _RUN["finished"] = True
        try:
            _save_run()
        except BaseException:
            pass
        # No changes to session.exitstatus, ever.


def events(path):
    if path.stat().st_size > MAX_DECODED:
        raise Invalid("decoded_quota")
    with path.open(encoding="ascii") as stream:
        for index, line in enumerate(stream):
            if index >= MAX_RECORDS or len(line) > 32768:
                raise Invalid("decoded_quota")
            yield json.loads(line)


def normalized_path(path, volumes):
    # Exact paths only; no basename or suffix matching. No filesystem reads.
    value = ntpath.normcase(ntpath.normpath(path))
    if value.startswith("\\??\\"):
        value = value[4:]
    drive, tail = ntpath.splitdrive(value)
    if drive:
        target = volumes.get(drive)
        if target is None:
            raise Invalid("volume_mapping_missing")
        value = target.lower() + tail
    if not value.startswith("\\device\\"):
        raise Invalid("unsupported_path")
    return value


def intersect(a, b, c, d):
    return max(0, min(b, d) - max(a, c))


def grouped_events(records):
    stamp = None
    group = []
    for event in records:
        current = event.get("qpc")
        if current is None:
            if group:
                yield group
                group = []
            yield [event]
            continue
        if stamp is not None and current < stamp:
            raise Invalid("timestamp_regression")
        if group and current != stamp:
            yield group
            group = []
        stamp = current
        group.append(event)
        if len(group) > 65536:
            raise Invalid("identity_quota")
    if group:
        yield group


def reduce_trace(records, observer, control):
    records = iter(records)
    meta = next(records)
    if meta.get("kind") != "meta" or meta.get("clock") != 1 or meta.get("frequency") != observer["frequency"]:
        raise Invalid("clock_mismatch")
    if meta.get("events_lost") or meta.get("buffers_lost") or not meta.get("finalized"):
        raise Invalid("event_loss_or_unfinalized")
    freq, pid = meta["frequency"], observer["pid"]
    stop = control["stop_request"]
    watched = {row["tid"] for case in observer["cases"] for row in case["calls"] if row["begin"] < stop}
    expected_paths = {}
    for case in observer["cases"]:
        for store, path in enumerate(case["paths"]):
            for role, suffix in (("db", ""), ("wal", "-wal")):
                key = normalized_path(path + suffix, control["volumes"])
                value = (case["case"], store, role)
                if key in expected_paths and expected_paths[key] != value:
                    raise Invalid("ambiguous_identity")
                expected_paths[key] = value
    process = None
    lifetimes, cpus, first_cpu, running = {}, {}, {}, {}
    changes = {tid: [] for tid in watched}
    files, names, irps = {}, {}, {}
    flushes = []
    counts = {k: 0 for k in ("process_start", "thread_start", "switch", "ready", "file_create", "file_flush", "file_end", "disk_flush")}
    unmatched = 0
    done = None
    epoch = 0

    def transition(tid, stamp, state):
        if tid not in watched:
            return
        if tid not in lifetimes or (lifetimes[tid][2] is not None and stamp > lifetimes[tid][2]):
            raise Invalid("thread_lifetime_missing")
        changes[tid].append((stamp, state))
        if len(changes[tid]) > 100000:
            raise Invalid("state_quota")

    def claim(irp, value):
        if irp in irps and (irps[irp] is not None or value is not None):
            raise Invalid("ambiguous_identity")
        irps[irp] = value

    def role_of(path):
        try:
            return expected_paths.get(normalized_path(path, control["volumes"]))
        except Invalid:
            return None

    for group in grouped_events(records):
        switches, readies = [], []
        for event in group:
            kind = event.get("kind")
            if kind == "end":
                if done is not None:
                    raise Invalid("decode_incomplete")
                done = event
                continue
            if done is not None:
                raise Invalid("records_after_end")
            stamp = event["qpc"]
            if stamp > stop:
                continue  # Stop/rundown is never used as scheduling coverage.
            if kind in counts:
                counts[kind] += 1
            if kind == "process_start" and event["pid"] == pid:
                if process is not None:
                    raise Invalid("pid_reuse")
                if event["rundown"] or stamp > observer["begin"] or stamp < control["begin"]:
                    raise Invalid("process_lifetime_missing")
                process = stamp
            elif kind == "process_end" and event["pid"] == pid:
                if stamp < min(stop, max((c["end"] or c["begin"]) for c in observer["cases"])):
                    raise Invalid("process_lifetime_ended")
            elif kind == "thread_start" and event["tid"] in watched:
                tid = event["tid"]
                if tid in lifetimes or event["rundown"] or event["pid"] != pid:
                    raise Invalid("thread_reuse_or_missing_start")
                lifetimes[tid] = (event["pid"], stamp, None)
                changes[tid].append((stamp, "unknown"))
            elif kind == "thread_end" and event["tid"] in watched:
                tid = event["tid"]
                if tid not in lifetimes:
                    raise Invalid("thread_lifetime_missing")
                owner, born, died = lifetimes[tid]
                if died is not None:
                    raise Invalid("thread_reuse_or_missing_start")
                lifetimes[tid] = (owner, born, stamp)
                changes[tid].append((stamp, "unknown"))
            elif kind == "switch":
                switches.append(event)
            elif kind == "ready":
                readies.append(event)
            elif kind == "file_name":
                key = event["object"]
                if event["remove"]:
                    names.pop(key, None)
                else:
                    role = role_of(event["name"])
                    if key in names and names[key] != role and (names[key] is not None or role is not None):
                        raise Invalid("ambiguous_identity")
                    names[key] = role  # Presence and known unrelated remain distinct.
            elif kind == "file_create":
                obj, role = event["object"], role_of(event["name"])
                if obj in files and (files[obj][1] is not None or role is not None):
                    raise Invalid("ambiguous_identity")
                epoch += 1
                files[obj] = (epoch, role, stamp)
                claim(event["irp"], None)
            elif kind == "file_close":
                # A pending matched flush cannot outlive the object's lifetime.
                if any(x is not None and x[2] == event["object"] for x in irps.values()):
                    raise Invalid("ambiguous_identity")
                files.pop(event["object"], None)
                claim(event["irp"], None)
            elif kind in ("file_flush", "file_other"):
                match = files.get(event["object"])
                role = match[1] if match else None
                if role is not None and event["key"] in names and names[event["key"]] != role:
                    raise Invalid("ambiguous_identity")
                if role is not None and event.get("opcode") in (70, 71):
                    raise Invalid("ambiguous_identity")  # Delete/rename can change the path.
                if kind == "file_flush" and event["tid"] in watched:
                    if role is None:
                        unmatched += 1
                    if event["tid"] not in lifetimes or lifetimes[event["tid"]][0] != pid:
                        raise Invalid("thread_lifetime_missing")
                value = (stamp, event["tid"], event["object"], match[0], role) if kind == "file_flush" and role is not None and event["tid"] in watched else None
                claim(event["irp"], value)
            elif kind == "file_end":
                started = irps.pop(event["irp"], None)
                if started is not None:
                    begin, tid, obj, generation, role = started
                    current = files.get(obj)
                    if not current or current[0] != generation or stamp < begin:
                        raise Invalid("ambiguous_identity")
                    flushes.append((begin, stamp, tid, role, event["status"]))
                    if len(flushes) > 32768:
                        raise Invalid("flush_quota")
            if max(len(files), len(names), len(irps)) > 262144:
                raise Invalid("identity_quota")
        # ETW orders same-clock events across CPUs arbitrarily. Remove all old
        # runners before adding new runners, so valid migrations at equal QPC
        # do not depend on merged-record order.
        seen_cpu = set()
        for event in switches:
            cpu, old, stamp = event["cpu"], event["old_tid"], event["qpc"]
            if cpu in seen_cpu or (cpu in cpus and cpus[cpu] != old):
                raise Invalid("scheduler_incoherent")
            seen_cpu.add(cpu)
            first_cpu.setdefault(cpu, stamp)
            if old and old in running and running[old] != cpu:
                raise Invalid("scheduler_incoherent")
            running.pop(old, None)
            state = "ready" if event["old_state"] in (1, 3, 7) else "blocked" if event["old_state"] == 5 else "unknown"
            transition(old, stamp, state)
        for event in readies:
            tid = event["tid"]  # Payload target; header PID/TID intentionally ignored.
            if tid in watched and tid in running:
                raise Invalid("scheduler_incoherent")
            transition(tid, event["qpc"], "ready")
        for event in switches:
            cpu, new = event["cpu"], event["new_tid"]
            if new and new in running:
                raise Invalid("scheduler_incoherent")
            cpus[cpu] = new
            if new:
                running[new] = cpu
            transition(new, event["qpc"], "running")
    if done is None or done.get("schema_errors") or done.get("lost") or done.get("process_status") != 0:
        raise Invalid("decode_incomplete")
    if process is None:
        raise Invalid("process_lifetime_missing")
    if set(first_cpu) != set(range(meta["processors"])) or any(t > process for t in first_cpu.values()):
        raise Invalid("circular_prefix_unproven")
    if any(not counts[k] for k in ("thread_start", "switch", "ready", "file_create", "file_flush", "file_end", "disk_flush")):
        raise Invalid("actual_event_coverage_missing")
    if unmatched:
        raise Invalid("unmatched_target_flush")
    result = []
    for case in observer["cases"]:
        if case["begin"] >= stop:
            result.append({"case": CASE_IDS[case["case"]], "outcomes": case["outcomes"], "capture_status": "outside_capture", "calls": []})
            continue
        if case["invalid"] or case["omitted"] or not case["retired"]:
            raise Invalid("observer_incomplete")
        calls = []
        for row in case["calls"]:
            begin, actual_end, tid = row["begin"], row["end"], row["tid"]
            if begin >= stop:
                continue
            if actual_end is not None and actual_end < begin:
                raise Invalid("call_interval_uncovered")
            end = stop if actual_end is None or actual_end > stop else actual_end
            # Explicitly truncated observation, never an invented native return.
            returned = actual_end is not None and actual_end <= stop
            owner, born, died = lifetimes.get(tid, (None, 0, None))
            if owner != pid or born > begin or (died is not None and died < end):
                raise Invalid("thread_lifetime_mismatch")
            state, cursor = "unknown", begin
            durations = dict.fromkeys(STATES, 0)
            for stamp, next_state in changes[tid]:
                if stamp <= begin:
                    state = next_state
                    continue
                if stamp >= end:
                    break
                durations[state] += stamp - cursor
                cursor, state = stamp, next_state
            durations[state] += end - cursor
            overlays = []
            for a, b, thread, role, status in flushes:
                if thread == tid and role[:2] == (case["case"], row["store"]) and intersect(a, b, begin, end):
                    overlays.append({"file": role[2], "begin_us": (a-case["begin"])*1_000_000//freq,
                        "end_us": (b-case["begin"])*1_000_000//freq, "status": "success" if status == 0 else "failure"})
            calls.append({"phase": row["phase"], "begin_us": (begin-case["begin"])*1_000_000//freq,
                "end_us": (end-case["begin"])*1_000_000//freq, "returned_in_capture": returned,
                "state_us": {k: v*1_000_000//freq for k,v in durations.items()}, "flushes": overlays,
                "failed": row["failed"] if returned else False})
        result.append({"case": CASE_IDS[case["case"]], "outcomes": case["outcomes"], "capture_status": "observed" if calls else "no_calls", "calls": calls})
    if not any(case["calls"] for case in result):
        raise Invalid("actual_event_coverage_missing")
    return {"cases": result, "coverage": counts, "unmatched_flushes": unmatched,
            "pending_matched_flushes": sum(value is not None for value in irps.values())}


REASONS = frozenset((
    "ok", "preflight_failed", "source_changed", "wrong_environment", "capability_missing",
    "clock_unavailable", "clock_mismatch", "event_loss_or_unfinalized", "observer_quota",
    "observer_incomplete", "observer_missing", "decoded_quota", "timestamp_regression",
    "records_after_end", "process_lifetime_missing", "process_lifetime_ended", "pid_reuse",
    "thread_reuse_or_missing_start", "thread_lifetime_missing", "thread_lifetime_mismatch",
    "state_quota", "flush_quota", "identity_quota", "ambiguous_identity", "decode_incomplete",
    "actual_event_coverage_missing", "call_interval_uncovered", "volume_mapping_missing",
    "unsupported_path", "stop_failed", "cancel_failed", "parse_failed", "storage_guard",
    "suite_incomplete", "unexpected_failure", "summary_invalid", "cleanup_unconfirmed",
    "scheduler_incoherent", "circular_prefix_unproven", "unmatched_target_flush",
))


def public_summary(result, original, complete, reason, stopped, clean):
    """Rebuild every public level; reject keys, types and values outside schema."""
    if reason not in REASONS or type(complete) is not bool or type(clean) is not bool:
        raise Invalid("summary_invalid")
    if original is not None and (type(original) is not int or not -2**31 <= original <= 2**32):
        raise Invalid("summary_invalid")
    if stopped not in ("not_started", "target_report", "targets_complete", "suite_complete", "deadline", "storage_guard", "error"):
        raise Invalid("summary_invalid")
    out = {"role": "diagnostic_only", "validity": "VALID" if reason == "ok" and clean else "INVALID",
           "reason": reason, "original_pytest_exit": original, "suite_complete": complete,
           "stop_reason": stopped, "cleanup_confirmed": clean, "cases": [], "coverage": {},
           "loss_status": "validated_zero" if reason == "ok" else "not_validated",
           "interpretation": "scheduled_running_includes_unaccounted_ISR_DPC;flush_overlays_are_not_additive_or_durability_proof;no_specific_CPU_fsync_or_lock_cause_inference"}
    if result is not None:
        def number(value):
            if type(value) is not int or not 0 <= value <= 2**63 - 1:
                raise Invalid("summary_invalid")
            return value
        expected_coverage = {"process_start", "thread_start", "switch", "ready", "file_create", "file_flush", "file_end", "disk_flush"}
        if set(result) != {"cases", "coverage", "unmatched_flushes", "pending_matched_flushes"} or set(result["coverage"]) != expected_coverage:
            raise Invalid("summary_invalid")
        out["coverage"] = {key: number(result["coverage"][key]) for key in sorted(expected_coverage)}
        out["unmatched_flushes"] = number(result["unmatched_flushes"])
        out["pending_matched_flushes"] = number(result["pending_matched_flushes"])
        if len(result["cases"]) > 2:
            raise Invalid("summary_invalid")
        for case in result["cases"]:
            if set(case) != {"case", "outcomes", "capture_status", "calls"} or case["case"] not in CASE_IDS or case["capture_status"] not in ("observed", "outside_capture", "no_calls") or len(case["calls"]) > MAX_CALLS:
                raise Invalid("summary_invalid")
            if set(case["outcomes"]) != {"setup", "call", "teardown"} or any(v not in ("passed", "failed", "skipped", "pending") for v in case["outcomes"].values()):
                raise Invalid("summary_invalid")
            target = {"case": case["case"], "outcomes": dict(case["outcomes"]), "capture_status": case["capture_status"], "calls": []}
            for call in case["calls"]:
                if set(call) != {"phase", "begin_us", "end_us", "state_us", "flushes", "failed", "returned_in_capture"} or call["phase"] not in ("commit", "context_exit") or type(call["failed"]) is not bool or type(call["returned_in_capture"]) is not bool:
                    raise Invalid("summary_invalid")
                if set(call["state_us"]) != set(STATES) or len(call["flushes"]) > 32768:
                    raise Invalid("summary_invalid")
                row = {"phase": call["phase"], "begin_us": number(call["begin_us"]), "end_us": number(call["end_us"]),
                       "state_us": {k: number(call["state_us"][k]) for k in STATES}, "failed": call["failed"], "returned_in_capture": call["returned_in_capture"], "flushes": []}
                for flush in call["flushes"]:
                    if set(flush) != {"file", "begin_us", "end_us", "status"} or flush["file"] not in ("db", "wal") or flush["status"] not in ("success", "failure"):
                        raise Invalid("summary_invalid")
                    row["flushes"].append({"file": flush["file"], "begin_us": number(flush["begin_us"]),
                                           "end_us": number(flush["end_us"]), "status": flush["status"]})
                target["calls"].append(row)
            out["cases"].append(target)
    data = json.dumps(out, ensure_ascii=True, separators=(",", ":"))
    if len(data.encode()) > MAX_SUMMARY:
        raise Invalid("summary_invalid")
    return data


def checked_private(command, root, timeout=15):
    # Never capture unbounded native output in memory, or stream it to Actions.
    with (root / "commands.private").open("ab") as stream:
        proc = subprocess.Popen(command, stdout=stream, stderr=stream)
        limit = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if time.monotonic() >= limit or not storage_ok(root):
                    proc.kill()
                    proc.wait(timeout=5)
                    raise Invalid("storage_guard" if not storage_ok(root) else "preflight_failed")
                time.sleep(.1)
            return proc.returncode
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


def storage_ok(root):
    import shutil
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            size = path.stat().st_size
            total += size
            if path.suffix == ".etl" and size > MAX_ETL:
                return False
            if path.name == "events.private" and size > MAX_DECODED:
                return False
            if path.name in ("commands.private", "pytest.private") and size > 32 * 1024 * 1024:
                return False
    return total <= MAX_DIRECTORY and shutil.disk_usage(root).free >= 2 * 1024 * 1024 * 1024


def volume_map():
    kernel = ctypes.windll.kernel32
    kernel.QueryDosDeviceW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
    kernel.QueryDosDeviceW.restype = ctypes.c_uint32
    result = {}
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        buf = ctypes.create_unicode_buffer(32768)
        if kernel.QueryDosDeviceW(letter + ":", buf, len(buf)):
            # Multiple targets/aliases are unsupported; never guess a device.
            parts = buf[:].split("\0")
            targets = [p for p in parts if p]
            if len(targets) == 1:
                result[(letter + ":").lower()] = targets[0]
    return result


def source_preflight(repo, root):
    allowed = {
        ".github/workflows/runtime-kernel-trace-diagnostics.yml",
        "docs/testing/2026-10-02-windows-commit-trace-design.md",
        "scripts/windows_commit_trace.wprp", "scripts/windows_commit_trace_reader.cpp",
        "scripts/r3_windows_commit_trace.py", "scripts/diagnostics/windows_commit_trace_fixtures.py",
    }
    def git(*args):
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=15)
        if proc.returncode or len(proc.stdout) > MAX_PRIVATE or len(proc.stderr) > MAX_PRIVATE:
            raise Invalid("source_changed")
        return proc.stdout.decode("utf-8").strip()
    if git("rev-parse", "HEAD^") != BASE or git("log", "-1", "--format=%B") != NONCE:
        raise Invalid("source_changed")
    changed = set(git("diff", "--name-only", BASE, "HEAD").splitlines())
    if not changed <= allowed or ".github/workflows/runtime-kernel-trace-diagnostics.yml" not in changed or git("status", "--porcelain", "--untracked-files=no"):
        raise Invalid("source_changed")
    # Baseline production and tests are byte-identical to the named source tree.
    if git("diff", "--name-only", BASE, "HEAD", "src", "tests", "pyproject.toml", "requirements-dev.txt", "scripts/r3_sqlite_phases.py"):
        raise Invalid("source_changed")


def control_run(root, reader):
    """Exactly one capture. Finite stop/parse; never cancel another instance."""
    import shutil
    repo = Path(__file__).resolve().parents[1]
    original = None
    complete = clean = False
    stopped = "not_started"
    reason = "unexpected_failure"
    result = None
    started = attempted = False
    suite = None
    killed = False
    session = "R3CommitTrace_" + os.environ.get("GITHUB_RUN_ID", "") + "_1"
    wpr = str(Path(os.environ.get("SystemRoot", "")) / "System32" / "wpr.exe")
    def wr(*args, timeout=15):
        return checked_private([wpr, *args, "-instancename", session], root, timeout)
    try:
        if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Windows" or os.environ.get("GITHUB_RUN_ATTEMPT") != "1" or not os.environ.get("GITHUB_RUN_ID", "").isdigit():
            raise Invalid("wrong_environment")
        if not root.is_dir() or not reader.is_file() or not Path(wpr).is_file():
            raise Invalid("capability_missing")
        source_preflight(repo, root)
        if not storage_ok(root):
            raise Invalid("storage_guard")
        if checked_private([str(reader), "--preflight"], root):
            raise Invalid("preflight_failed")
        profile = repo / "scripts/windows_commit_trace.wprp"
        if checked_private([wpr, "-profiles", str(profile)], root) or checked_private([wpr, "-profiledetails", str(profile) + "!CommitTrace.Light"], root):
            raise Invalid("preflight_failed")
        # A UUID-like run identity plus explicit absence protects other sessions.
        absent = wr("-status")
        if absent & 0xffffffff != 0xc5583000:
            raise Invalid("preflight_failed")
        private_control = {"begin": qpc(), "frequency": frequency(), "volumes": volume_map()}
        remaining_job = float(os.environ["R3_JOB_DEADLINE_UNIX"]) - time.time()
        if remaining_job < 5 * 60:
            raise Invalid("preflight_failed")
        # Leave five minutes for stop/suite retirement/reduction/cleanup, with
        # the 30-minute workflow itself as final ephemeral-runner containment.
        deadline = time.monotonic() + min(STOP_AFTER, remaining_job - 5 * 60)
        suite_deadline = time.monotonic() + min(RUN_SECONDS, remaining_job - 3 * 60)
        (root / "start-attempted.private").write_bytes(b"1")
        attempted = True
        started = True  # Uncertain until confirmed absent, even if start times out.
        if wr("-start", str(profile) + "!CommitTrace.Light", "-recordtempto", str(root)):
            raise Invalid("preflight_failed")
        if checked_private([str(reader), "--session-check", session, str(root / "session.private")], root):
            raise Invalid("preflight_failed")
        allocation = json.loads((root / "session.private").read_text(encoding="ascii"))
        if allocation != {"matched": 1, "valid": True}:
            raise Invalid("preflight_failed")
        observer = root / "observer.private"
        with (root / "pytest.private").open("xb") as stream:
            suite = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-p", "scripts.r3_windows_commit_trace",
                                      "--windows-commit-observer", str(observer)], cwd=repo, stdout=stream, stderr=stream)
            while True:
                if (root / "target.failed").exists():
                    stopped = "target_report"
                    break
                if (root / "targets-complete.private").exists():
                    stopped = "targets_complete"
                    break
                if suite.poll() is not None:
                    stopped = "suite_complete"
                    break
                if time.monotonic() >= deadline:
                    stopped = "deadline"
                    break
                if not storage_ok(root):
                    stopped = "storage_guard"
                    break
                time.sleep(.1)
            private_control["stop_request"] = qpc()
            if wr("-stop", str(root / "capture.etl"), "-skipPdbGen", timeout=STOP_SECONDS):
                raise Invalid("stop_failed")
            if wr("-status") & 0xffffffff != 0xc5583000:
                raise Invalid("stop_failed")
            started = False
            # Preserve full suite/order after stopping only the trace.
            remaining = suite_deadline - time.monotonic()
            try:
                original = suite.wait(timeout=max(.1, remaining))
                complete = True
            except subprocess.TimeoutExpired:
                killed = True
                suite.kill()
                suite.wait(timeout=5)
                raise Invalid("suite_incomplete")
        if not observer.is_file() or observer.stat().st_size > MAX_PRIVATE:
            raise Invalid("observer_missing")
        observed = json.loads(observer.read_text(encoding="utf-8"))
        if observed.get("exit") != original or not observed.get("finished") or observed.get("invalid") is not False or observed.get("collected") != [0, 1] or [c["case"] for c in observed.get("cases", [])] != [0, 1]:
            raise Invalid("observer_incomplete")
        if not storage_ok(root):
            raise Invalid("storage_guard")
        parse_deadline = time.monotonic() + PARSE_SECONDS
        if checked_private([str(reader), str(root / "capture.etl"), str(root / "events.private")], root, PARSE_SECONDS):
            raise Invalid("parse_failed")
        private_write(root / "control.private", private_control)
        # Native decoding plus JSON/reduction share one finite parser deadline.
        remaining_parse = max(.1, parse_deadline - time.monotonic())
        if checked_private([sys.executable, str(Path(__file__).resolve()), "--reduce", str(root)], root, remaining_parse):
            raise Invalid("parse_failed")
        reduced = json.loads((root / "reduced.private").read_text(encoding="ascii"))
        if reduced["reason"] != "ok":
            raise Invalid(reduced["reason"])
        result = reduced["result"]
        source_preflight(repo, root)
        reason = "ok"
    except Invalid as error:
        reason = str(error) if str(error) in REASONS else "unexpected_failure"
    except BaseException:
        reason = "unexpected_failure"
    finally:
        if attempted and started:
            try:
                # Only our unique instance; never default-session cancellation.
                if wr("-cancel", timeout=CANCEL_SECONDS):
                    reason = "cancel_failed"
                elif wr("-status") & 0xffffffff == 0xc5583000:
                    started = False
                else:
                    reason = "cancel_failed"
            except BaseException:
                reason = "cancel_failed"
        if suite is not None and suite.poll() is not None and original is None and not killed:
            original = suite.returncode
            complete = True
        if suite is not None and suite.poll() is None:
            # A diagnostic failure must not cut short an otherwise live suite.
            try:
                original = suite.wait(timeout=max(.1, suite_deadline-time.monotonic()))
                complete = True
            except BaseException:
                killed = True
                suite.kill()
                try:
                    suite.wait(timeout=5)
                except BaseException:
                    pass
        try:
            # All raw files remain contained in this exact owned temporary root.
            if not started:
                shutil.rmtree(root)
            clean = not root.exists() and not started
        except BaseException:
            clean = False
        if not clean and reason == "ok":
            reason = "cleanup_unconfirmed"
    try:
        print(public_summary(result, original, complete, reason, stopped, clean))
    except BaseException:
        print(public_summary(None, original, complete, "summary_invalid", stopped, clean))
    try:
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write("diagnostic_valid=" + ("true" if reason == "ok" and clean else "false") + "\n")
    except BaseException:
        pass
    # Tests and diagnostics remain separate; never turn a failing test into pass.
    return original if original is not None else 2


def reduce_command(root):
    try:
        observed = json.loads((root / "observer.private").read_text(encoding="utf-8"))
        control = json.loads((root / "control.private").read_text(encoding="utf-8"))
        reduced = reduce_trace(events(root / "events.private"), observed, control)
        private_write(root / "reduced.private", {"reason": "ok", "result": reduced})
    except Invalid as error:
        reason = str(error) if str(error) in REASONS else "unexpected_failure"
        private_write(root / "reduced.private", {"reason": reason, "result": None})
    except BaseException:
        private_write(root / "reduced.private", {"reason": "unexpected_failure", "result": None})
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--reduce":
        raise SystemExit(reduce_command(Path(sys.argv[2])))
    if len(sys.argv) == 4 and sys.argv[1] == "--control":
        raise SystemExit(control_run(Path(sys.argv[2]), Path(sys.argv[3])))
    raise SystemExit(2)
