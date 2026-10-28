"""Temporary opt-in CI observer: creation stacks around the original A18 call.

Runs only on an explicitly enabled Windows Actions runner. The 32 MiB memory
trace records process/thread and loader events, with ThreadCreate stacks. It
does not sample, suspend threads, record file/network traffic or change tests.
Only our randomly named WPR instance can be stopped or cancelled. The trace
is diagnostic, not acceptance; missing stacks or lost events remain unknown.
"""
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import pytest

from scripts.ci_pytest_reports import NATIVE_OWNER_TEST


INVALID = pytest.StashKey()


def enabled():
    return (os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_OS") == "Windows"
            and os.environ.get("EMO_A18_CREATION_TRACE") == "1")


class Trace:
    def __init__(self):
        self.instance = "EmoA18_" + uuid4().hex
        self.root = Path("build/a18-creation-trace") / self.instance
        self.wpr = str(Path(os.environ["SystemRoot"]) / "System32/wpr.exe")
        self.profile = Path(__file__).with_name("a18_thread_creation.wprp").resolve()
        self.record = {"role": "diagnostic_only", "test": NATIVE_OWNER_TEST,
                       "pid": os.getpid(), "instance": self.instance,
                       "commands": [], "errors": [], "retired": False}
        self.attempted = False
        self.started = False

    def command(self, *args):
        row = {"operation": args[0], "unix_ns": time.time_ns(),
               "monotonic_ns": time.monotonic_ns()}
        self.record["commands"].append(row)
        try:
            with (self.root / "wpr.log").open("ab") as stream:
                result = subprocess.run([self.wpr, *args, "-instancename", self.instance],
                    stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                    timeout=30, check=False)
            row["exit_code"] = result.returncode
            return result.returncode == 0
        except BaseException as error:
            row["error_type"] = type(error).__name__
            return False

    def start(self):
        self.root.mkdir(parents=True, exist_ok=False)
        self.attempted = True
        self.started = self.command("-start", str(self.profile) + "!A18Creation.Light")
        if not self.started:
            self.record["errors"].append("start_failed")
        self.record["test_start_unix_ns"] = time.time_ns()
        self.record["test_start_monotonic_ns"] = time.monotonic_ns()

    def finish(self):
        self.record["test_end_unix_ns"] = time.time_ns()
        self.record["test_end_monotonic_ns"] = time.monotonic_ns()
        try:
            if self.started:
                stopped = self.command("-stop", str((self.root / "creation.etl").resolve()),
                                       "A18 original call", "-skipPdbGen")
                self.record["retired"] = stopped
                if not stopped:
                    self.record["errors"].append("stop_failed")
            if self.attempted and not self.record["retired"]:
                self.record["retired"] = self.command("-cancel")
            etl = self.root / "creation.etl"
            self.record["etl_bytes"] = etl.stat().st_size if etl.is_file() else 0
            if not self.record["etl_bytes"]:
                self.record["errors"].append("trace_missing")
        finally:
            (self.root / "metadata.json").write_text(
                json.dumps(self.record, indent=2) + "\n", encoding="utf-8")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    if item.nodeid != NATIVE_OWNER_TEST or not enabled():
        return (yield)
    trace = None
    errors = []
    try:
        try:
            trace = Trace()
            trace.start()
        except BaseException as error:
            errors.append("setup_" + type(error).__name__)
        return (yield)
    finally:
        if trace is not None:
            try:
                trace.finish()
            except BaseException as error:
                errors.append("finish_" + type(error).__name__)
            errors.extend(trace.record["errors"])
            if not trace.record["retired"]:
                errors.append("retirement_unconfirmed")
        if errors:
            item.config.stash[INVALID] = errors
        print("A18_CREATION_TRACE " + json.dumps({"role": "diagnostic_only",
            "path": str(trace.root) if trace else None, "errors": errors}), flush=True)


def pytest_sessionfinish(session):
    errors = session.config.stash.get(INVALID, [])
    if errors:
        print("A18_CREATION_TRACE_INVALID " + json.dumps(errors), flush=True)
        if session.exitstatus == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
