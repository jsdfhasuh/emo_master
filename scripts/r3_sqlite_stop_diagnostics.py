"""Explicit pytest plugin for the unchanged real-spawn stop test.

python -m pytest -q -p scripts.r3_sqlite_stop_diagnostics \
  --sqlite-stop-diagnostics NEW.json \
  tests/runtime/test_runtime_job_lifecycle_integration.py::testRealSpawnConcurrencyStopsAndCleanup

It may also accompany the whole suite to observe its original test order. Only
the named test is instrumented. Deadlines, worker targets, SQL and durability
remain unchanged. Output is bounded and excludes SQL, parameters and payloads.
"""
from pathlib import Path
import hashlib
import platform
import sqlite3
import subprocess
import sys

import pytest

from scripts.r3_job_diagnostics import JobDiagnostics


TARGET = "tests/runtime/test_runtime_job_lifecycle_integration.py::testRealSpawnConcurrencyStopsAndCleanup"
SOURCE_FILES = (
    "scripts/r3_sqlite_phases.py", "scripts/r3_job_diagnostics.py", "scripts/r3_sqlite_stop_diagnostics.py",
    "tests/runtime/runtime_test_utils.py", "tests/runtime/test_runtime_job_lifecycle_integration.py",
    "src/emo_master/apps/runtime/context/sqlite_store.py",
    "src/emo_master/apps/runtime/events/event_store.py",
    "src/emo_master/apps/runtime/jobs/supervisor.py", "src/emo_master/apps/runtime/jobs/event_bridge.py",
)


def sourceHashes():
    root = Path(__file__).resolve().parents[1]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def pytest_addoption(parser):
    parser.addoption("--sqlite-stop-diagnostics", metavar="NEW.json",
                     help="opt-in SQL phase/progress evidence for the real-spawn lifecycle test")


def pytest_collection_modifyitems(config, items):
    output = config.getoption("sqlite_stop_diagnostics")
    if not output:
        return
    if sum(item.nodeid == TARGET for item in items) != 1:
        raise pytest.UsageError("SQLite stop diagnostics require exactly one selected lifecycle test")
    if Path(output).exists() or not Path(output).parent.is_dir():
        raise pytest.UsageError("SQLite stop diagnostic output must be new, in an existing directory")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    output = item.config.getoption("sqlite_stop_diagnostics")
    if not output or item.nodeid != TARGET:
        return (yield)
    source = {"test": TARGET, "python": sys.version, "platform": platform.platform(),
              "sqlite_version": sqlite3.sqlite_version, "pytest_outcome": "running"}
    try:
        source["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1], text=True, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        source["commit"] = "unavailable"
    source["observer_effect"] = ("Explicit instrumented run. Periodic and wait entry/normal return samples stay in bounded memory. "
        "Failure/cleanup/final JSON writes are synchronous and can delay cleanup. Sampling and scheduling still perturb execution. "
        "A hard kill may lose the in-memory tail. A diagnostic PASS does not prove uninstrumented Windows behavior.")
    probe = JobDiagnostics(output, source=source, plannedSeconds=1, sampleSeconds=0.5, persistPeriodic=False)
    try:
        source["file_sha256_before"] = sourceHashes()
    except OSError as error:
        probe._error(error)
    originalFactory = item.module.RuntimeService
    originalWait = item.module.waitForTerminal
    patch = pytest.MonkeyPatch()
    initialized = False
    originalFailed = False

    def factory(*args, **kwargs):
        nonlocal initialized
        runtime = originalFactory(*args, **kwargs)
        if initialized:
            probe._error(RuntimeError("unexpected second Runtime"))
            return runtime
        initialized = True
        try:
            # Concurrent workers would make the single shared counter set
            # ambiguous. This run observes only the owner and native SQL calls.
            probe.install(runtime, sqlitePhases=True, workerCounters=False)
            originalClose = runtime.close

            def close(*args, **kwargs):
                probe.capture("before_runtime_close")
                try:
                    return originalClose(*args, **kwargs)
                finally:
                    probe.capture("after_runtime_close")

            # Instance monkeypatch undo would write a resolved bound method
            # back as an own attribute, adding a self-cycle after the test.
            probe._patch(runtime, "close", close)
        except BaseException as error:
            probe._error(error)
        return runtime

    def wait(*args, **kwargs):
        probe.capture("terminal_wait_enter", stacks=False, save=False)
        try:
            return originalWait(*args, **kwargs)
        except BaseException:
            probe.capture("terminal_wait_failed")
            raise
        finally:
            probe.capture("terminal_wait_return", stacks=False, save=False)

    patch.setattr(item.module, "RuntimeService", factory)
    patch.setattr(item.module, "waitForTerminal", wait)
    try:
        result = yield
        source["pytest_outcome"] = "passed"
        return result
    except BaseException:
        originalFailed = True
        source["pytest_outcome"] = "failed"
        raise
    finally:
        patch.undo()
        probe.capture("test_finished")
        try:
            source["file_sha256_after"] = sourceHashes()
        except OSError as error:
            probe._error(error)
        source["source_stable"] = bool(source.get("file_sha256_before")) and source.get("file_sha256_before") == source.get("file_sha256_after")
        phase = probe.sqlitePhases.snapshot() if probe.sqlitePhases is not None else {}
        valid = initialized and source["source_stable"] and phase.get("enabled", False) and not probe.errors
        source["diagnostic_valid"] = valid
        try:
            probe.close()
        except BaseException as error:
            valid = False
            source["diagnostic_valid"] = False
            probe._error(error)
        # Keep the exact original test failure. A successful test with broken
        # observations is an invalid diagnostic run, never a diagnostic PASS.
        if not originalFailed and (not valid or probe.errors):
            pytest.fail("SQLite diagnostic observer failed; original test outcome is recorded in evidence")
