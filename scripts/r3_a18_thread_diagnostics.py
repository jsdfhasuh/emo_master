"""Opt-in, post-assertion native start-module evidence for the original A18.

python -m pytest -q -p scripts.r3_a18_thread_diagnostics \
  --native-thread-origins NEW.json

Only the exact A18 lifecycle test is wrapped, and its original predicate runs
first. A passing assertion performs no capture. The first failing assertion
produces one bounded, exclusive-create JSON file; its exact exception survives
all diagnostic failures. No output means no captured thread assertion, never
an inferred PASS. Normal CI does not load this plugin.
"""
import json
from pathlib import Path
import time

import pytest

from scripts.r3_native_thread_origins import collect, HELPER_SECONDS, LIMITATIONS, MAX_BYTES, MAX_THREADS


TARGET = ("tests/ui/presentation/test_retained_acceptance_lifecycle.py::"
          "testThousandNavigationsAndThirtyFloatingCyclesRetireNativeOwners")


def pytest_addoption(parser):
    parser.addoption("--native-thread-origins", metavar="NEW.json",
                     help="opt-in post-failure Windows start modules for A18 new native threads")


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
    original = item.module.assertNoNewThreads
    captured = False

    def check(before, after):
        nonlocal captured
        try:
            return original(before, after)
        except AssertionError:
            if not captured:
                captured = True
                try:
                    failedAt = time.time_ns()
                    newIds = sorted(after["native_thread_ids"] - before["native_thread_ids"])
                    record = {"schema_version": 1, "test": TARGET,
                              "trigger": "original_assertNoNewThreads_failed",
                              "failure_unix_ns": failedAt, "new_native_thread_count": len(newIds),
                              "selected_native_thread_ids": newIds[:MAX_THREADS],
                              "omitted_native_thread_count": max(0, len(newIds) - MAX_THREADS),
                              "helper_timeout_seconds": HELPER_SECONDS, "limitations": LIMITATIONS,
                              "evidence": collect(newIds)}
                    content = json.dumps(record, ensure_ascii=True, indent=2) + "\n"
                    if len(content.encode("ascii")) <= MAX_BYTES:
                        with Path(output).open("x", encoding="ascii") as stream:
                            stream.write(content)
                except BaseException:
                    # Observation is subordinate to the already-raised assertion.
                    pass
            raise

    patch = pytest.MonkeyPatch()
    patch.setattr(item.module, "assertNoNewThreads", check)
    try:
        return (yield)
    finally:
        patch.undo()
