"""Failure-only, opt-in SQL evidence in the original pytest order.

Only the reviewed modules below are covered, from setup through teardown. The
common cached diagnostic helper is a failure boundary, not a polling hook. No
Runtime, store, thread, frame, cursor or native connection is retained here.
The weak registry owns only phase counters and lifetime tokens. No extra SQL,
close, wait, GC, observer thread or pre-failure output is introduced.
"""
from pathlib import Path
from types import FunctionType
from collections import deque
import ast
import hashlib
import json
import platform
import sqlite3
import sys
import threading
import time
from uuid import uuid4
from weakref import WeakKeyDictionary

import pytest

from scripts.r3_sqlite_phases import SqlitePhases, _Connection


MODULES = (
    "tests/e2e/test_mvp_smoke.py",
    "tests/runtime/presentation/test_image_demand_client.py",
    "tests/runtime/presentation/test_live_job_status.py",
    "tests/runtime/presentation/test_multi_capture.py",
    "tests/runtime/presentation/test_normal_capture.py",
    "tests/runtime/test_global_counter_grpc.py",
    "tests/runtime/test_legacy_snapshot_policy.py",
    "tests/runtime/test_operator_editor_preview.py",
    "tests/runtime/test_runtime_grpc_service.py",
    "tests/runtime/test_runtime_job_lifecycle_integration.py",
    "tests/runtime/test_runtime_project_execution.py",
    "tests/runtime/test_runtime_workflow_architecture.py",
    "tests/ui/page_designer/test_normal_run_path.py",
    "tests/ui/page_designer/test_normal_run_viewing.py",
    "tests/ui/page_designer/test_retained_acceptance_multiscope.py",
)
SOURCE_FILES = MODULES + (
    "scripts/r3_sqlite_failure_diagnostics.py", "scripts/r3_sqlite_phases.py",
    "tests/runtime/runtime_test_utils.py",
    "src/emo_master/apps/runtime/context/sqlite_store.py",
    "src/emo_master/apps/runtime/grpc_server/service.py",
    "src/emo_master/apps/runtime/events/event_store.py",
    "src/emo_master/apps/runtime/jobs/supervisor.py",
    "src/emo_master/apps/runtime/jobs/event_bridge.py",
)
OTHER_PLUGINS = ("scripts.r3_sqlite_stop_diagnostics", "scripts.r3_sqlite_capture_diagnostics",
                 "scripts.r3_a18_thread_diagnostics")
OTHER_OPTIONS = ("sqlite_stop_diagnostics", "sqlite_capture_diagnostics", "native_thread_origins")
LIMITATIONS = (
    "Diagnostic only; a passing instrumented run or NO_FAILURE_OBSERVED is inconclusive. "
    "Only reviewed common-helper timeouts and lazy failed assertions in the allowlist are covered. "
    "Failure helpers must run on the pytest main Thread; off-thread helpers pass through and invalidate evidence. "
    "SQL totals span all Jobs on one store. SQL and cached job details are independent, non-atomic readings. "
    "SQL/WAL describe the exact parent Runtime passed to the helper, not a spawned worker's separate database. "
    "One active call_id snapshot does not prove continuous blocking for any preceding wait. "
    "Process-run token, test epoch and store token identify observation lifetimes; PID/TID/object id do not. "
    "Idle keeper direct sqlite3.connect, spawned workers, subclasses/proxies and instance overrides are unobserved. "
    "No native finalization, busy-handler, checkpoint or filesystem tracing. Commit duration identifies no cause. "
    "Wrappers, locks and failure-only synchronous JSON writes perturb execution and may delay cleanup. "
    "Reported clocks and independent raw readings are preserved without clamping or overhead subtraction. "
    "Retirement disables observation without waiting for already-entered native calls to return. "
    "A hard kill can leave a partial report or lose final lifecycle validation."
)
_RUN = pytest.StashKey()
_MISSING = object()
# Exact evidenced helper-wait failures, with sources and exclusions recorded in
# docs/testing/2026-10-01-runtime-sql-failure-scope.md. No module-wide detail gate.
DETAIL_TESTS = (
    "tests/runtime/presentation/test_normal_capture.py::testOriginalStartCapturesInstalledOperatorsAndProductionCounter",
    "tests/runtime/presentation/test_normal_capture.py::testExplicitStopReleaseAndRestartNormalJob[graceful]",
    "tests/runtime/presentation/test_normal_capture.py::testExplicitStopReleaseAndRestartNormalJob[force]",
    "tests/runtime/test_legacy_snapshot_policy.py::testAllRunMissingImageCannotRelabelEarlierNodeSnapshot",
    "tests/runtime/test_legacy_snapshot_policy.py::testAllThenNoneKeepsHistoryWithoutNewSnapshotsAndRestoresPolicy",
    "tests/runtime/presentation/test_image_demand_client.py::testZeroDemandKeepsFormalResultsAndResumesFinalCapturedResult[normal]",
    "tests/runtime/presentation/test_image_demand_client.py::testZeroDemandKeepsFormalResultsAndResumesFinalCapturedResult[presentation]",
    "tests/runtime/test_runtime_job_lifecycle_integration.py::testRealSpawnConcurrencyStopsAndCleanup",
    "tests/runtime/presentation/test_multi_capture.py::testLateResetFromSameRuntimeCannotReplaceNewerScopeState",
)
SNAPSHOT_TESTS = DETAIL_TESTS[:3]
DETAIL_SOURCES = ("scripts/r3_sqlite_wal_metadata.py", "src/emo_master/apps/runtime/presentation/store.py",
                  "tests/conftest.py", "tests/runtime/presentation/conftest.py")


def validDetailScope(report):
    """Validate the schema-3 scope contract, not SQL/WAL causes or lifecycle."""
    targets = report.get("collected_detail_targets")
    if (report.get("schema") != 3 or report.get("call_details") is not True
            or report.get("declared_detail_targets") != list(DETAIL_TESTS)
            or not isinstance(targets, list) or not targets
            or any(not isinstance(node, str) or node not in DETAIL_TESTS for node in targets)
            or len(set(targets)) != len(targets)):
        return False
    for row in report.get("failures", []):
        node = row.get("test")
        detailed = node in targets
        if row.get("call_details") is not detailed or (node in DETAIL_TESTS and not detailed):
            return False
        polls, wal = row.get("existing_snapshot_polls"), row.get("wal_metadata")
        sqlDetails = row.get("sql", {}).get("connection_detail_limitations")
        if detailed:
            if not isinstance(polls, dict) or not isinstance(wal, dict) or not isinstance(sqlDetails, str):
                return False
            if node not in SNAPSHOT_TESTS:
                if polls != {"status": "UNAVAILABLE", "reason": "no_reviewed_main_thread_resultstore_polling_for_target"}:
                    return False
            elif (polls.get("status") == "OBSERVED" and type(polls.get("count")) is int and polls["count"] > 0):
                continue
            elif polls.get("status") != "UNAVAILABLE" or polls.get("reason") != "no_observed_calls" or polls.get("count") != 0:
                return False
        elif polls is not None or wal is not None or sqlDetails is not None:
            return False
    return True


def sourceHashes():
    root = Path(__file__).resolve().parents[1]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def detailSourceHashes():
    root = Path(__file__).resolve().parents[1]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in DETAIL_SOURCES}


def clocks():
    return {name: {key: getattr(time.get_clock_info(name), key) for key in
                  ("implementation", "monotonic", "adjustable", "resolution")}
            for name in ("monotonic", "perf_counter", "thread_time")}


def reading():
    # Separate calls, explicitly not simultaneous or an overhead estimate.
    return {"monotonic_ns": time.monotonic_ns(), "perf_counter_ns": time.perf_counter_ns(),
            "thread_cpu_ns": time.thread_time_ns()}


class FailureRun:
    MAX_RECORDS = 16
    MAX_BYTES = 1024 * 1024

    def __init__(self, path, storeType, runtimeType, helper, *, callDetails=False, snapshotType=None,
                 detailTargets=()):
        self.path = Path(path)
        self.storeType, self.runtimeType, self.helper = storeType, runtimeType, helper
        self.rawConnect = vars(storeType)["_connect"]
        self.originalDetails, self.originalWait = helper.jobFailureDetails, helper.waitForTerminal
        self.callDetails, self.snapshotType = callDetails, snapshotType
        self.detailTargets = tuple(detailTargets)
        self.metadataRetired = True
        self.rawSnapshot = vars(snapshotType).get("snapshot") if snapshotType is not None else None
        self.active = self.pending = None
        self.errorLock = threading.Lock()
        self.retryAttempted = False
        self.epoch = 0
        self.invalid = []
        self.records = []
        self.started = self.selected = self.failedTests = self.uncoveredFailures = 0
        self.dropped = 0
        self.report = {"schema": 1, "role": "diagnostic_only", "process_run_token": uuid4().hex,
            "python": sys.version, "platform": platform.platform(), "sqlite_version": sqlite3.sqlite_version,
            "clocks": clocks(), "allowlist": MODULES, "limitations": LIMITATIONS,
            "source_sha256_before": sourceHashes(), "session_finished": False,
            "observer_retired": False, "original_pytest_exit": None}
        if callDetails:
            self.report.update(schema=3, call_details=True, declared_detail_targets=DETAIL_TESTS,
                               collected_detail_targets=self.detailTargets,
                               detail_source_sha256_before=detailSourceHashes())

    def invalidate(self, reason):
        with self.errorLock:
            if reason not in self.invalid and len(self.invalid) < 32:
                self.invalid.append(reason)

    def stableSource(self):
        try:
            after = sourceHashes()
            self.report["source_sha256_after"] = after
            stable = after == self.report["source_sha256_before"]
            if self.callDetails:
                extra = detailSourceHashes()
                self.report["detail_source_sha256_after"] = extra
                stable = stable and extra == self.report["detail_source_sha256_before"]
        except BaseException as error:
            stable = False
            self.invalidate("source_read:" + type(error).__name__)
        if not stable:
            self.invalidate("changed_source")
        self.report["source_stable"] = stable
        return stable

    def payload(self):
        status = ("INVALID" if self.invalid else
                  "UNOBSERVED" if any(row["status"] != "OBSERVED" for row in self.records) else
                  "FAILURE_OBSERVED" if self.records else "NO_FAILURE_OBSERVED")
        return dict(self.report, status=status, errors=list(self.invalid), failures=self.records,
                    selected_tests=self.selected, started_tests=self.started,
                    failed_tests=self.failedTests, failed_tests_without_helper=self.uncoveredFailures,
                    dropped_failure_records=self.dropped)

    def save(self):
        if not self.records:
            return  # No successful-test, collection or retirement output file.
        try:
            content = json.dumps(self.payload(), ensure_ascii=True, indent=2)
            if len(content.encode("utf-8")) > self.MAX_BYTES:
                self.invalidate("report_byte_quota")
                # Preserve identity and lifecycle while explicitly discarding
                # oversized details; never replace them with invented evidence.
                for row in self.records:
                    row.pop("sql", None)
                    row.pop("cached_job_details", None)
                    row.pop("existing_snapshot_polls", None)
                    row.pop("wal_metadata", None)
                    row["status"] = "INVALID"
                content = json.dumps(self.payload(), ensure_ascii=True, indent=2)
            if len(content.encode("utf-8")) > self.MAX_BYTES:
                raise OverflowError("report byte quota")
            # First creation is exclusive; later writes update only this run's
            # own failure file. No file is opened before a helper invocation.
            mode = "w" if self.report.get("output_created") else "x"
            with self.path.open(mode, encoding="utf-8") as stream:
                self.report["output_created"] = True
                stream.write(content)
        except BaseException as error:
            self.invalidate("report_write:" + type(error).__name__)


class FailureOwner:
    MAX_LIVE = 16
    MAX_LIFETIMES = 64

    def __init__(self, run, module, nodeid):
        self.run, self.module, self.nodeid = run, module, nodeid[:512]
        run.epoch += 1
        self.epoch = run.epoch
        self.registry = WeakKeyDictionary()
        self.lock = threading.Lock()
        self.lifetimes = 0
        self.gaps = {}
        self.errors = []
        self.patches = []
        self.active = True
        self.helperCalls = 0
        self.outcomes = {name: "pending" for name in ("setup", "call", "teardown")}
        self.callDetails = run.callDetails and self.nodeid in DETAIL_TESTS and self.nodeid in run.detailTargets
        self.observePolls = self.callDetails and self.nodeid in SNAPSHOT_TESTS and run.snapshotType is not None
        self.allowedRoot = None
        self.walCleanupConfirmed = True
        self.pollRows = deque(maxlen=128)
        self.pollStores = WeakKeyDictionary()
        self.pollSerial = self.pollCount = self.pollFailed = self.pollDropped = 0
        self.pollFirst = self.pollLast = self.pollMaxGap = None

    def invalidate(self, reason):
        with self.run.errorLock:
            if reason not in self.errors and len(self.errors) < 16:
                self.errors.append(reason)
        self.run.invalidate(reason)

    def checkIdentities(self):
        for target, name, original, replacement in self.patches:
            if vars(target).get(name, _MISSING) is not replacement:
                self.invalidate("changed_patch:" + name)
        if self.run.helper.waitForTerminal is not self.run.originalWait:
            self.invalidate("changed_wait_helper")
        alias = vars(self.module).get("waitForTerminal", _MISSING)
        if alias is not _MISSING and alias is not self.run.originalWait:
            self.invalidate("changed_wait_alias")

    def patch(self, target, name, original, replacement):
        if vars(target).get(name, _MISSING) is not original:
            raise RuntimeError("changed patch source: " + name)
        # Register first: a setter may mutate before it raises.
        self.patches.append((target, name, original, replacement))
        setattr(target, name, replacement)

    def state(self, store):
        if not self.active or type(store) is not self.run.storeType:
            return None
        with self.lock:
            if not self.active:
                return None
            existing = self.registry.get(store)
            if existing is not None:
                return existing
            if len(self.registry) >= self.MAX_LIVE or self.lifetimes >= self.MAX_LIFETIMES:
                self.gaps[0] = "store_registry_quota"
                return None
            self.lifetimes += 1
            value = (self.lifetimes, SqlitePhases(details=self.callDetails))
            self.registry[store] = value
            return value

    def install(self):
        run = self.run
        original = run.rawConnect
        if threading.current_thread() is not threading.main_thread():
            self.invalidate("installation_off_main_thread")
            self.active = False
            return

        def connect(store, *args, **kwargs):
            state = None
            try:
                state = self.state(store)
            except BaseException as error:
                self.invalidate("registry:" + type(error).__name__)
            # Never hold the registry lock across an original/native call, and
            # never retry that call when diagnostics fail.
            connection = (state[1].call("connect_configure", original, store, *args, **kwargs)
                          if state else original(store, *args, **kwargs))
            if state is None:
                return connection
            with self.lock:
                if not self.active or not state[1].enabled:
                    return connection
                try:
                    if type(connection) is not sqlite3.Connection:
                        self.gaps[state[0]] = "unsupported_connection_type"
                        return connection
                    return _Connection(connection, state[1])
                except BaseException as error:
                    self.invalidate("connection_adapter:" + type(error).__name__)
                    return connection

        def details(*args, **kwargs):
            # Failed restoration may leave a dormant wrapper reachable by
            # later tests. It must not reuse this test's epoch or write files.
            with self.lock:
                active = self.active
            if not active:
                return run.originalDetails(*args, **kwargs)
            # The reviewed failure sites execute on pytest's main Thread.
            # Compare live Thread objects directly, never a reusable TID. No
            # Thread is retained and no concurrent failure writer is created.
            if threading.current_thread() is not threading.main_thread():
                self.invalidate("failure_helper_off_main_thread")
                return run.originalDetails(*args, **kwargs)
            row = None
            try:
                runtime = kwargs.get("runtimeService", args[0] if args else None)
                job = kwargs.get("jobId", args[1] if len(args) > 1 else None)
                row = self.capture(runtime, job)
            except BaseException as error:
                self.invalidate("failure_capture:" + type(error).__name__)
            try:
                result = run.originalDetails(*args, **kwargs)
            except BaseException as error:
                if row is not None and self.active:
                    row["helper_exception_type"] = type(error).__name__
                raise
            else:
                if row is not None and self.active and isinstance(result, str):
                    row["cached_job_details"] = result[:16384]
                return result
            finally:
                try:
                    if row is not None and self.active:
                        row["after_cached_helper"] = reading()
                    if row is not None and self.active:
                        run.save()
                except BaseException as error:
                    self.invalidate("failure_save:" + type(error).__name__)

        def snapshot(store, *args, **kwargs):
            # Observe only calls the original selected test already makes.
            # Never retain the return object or manufacture another poll.
            observed = self.active and threading.current_thread() is threading.main_thread()
            start = _pollClock() if observed else None
            failed = False
            try:
                return run.rawSnapshot(store, *args, **kwargs)
            except BaseException:
                failed = True
                raise
            finally:
                end = _pollClock() if observed and self.active else None
                if observed and self.active:
                    try:
                        self.recordPoll(store, args, kwargs, start, end, failed)
                    except BaseException as error:
                        self.invalidate("existing_poll:" + type(error).__name__)

        try:
            self.patch(run.storeType, "_connect", original, connect)
            self.patch(run.helper, "jobFailureDetails", run.originalDetails, details)
            alias = vars(self.module).get("jobFailureDetails", _MISSING)
            if alias is not _MISSING:
                self.patch(self.module, "jobFailureDetails", run.originalDetails, details)
            if self.observePolls:
                self.patch(run.snapshotType, "snapshot", run.rawSnapshot, snapshot)
            self.checkIdentities()
        except BaseException as error:
            self.invalidate("installation:" + type(error).__name__)
            self.active = False

    def recordPoll(self, store, args, kwargs, start, end, failed):
        if type(store) is not self.run.snapshotType:
            return
        token = self.pollStores.get(store)
        if token is None:
            if self.pollSerial >= self.MAX_LIFETIMES or len(self.pollStores) >= self.MAX_LIVE:
                self.pollDropped += 1
                return
            self.pollSerial += 1
            token = self.pollSerial
            self.pollStores[store] = token
        job = kwargs.get("jobId", args[0] if args else None)
        self.pollCount += 1
        self.pollFailed += int(failed)
        if self.pollFirst is None:
            self.pollFirst = start
        if isinstance(start, int) and isinstance(self.pollLast, int):
            gap = start - self.pollLast
            self.pollMaxGap = gap if self.pollMaxGap is None else max(self.pollMaxGap, gap)
        self.pollLast = start
        self.pollDropped += int(len(self.pollRows) == self.pollRows.maxlen)
        self.pollRows.append({"serial": self.pollCount, "store_operation_token": token,
            "job_id": job[:128] if isinstance(job, str) else None,
            "start_perf_ns": start, "end_perf_ns": end, "failed": failed})

    def pollSnapshot(self):
        if not self.observePolls:
            return {"status": "UNAVAILABLE", "reason": "no_reviewed_main_thread_resultstore_polling_for_target"}
        return {"status": "OBSERVED" if self.pollCount else "UNAVAILABLE",
            **({} if self.pollCount else {"reason": "no_observed_calls"}),
            "count": self.pollCount, "failed": self.pollFailed, "omitted": self.pollDropped,
            "first_start_perf_ns": self.pollFirst, "last_start_perf_ns": self.pollLast,
            "max_intercall_start_gap_ns": self.pollMaxGap, "tail": list(self.pollRows),
            "limitations": "Only existing main-thread snapshot calls in the exact selected test. "
                "First wrapper observation is not the original wait start or deadline. "
                "Intervals may span different Jobs and include test actions. No extra polls. "
                "Progress cannot distinguish commit-thread descheduling from I/O waits."}

    def capture(self, runtime, job):
        with self.lock:
            if not self.active:
                return None
            self.helperCalls += 1
        if len(self.run.records) >= self.run.MAX_RECORDS:
            self.run.dropped += 1
            self.invalidate("failure_record_quota")
            return None
        self.checkIdentities()
        row = {"test": self.nodeid, "test_epoch": self.epoch,
               "call_details": self.callDetails,
               "process_run_token": self.run.report["process_run_token"],
               "job_id": job[:128] if isinstance(job, str) else None,
               "status": "UNOBSERVED", "before_sql_snapshot": reading(),
               "outcomes": dict(self.outcomes), "gaps": [], "errors": []}
        if type(runtime) is not self.run.runtimeType:
            row["gaps"].append("not_exact_runtime_type")
        else:
            store = vars(runtime).get("sqliteStore")
            if type(store) is not self.run.storeType:
                row["gaps"].append("missing_or_unsupported_store")
            elif "_connect" in vars(store):
                row["gaps"].append("instance_connect_override")
            else:
                with self.lock:
                    state = self.registry.get(store)
                    gaps = dict(self.gaps)
                if state is None:
                    row["gaps"].append("untracked_store")
                else:
                    token, phases = state
                    row["store_token"] = token
                    row["sql"] = phases.snapshot()
                    if token in gaps:
                        row["gaps"].append(gaps[token])
                    if not row["sql"].get("enabled") or row["sql"].get("errors"):
                        self.invalidate("phase_observer_disabled")
                    if not row["gaps"]:
                        row["status"] = "OBSERVED"
                if 0 in gaps:
                    row["gaps"].append(gaps[0])
                    row["status"] = "UNOBSERVED"
        row["after_sql_snapshot"] = reading()
        if self.callDetails:
            row["existing_snapshot_polls"] = self.pollSnapshot()
            # This boundary is reached only after the original predicate has
            # failed. No successful-path filesystem observation is added.
            if type(runtime) is self.run.runtimeType and type(vars(runtime).get("sqliteStore")) is self.run.storeType:
                try:
                    from scripts.r3_sqlite_wal_metadata import captureWalMetadata
                    row["wal_metadata"] = captureWalMetadata(
                        vars(runtime.sqliteStore).get("dbPath"), self.allowedRoot)
                except BaseException as error:
                    row["wal_metadata"] = {"status": "UNAVAILABLE", "error_type": type(error).__name__,
                                           "cleanup": {"retirement_confirmed": False}}
                metadata = row["wal_metadata"]
                if not isinstance(metadata, dict) or metadata.get("cleanup", {}).get("retirement_confirmed") is not True:
                    self.walCleanupConfirmed = self.run.metadataRetired = False
                    self.invalidate("wal_metadata_cleanup_unconfirmed")
            else:
                row["wal_metadata"] = {"status": "UNAVAILABLE", "reason": "not_exact_owned_store"}
        self.run.stableSource()
        row["errors"] = list(self.errors)
        if self.errors or self.run.invalid:
            row["status"] = "INVALID"
        with self.lock:
            if not self.active:
                return None
            self.run.records.append(row)
        return row

    def retire(self):
        with self.lock:
            self.active = False
        self.checkIdentities()
        remaining = []
        for target, name, original, replacement in reversed(self.patches):
            current = vars(target).get(name, _MISSING)
            if current is replacement:
                try:
                    setattr(target, name, original)
                except BaseException as error:
                    self.invalidate("restoration:" + type(error).__name__)
                if vars(target).get(name, _MISSING) is replacement:
                    remaining.append((target, name, original, replacement))
                elif vars(target).get(name, _MISSING) is not original:
                    self.invalidate("changed_during_restoration:" + name)
            elif current is not original:
                self.invalidate("third_party_replacement:" + name)
        self.patches = list(reversed(remaining))
        with self.lock:
            for _token, phases in self.registry.values():
                phases.enabled = False
            self.registry.clear()
        self.pollStores.clear()
        for row in self.run.records:
            if row["test_epoch"] == self.epoch:
                row["outcomes"] = dict(self.outcomes)
                row["observer_retired"] = not self.patches and self.walCleanupConfirmed
                row["errors"] = list(self.errors)
                if self.errors:
                    row["status"] = "INVALID"
        return not self.patches


def pytest_addoption(parser):
    parser.addoption("--sqlite-failure-diagnostics", metavar="NEW.json",
                     help="failure-only SQL evidence for verified common runtime failure helpers")
    parser.addoption("--sqlite-failure-call-details", action="store_true", default=False,
                     help="opt-in QPC/operation details and failure-only WAL header for exact reviewed runtime waits")


def pytest_collection_modifyitems(config, items):
    output = config.getoption("sqlite_failure_diagnostics")
    detailed = config.getoption("sqlite_failure_call_details", default=False)
    if not output:
        if detailed:
            raise pytest.UsageError("SQLite call details require --sqlite-failure-diagnostics")
        return
    if (any(config.pluginmanager.hasplugin(name) for name in OTHER_PLUGINS)
            or any(getattr(loaded, "__name__", None) in OTHER_PLUGINS
                   for loaded in config.pluginmanager.get_plugins())
            or any(config.getoption(name, default=None) for name in OTHER_OPTIONS)):
        raise pytest.UsageError("SQLite failure diagnostics cannot stack diagnostic plugins/options")
    if config.stash.get(_RUN, None) is not None:
        raise pytest.UsageError("SQLite failure diagnostic plugin is already installed")
    if Path(output).exists() or not Path(output).parent.is_dir():
        raise pytest.UsageError("SQLite failure output must be new, in an existing directory")
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.runtime import runtime_test_utils as helper
    root = Path(__file__).resolve().parents[1]
    raw = vars(SqliteStore).get("_connect")
    for function, name, source in ((raw, "_connect", "src/emo_master/apps/runtime/context/sqlite_store.py"),
                             (helper.jobFailureDetails, "jobFailureDetails", "tests/runtime/runtime_test_utils.py"),
                             (helper.waitForTerminal, "waitForTerminal", "tests/runtime/runtime_test_utils.py")):
        if (type(function) is not FunctionType or function.__code__.co_name != name
                or Path(function.__code__.co_filename).resolve() != root / source):
            raise pytest.UsageError("SQLite failure diagnostic source identity is overridden")
    selected = 0
    checked = set()
    for item in items:
        name = item.nodeid.split("::", 1)[0]
        if name not in MODULES:
            continue
        selected += 1
        if name in checked:
            continue
        checked.add(name)
        if Path(item.path).resolve() != root / name or Path(item.module.__file__).resolve() != root / name:
            raise pytest.UsageError("SQLite failure diagnostic module identity mismatch")
        imports = [alias for node in ast.walk(ast.parse((root / name).read_text(encoding="utf-8")))
                   if isinstance(node, ast.ImportFrom) and node.module == "tests.runtime.runtime_test_utils"
                   for alias in node.names]
        if not imports or any(alias.name not in {"waitForTerminal", "jobFailureDetails"}
                              or alias.asname not in (None, alias.name) for alias in imports):
            raise pytest.UsageError("SQLite failure diagnostic helper coverage changed")
        for alias in ("waitForTerminal", "jobFailureDetails"):
            value = vars(item.module).get(alias, _MISSING)
            if value is not _MISSING and value is not getattr(helper, alias):
                raise pytest.UsageError("SQLite failure diagnostic module helper alias is overridden")
    if not selected:
        raise pytest.UsageError("SQLite failure diagnostics require a reviewed allowlisted test")
    snapshotType = None
    detailTargets = ()
    if detailed:
        detailTargets = tuple(dict.fromkeys(item.nodeid for item in items if item.nodeid in DETAIL_TESTS))
        if not detailTargets:
            raise pytest.UsageError("SQLite call details require an exact reviewed runtime wait test")
        from emo_master.apps.runtime.presentation.store import ResultStore
        snapshotType = ResultStore
        snapshot = vars(ResultStore).get("snapshot")
        if (type(snapshot) is not FunctionType or snapshot.__code__.co_name != "snapshot"
                or Path(snapshot.__code__.co_filename).resolve() != root / DETAIL_SOURCES[1]):
            raise pytest.UsageError("SQLite snapshot diagnostic source identity is overridden")
    run = FailureRun(output, SqliteStore, RuntimeService, helper,
                     callDetails=detailed, snapshotType=snapshotType, detailTargets=detailTargets)
    run.selected = selected
    config.stash[_RUN] = run


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    run = item.config.stash.get(_RUN, None)
    if run is not None and run.active is not None and run.active.callDetails and item.nodeid == run.active.nodeid:
        root = getattr(item, "funcargs", {}).get("tmp_path")
        # Only the already-created, exact test fixture root is allowed. Do not
        # request a fixture or fall back to a production/default database root.
        if isinstance(root, Path) and root.is_absolute():
            run.active.allowedRoot = str(root)


def _pollClock():
    try:
        return time.perf_counter_ns()
    except BaseException:
        return None


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    run = item.config.stash.get(_RUN, None)
    if run is None or item.nodeid.split("::", 1)[0] not in MODULES:
        return (yield)
    if run.active is not None or run.pending is not None:
        run.invalidate("pending_owner_prevents_observation")
        return (yield)
    owner = FailureOwner(run, item.module, item.nodeid)
    run.active = owner
    run.started += 1
    owner.install()
    try:
        return (yield)
    except BaseException:
        owner.invalidate("interrupted_test_protocol")
        raise
    finally:
        if not owner.retire():
            run.pending = owner
        if "failed" in owner.outcomes.values():
            run.failedTests += 1
            if not owner.helperCalls:
                run.uncoveredFailures += 1
                run.invalidate("failed_target_without_helper")
        run.active = None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    run = item.config.stash.get(_RUN, None)
    if run is not None and run.active is not None and report.when in run.active.outcomes:
        run.active.outcomes[report.when] = report.outcome
    return report


def pytest_sessionfinish(session, exitstatus):
    run = session.config.stash.get(_RUN, None)
    if run is None or run.report["session_finished"]:
        return
    run.report["original_pytest_exit"] = int(exitstatus)
    if run.pending is not None and not run.retryAttempted:
        run.retryAttempted = True
        run.report["restoration_retry"] = "succeeded" if run.pending.retire() else "failed"
        if not run.pending.patches:
            run.pending = None
    run.stableSource()
    run.report["observer_retired"] = run.active is None and run.pending is None and run.metadataRetired
    if not run.report["observer_retired"]:
        run.invalidate("observer_not_retired")
    if run.started != run.selected:
        run.invalidate("selected_test_coverage_incomplete")
    run.report["session_finished"] = True
    run.save()
    # Never replace an original failure/interrupt/usage code with success or a
    # different failure. A successful test run with invalid evidence is invalid.
    if run.invalid and not session.exitstatus:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    terminal = session.config.pluginmanager.getplugin("terminalreporter")
    if terminal is not None:
        terminal.write_line("SQLITE_FAILURE_DIAGNOSTIC_SUMMARY " + json.dumps({key: value for key, value in
            run.payload().items() if key not in {"failures", "source_sha256_before", "source_sha256_after"}},
            ensure_ascii=True))
