"""Exact Designer first-run observer; unchanged test and ten-second watchdog.

The failure snapshot runs only after the original wait raises and before test
cleanup. It never inspects frame locals, source lines, SQL arguments or payloads.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import sys

import pytest

from scripts import join_sqlite_diagnostic as base

TARGET = ("tests/designer/test_node_image_viewer.py::"
          "testTwoRealRunsFollowSelectionWithoutExtraReadsAndViewerCloseKeepsRuntime")
MAX_THREADS = 32
MAX_FRAMES = 32


def sourceHashes(root):
    result = base.sourceHashes(root)
    path = Path(__file__)
    result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def threadStacks(root):
    frames = sys._current_frames()
    rows = []
    for ident, frame in list(frames.items())[:MAX_THREADS]:
        stack = []
        while frame is not None and len(stack) < MAX_FRAMES:
            path = Path(frame.f_code.co_filename)
            try:
                filename = str(path.relative_to(root))
            except ValueError:
                filename = path.name  # No external absolute paths.
            stack.append({'file': filename, 'line': frame.f_lineno,
                          'function': frame.f_code.co_name})
            frame = frame.f_back
        rows.append({'thread_id': ident, 'frames': stack,
                     'frames_truncated': frame is not None})
    return {'threads': rows, 'threads_truncated': len(frames) > MAX_THREADS}


def observedWait(original, capture):
    def wait(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except AssertionError:
            try:
                capture()
            except BaseException:
                pass  # A diagnostic failure cannot replace the original error.
            raise
    return wait


def captureFailure(state, root):
    if 'before_cleanup' in state:
        return
    try:
        state['before_cleanup'] = {
            'trigger': 'original_wait_AssertionError',
            'stores': [probe.snapshot() for probe in state['observers'].probes],
            'stacks': threadStacks(root)}
    except BaseException as error:
        state['observers'].recordError(error)


def pytest_addoption(parser):
    parser.addoption('--designer-sqlite-report', help='New bounded diagnostic JSON file')


def pytest_configure(config):
    output = config.getoption('--designer-sqlite-report')
    if output:
        if Path(output).exists():
            raise pytest.UsageError('diagnostic output must be new')
        config._designer_sqlite = {'role': 'diagnostic_only', 'target': TARGET,
            'outcomes': {}, 'observers': base.Observers(),
            'source_before': sourceHashes(Path(config.rootpath)),
            'python': platform.python_version(), 'platform': platform.system(),
            'limitations': 'One original Designer test; not full-suite order or acceptance. '
                'Parent-process SQLite only. Totals include initialization and cleanup; '
                'nested phase totals are not additive. A stack is a single instant, not '
                'proof of a continuous wait. No frame locals, source lines, SQL parameters, '
                'database payloads, or environment dump. Timing perturbs scheduling. '
                'Commit timing does not identify native filesystem waits. '
                'Failure snapshot only covers AssertionError from original wait, not other failures.'}


def pytest_collection_modifyitems(config, items):
    if hasattr(config, '_designer_sqlite') and [i.nodeid for i in items] != [TARGET]:
        raise pytest.UsageError('diagnostic requires exactly the original Designer test')


@pytest.fixture(autouse=True)
def observeDesignerStore(request, monkeypatch):
    state = getattr(request.config, '_designer_sqlite', None)
    if state is None or request.node.nodeid != TARGET:
        yield
        return
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    from tests.designer import test_node_image_viewer as target
    original = SqliteStore.__init__

    def initialize(store, *args, **kwargs):
        original(store, *args, **kwargs)
        state['observers'].attach(store)

    monkeypatch.setattr(SqliteStore, '__init__', initialize)
    monkeypatch.setattr(target, 'wait', observedWait(target.wait,
        lambda: captureFailure(state, Path(request.config.rootpath))))
    try:
        yield
    finally:
        state['stores'] = state['observers'].retire()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    result = yield
    state = getattr(item.config, '_designer_sqlite', None)
    if state is not None and item.nodeid == TARGET:
        report = result.get_result()
        state['outcomes'][report.when] = {'outcome': report.outcome,
                                         'duration_seconds': report.duration}


def pytest_sessionfinish(session, exitstatus):
    state = getattr(session.config, '_designer_sqlite', None)
    if state is None:
        return
    observers = state.pop('observers')
    state['observer_errors'] = observers.errors
    state['observer_retired'] = all(p.patch is None for p in observers.probes)
    state['source_stable'] = state['source_before'] == sourceHashes(Path(session.config.rootpath))
    state['original_pytest_exit'] = int(exitstatus)
    state['valid'] = (state['observer_retired'] and state['source_stable']
        and not state['observer_errors'] and bool(state.get('stores'))
        and base.completeOutcomes(state['outcomes'], exitstatus)
        and all(row['enabled'] and not row['errors'] and not row['active']
                for row in state.get('stores', [])))
    state['failure_snapshot_available'] = 'before_cleanup' in state
    encoded = json.dumps(state, indent=2, sort_keys=True) + '\n'
    if len(encoded.encode()) > base.MAX_BYTES:
        raise RuntimeError('diagnostic report exceeded bounded size')
    with Path(session.config.getoption('--designer-sqlite-report')).open('x', encoding='utf-8') as output:
        output.write(encoded)
