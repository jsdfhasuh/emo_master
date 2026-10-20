"""Opt-in, exact-test SQLite call-cost observer. Diagnostic only, not acceptance.

Run the original test, unchanged, with -p scripts.join_sqlite_diagnostic and
--join-sqlite-report NEW_FILE. No database payloads or SQL parameters are retained.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re

import pytest

from scripts.r3_sqlite_phases import SqlitePhases

TARGET = ("tests/runtime/test_review_dispatch_lifecycle.py::"
          "testThreeHundredSequentialJoinsWithTwoRealPersistentConsumers")
MAX_STORES = 8
MAX_BYTES = 1024 * 1024


def sourceHashes(root):
    paths = sorted((root / "src").rglob("*.py"))
    paths += sorted((root / "tests").rglob("*.py"))
    paths += [root / "scripts/r3_sqlite_phases.py", Path(__file__)]
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


class Observers:
    def __init__(self):
        self.probes = []
        self.errors = []

    def attach(self, store):
        # Observer errors must never replace the original constructor outcome.
        try:
            if len(self.probes) >= MAX_STORES:
                raise OverflowError("store observation capacity")
            probe = SqlitePhases()  # Minimal timing; no transaction-state getters.
            self.probes.append(probe)
            probe.install(store)
        except BaseException as error:
            self.recordError(error)

    def recordError(self, error):
        if len(self.errors) < MAX_STORES:
            self.errors.append(type(error).__name__)

    def retire(self):
        rows = []
        for probe in self.probes:
            try:
                rows.append(probe.snapshot())
            except BaseException as error:
                self.recordError(error)
            finally:
                try:
                    probe.close()
                except BaseException as error:
                    self.recordError(error)
        return rows


def completeOutcomes(outcomes, exitstatus):
    return (set(outcomes) == {"setup", "call", "teardown"}
        and outcomes["setup"]["outcome"] == outcomes["teardown"]["outcome"] == "passed"
        and ((outcomes["call"]["outcome"] == "passed" and exitstatus == 0)
             or (outcomes["call"]["outcome"] == "failed" and exitstatus == 1)))


def pytest_addoption(parser):
    parser.addoption("--join-sqlite-report", help="New diagnostic JSON output file")


def pytest_configure(config):
    output = config.getoption("--join-sqlite-report")
    if output:
        if Path(output).exists():
            raise pytest.UsageError("diagnostic output must be a new file")
        config._join_sqlite = {"role": "diagnostic_only", "target": TARGET,
            "outcomes": {}, "observers": Observers(),
            "source_before": sourceHashes(Path(config.rootpath)),
            "python": platform.python_version(), "platform": platform.system(),
            "limitations": "One exact original test, not full-suite order or acceptance. "
                "Timing perturbs scheduling. Parent-process stores only; no child instrumentation. "
                "Totals include initialization and cleanup. Nested phase totals are not additive. "
                "No SQL parameters, database contents, paths, credentials, or environment dump. "
                "Commit timing cannot distinguish filesystem, scheduling, or native SQLite waits."}
        sha = os.environ.get("GITHUB_SHA", "")
        config._join_sqlite["github_sha"] = sha if re.fullmatch("[0-9a-f]{40}", sha) else None


def pytest_collection_modifyitems(config, items):
    if hasattr(config, "_join_sqlite") and [item.nodeid for item in items] != [TARGET]:
        raise pytest.UsageError("diagnostic requires exactly the original 300-join test")


@pytest.fixture(autouse=True)
def observeJoinStore(request, monkeypatch):
    state = getattr(request.config, "_join_sqlite", None)
    if state is None or request.node.nodeid != TARGET:
        yield
        return
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    original = SqliteStore.__init__

    def initialize(store, *args, **kwargs):
        original(store, *args, **kwargs)
        state["observers"].attach(store)

    monkeypatch.setattr(SqliteStore, "__init__", initialize)
    try:
        yield
    finally:
        state["stores"] = state["observers"].retire()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    result = yield
    state = getattr(item.config, "_join_sqlite", None)
    if state is not None and item.nodeid == TARGET:
        report = result.get_result()
        state["outcomes"][report.when] = {"outcome": report.outcome,
                                          "duration_seconds": report.duration}


def pytest_sessionfinish(session, exitstatus):
    state = getattr(session.config, "_join_sqlite", None)
    if state is None:
        return
    observers = state.pop("observers")
    state["observer_errors"] = observers.errors
    state["observer_retired"] = all(probe.patch is None for probe in observers.probes)
    state["source_stable"] = state["source_before"] == sourceHashes(Path(session.config.rootpath))
    state["original_pytest_exit"] = int(exitstatus)
    state["valid"] = (state["observer_retired"] and state["source_stable"]
        and not state["observer_errors"] and bool(state.get("stores"))
        and completeOutcomes(state["outcomes"], exitstatus)
        and all(row["enabled"] and not row["errors"] and not row["active"]
                for row in state.get("stores", [])))
    encoded = json.dumps(state, indent=2, sort_keys=True) + "\n"
    if len(encoded.encode()) > MAX_BYTES:
        raise RuntimeError("diagnostic report exceeded bounded size")
    with Path(session.config.getoption("--join-sqlite-report")).open("x", encoding="utf-8") as output:
        output.write(encoded)
