"""Opt-in evidence for the unchanged ordinary normal-capture integration test.

python -m pytest -q -p scripts.r3_sqlite_capture_diagnostics \
  --sqlite-capture-diagnostics NEW.json

The full suite keeps its original order. Only TARGET's already-created channel
Runtime is observed, through fixture teardown. No additional SQL, worker target,
keeper, production lock, GC, deadline or durability changes are introduced.
"""
from pathlib import Path
import hashlib
import platform
import sqlite3
import subprocess
import sys
import time

import pytest

from scripts.r3_job_diagnostics import JobDiagnostics


TARGET = "tests/runtime/presentation/test_normal_capture.py::testOriginalStartCapturesInstalledOperatorsAndProductionCounter"
SOURCE_FILES = (
    "scripts/r3_sqlite_phases.py", "scripts/r3_job_diagnostics.py",
    "scripts/r3_sqlite_capture_diagnostics.py", "tests/runtime/runtime_test_utils.py",
    "tests/runtime/presentation/conftest.py", "tests/runtime/presentation/test_normal_capture.py",
    "src/emo_master/apps/runtime/context/sqlite_store.py",
    "src/emo_master/apps/runtime/events/event_store.py",
    "src/emo_master/apps/runtime/grpc_server/service.py",
    "src/emo_master/apps/runtime/presentation/service.py",
    "src/emo_master/apps/runtime/presentation/normal_capture.py",
    "src/emo_master/apps/runtime/jobs/supervisor.py", "src/emo_master/apps/runtime/jobs/event_bridge.py",
)
_SESSION = pytest.StashKey()
_RETIREMENT = pytest.StashKey()


class CaptureDiagnostics(JobDiagnostics):
    # Two sequential Jobs; preserve more of the first wait without unbounded
    # history. The shared observer still enforces its independent 1 MiB limit.
    MAX_SNAPSHOTS = 64

    def _ownersRetired(self):
        return (self.closed and not self.patches and self.sinkPatch is None
                and (self.sqlitePhases is None or self.sqlitePhases.patch is None)
                and (self.thread is None or not self.thread.is_alive()))

    def retire(self):
        # Base close marks closed before saving, and releases runtime after
        # saving. A failed final write must not keep the Runtime alive, nor
        # may closed=True let a retry skip that missing report.
        try:
            if self.closed:
                if not self._ownersRetired():
                    raise RuntimeError("diagnostic retirement is incomplete")
                self._save()
            else:
                self.close()
        finally:
            if self._ownersRetired():
                self.runtime = None
        if not self._ownersRetired() or self.runtime is not None:
            raise RuntimeError("diagnostic still owns Runtime resources")

    def _state(self):
        before = self.jobId
        state = super()._state()
        state.update(job_id_before=before, job_id_after=self.jobId, job_id_stable=before == self.jobId)
        return state


def sourceHashes():
    root = Path(__file__).resolve().parents[1]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def pytest_addoption(parser):
    parser.addoption("--sqlite-capture-diagnostics", metavar="NEW.json",
                     help="opt-in SQL phase/progress evidence for the original normal-capture test")


def pytest_collection_modifyitems(config, items):
    output = config.getoption("sqlite_capture_diagnostics")
    if not output:
        return
    if sum(item.nodeid == TARGET for item in items) != 1:
        raise pytest.UsageError("SQLite capture diagnostics require exactly one selected normal-capture test")
    if Path(output).exists() or not Path(output).parent.is_dir():
        raise pytest.UsageError("SQLite capture diagnostic output must be new, in an existing directory")


def _waitBoundary(probe, row, phase):
    try:
        probe._frontier("terminal_wait", {"ordinal": row["ordinal"], "jobId": row["job_id"], "phase": phase})
    except BaseException as error:
        probe._error(error)
    probe.capture("terminal_wait_" + phase, stacks=phase == "failed", save=phase == "failed")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    output = item.config.getoption("sqlite_capture_diagnostics")
    if not output or item.nodeid != TARGET:
        return (yield)
    source = {"test": TARGET, "python": sys.version, "platform": platform.platform(),
              "sqlite_version": sqlite3.sqlite_version, "pytest_outcome": "running",
              "fixture_teardown_outcome": "pending", "waits": [], "runtime_bound": False}
    try:
        source["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1], text=True, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        source["commit"] = "unavailable"
    source["observer_effect"] = ("Explicit instrumented run. Construction creates an empty output file before the test call. "
        "Periodic and wait entry/normal return samples stay in bounded memory. Wrapper enter_ns precedes entry sampling "
        "and the original wait deadline; exit_ns includes that entry observer cost. Original wait arguments/deadlines are unchanged. "
        "Only failed waits and final retirement write JSON synchronously; failure writes can delay cleanup. "
        "Sampling and scheduling perturb execution, and a hard kill may lose the in-memory tail. "
        "A diagnostic PASS does not prove uninstrumented Windows behavior. State job IDs bracket a non-atomic read; "
        "SQL phases are store-wide, not per-Job. Stable active call IDs, rather than repeated commit stacks, identify one pending call.")
    probe = CaptureDiagnostics(output, source=source, plannedSeconds=1, sampleSeconds=0.5, persistPeriodic=False)
    item.stash[_SESSION] = probe
    try:
        source["file_sha256_before"] = sourceHashes()
        # The channel fixture was created during setup, before this call hook.
        # Do not replace a factory or create another Runtime for observation.
        runtime = item.funcargs["channel"].runtime
        probe.install(runtime, sqlitePhases=True, workerCounters=False)
        source["runtime_bound"] = True
        originalClose = runtime.close
        originalWait = item.module.waitForTerminal

        def close(*args, **kwargs):
            probe.capture("before_runtime_close", save=False)
            try:
                return originalClose(*args, **kwargs)
            finally:
                probe.capture("after_runtime_close", save=False)

        def wait(*args, **kwargs):
            jobId = kwargs.get("jobId", args[1] if len(args) > 1 else "")
            row = {"ordinal": len(source["waits"]) + 1, "job_id": str(jobId)[:80],
                   "enter_ns": time.monotonic_ns(), "outcome": "running"}
            if len(source["waits"]) < 2:
                source["waits"].append(row)
            else:
                probe._error(RuntimeError("unexpected extra terminal wait"))
            _waitBoundary(probe, row, "enter")
            try:
                result = originalWait(*args, **kwargs)
            except BaseException:
                row.update(exit_ns=time.monotonic_ns(), outcome="failed")
                _waitBoundary(probe, row, "failed")
                raise
            row.update(exit_ns=time.monotonic_ns(), outcome="returned")
            _waitBoundary(probe, row, "return")
            return result

        probe._patch(runtime, "close", close)
        probe._patch(item.module, "waitForTerminal", wait)
    except BaseException as error:
        probe._error(error)
    try:
        result = yield
        source["pytest_outcome"] = "passed"
        return result
    except BaseException:
        source["pytest_outcome"] = "failed"
        raise
    finally:
        probe.capture("test_call_finished", save=False)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item):
    probe = item.stash.get(_SESSION, None)
    if probe is None:
        return (yield)
    source = probe.source
    originalFailed = source["pytest_outcome"] != "passed"
    try:
        result = yield
        source["fixture_teardown_outcome"] = "passed"
        return result
    except BaseException:
        originalFailed = True
        source["fixture_teardown_outcome"] = "failed"
        raise
    finally:
        probe.capture("fixture_teardown_finished", save=False)
        try:
            source["file_sha256_after"] = sourceHashes()
        except OSError as error:
            probe._error(error)
        source["source_stable"] = bool(source.get("file_sha256_before")) and source.get("file_sha256_before") == source.get("file_sha256_after")
        phase = probe.sqlitePhases.snapshot() if probe.sqlitePhases is not None else {}
        waitsValid = (source["pytest_outcome"] != "passed" or
                      len(source["waits"]) == 2 and all(row["outcome"] == "returned" for row in source["waits"]))
        valid = source["runtime_bound"] and source["source_stable"] and phase.get("enabled", False) and waitsValid and not probe.errors
        source["diagnostic_valid"] = bool(valid)
        try:
            probe.retire()
        except BaseException as error:
            valid = False
            source["diagnostic_valid"] = False
            source["retirement_retry"] = "pending"
            probe._error(error)
            # Collection permits exactly one target. Keep its failed owner
            # explicitly until one session-end retry; retain only the stash,
            # not the item/config ownership graph.
            item.config.stash[_RETIREMENT] = {"probe": probe, "item_stash": item.stash, "attempted": False}
        else:
            del item.stash[_SESSION]
        # Keep both the exact original call failure and fixture teardown failure.
        if not originalFailed and (not valid or probe.errors):
            pytest.fail("SQLite capture diagnostic observer failed; original test outcome is recorded in evidence")


def pytest_sessionfinish(session):
    pending = session.config.stash.get(_RETIREMENT, None)
    if pending is None or pending["attempted"]:
        return
    pending["attempted"] = True
    probe = pending["probe"]
    # Successful cleanup cannot convert a broken diagnostic into a PASS.
    probe.source["diagnostic_valid"] = False
    probe.source["retirement_retry"] = "succeeded"
    try:
        probe.retire()
    except BaseException as error:
        probe.source["retirement_retry"] = "failed"
        probe._error(error)
        # Best-effort final INVALID report. Failed output or ownership remains
        # explicitly retained; there is no repeated retry loop or GC rescue.
        try:
            probe._save()
        except BaseException as saveError:
            probe._error(saveError)
    else:
        if pending["item_stash"].get(_SESSION, None) is probe:
            del pending["item_stash"][_SESSION]
        del session.config.stash[_RETIREMENT]
    if session.exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
