"""Bounded, private crash-resilient pytest journal for diagnostics only."""
from collections import defaultdict
import faulthandler
import json
from pathlib import Path
import threading
import time

import pytest


MIB = 1024 * 1024
PHASE_BYTES = 16 * MIB
FAILURE_BYTES = 2 * MIB
STACK_BYTES = 2 * MIB
STACK_PERIOD_SECONDS = 60


def _short(value, limit):
    return str(value).encode("utf-8", errors="replace")[:limit].decode("utf-8", errors="ignore")


class Journal:
    def __init__(self, path, cap):
        self.file = path.open("xb", buffering=0)
        self.cap = cap
        self.size = 0
        self.full = False

    def write(self, record):
        if self.full:
            return
        line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if self.size + len(line) > self.cap - 128:
            self.full = True
            line = b'{"event":"budget_exhausted"}\n'
        self.file.write(line)
        self.size += len(line)

    def close(self):
        self.file.close()


class Evidence:
    def __init__(self, root):
        self.phases = Journal(root / "pytest-phases.jsonl", PHASE_BYTES)
        self.failures = Journal(root / "pytest-failures.jsonl", FAILURE_BYTES)
        self.stack = (root / "pytest-stacks.log").open("xb", buffering=0)
        self.done = threading.Event()
        self.monitor = None
        self.active_phase = None
        self.sequence = defaultdict(int)
        self._event("configured")
        self.failures.write({"event": "configured"})
        self.stack.write(b"event=configured\n")

    def _event(self, event, **details):
        self.phases.write({"event": event, "monotonic_ns": time.monotonic_ns(), **details})

    def _start(self, item, phase):
        key = (item.nodeid, phase)
        self.sequence[key] += 1
        self.active_phase = (item.nodeid, phase, self.sequence[key])
        self._event("phase_start", nodeid=_short(item.nodeid, 2048), phase=phase,
                    attempt=self.sequence[key])

    def _stacks(self):
        while not self.done.wait(STACK_PERIOD_SECONDS):
            active = self.active_phase
            if self.stack.tell() >= STACK_BYTES - 1024:
                self.stack.write(b"event=budget_exhausted\n")
                return
            self.stack.write((json.dumps({"event": "periodic_stack", "monotonic_ns": time.monotonic_ns(),
                                         "active_phase": [_short(active[0], 512), *active[1:]] if active else None},
                                        ensure_ascii=True) + "\n").encode("ascii"))
            faulthandler.dump_traceback(file=self.stack, all_threads=True)
            if self.stack.tell() > STACK_BYTES:
                self.stack.truncate(STACK_BYTES)
                return

    def pytest_sessionstart(self, session):
        self._event("session_start")
        self.monitor = threading.Thread(target=self._stacks, name="emo-pytest-evidence-stacks", daemon=True)
        self.monitor.start()

    def pytest_collection(self, session):
        self._event("collection_start")

    def pytest_collection_finish(self, session):
        self._event("collection_finish", items=len(session.items))

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item):
        self._start(item, "setup")

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_call(self, item):
        self._start(item, "call")

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_teardown(self, item, nextitem):
        self._start(item, "teardown")

    def pytest_runtest_logreport(self, report):
        record = {"nodeid": _short(report.nodeid, 2048), "phase": report.when,
                  "attempt": self.sequence[(report.nodeid, report.when)], "outcome": report.outcome,
                  "duration_ns": int(report.duration * 1_000_000_000)}
        self._event("phase_end", **record)
        if report.failed:
            self.failures.write({"event": "failure", "monotonic_ns": time.monotonic_ns(), **record,
                                 "detail": _short(report.longreprtext, 4096)})
        self.active_phase = None

    def pytest_collectreport(self, report):
        if report.failed:
            self.failures.write({"event": "collection_failure", "monotonic_ns": time.monotonic_ns(),
                                 "nodeid": _short(report.nodeid, 2048), "detail": _short(report.longreprtext, 4096)})

    def pytest_sessionfinish(self, session, exitstatus):
        self._event("session_finish", exitstatus=int(exitstatus))

    def pytest_unconfigure(self, config):
        self.done.set()
        if self.monitor is not None:
            self.monitor.join(timeout=1)
        self.phases.close()
        self.failures.close()
        # If the daemon is still writing a stack, don't close its descriptor
        # under it. Process teardown closes it instead.
        if self.monitor is None or not self.monitor.is_alive():
            self.stack.close()


def pytest_addoption(parser):
    parser.addoption("--emo-pytest-evidence-dir", type=Path, default=None)


def pytest_configure(config):
    target = config.getoption("--emo-pytest-evidence-dir")
    if target is not None:
        config.pluginmanager.register(Evidence(target), "emo-private-pytest-evidence")
