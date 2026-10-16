"""Opt-in diagnostic for real parent/worker SQLite calls, never production code.

pytest -p scripts.acceptance_sqlite_ledger --sqlite-ledger NEW_DIRECTORY ...
No timeout/retry/body/COMMIT policy changes. No SQL text or parameter values are
retained. Entries are buffered, bounded and flushed after producers retire.
Instrumentation perturbs scheduling; passing here does NOT close a historical
busy failure. Uninstrumented connections, native checkpoints/VFS and cursor SQL
are outside scope. Missing process ledgers or omitted rows invalidate coverage.
"""
from contextlib import contextmanager
from functools import partial
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest

from scripts.r3_sqlite_phases import _Connection


class Ledger:
    details = True
    limit = 250_000

    def __init__(self, directory, jobId=""):
        self.directory, self.jobId = Path(directory), jobId
        self.processToken = uuid4().hex
        self.rows, self.errors = [], []
        self.lock = threading.Lock()
        self.local = threading.local()
        self.serial = self.omitted = 0

    def connectionToken(self):
        return uuid4().hex

    def _disable(self, error):
        with self.lock:
            if len(self.errors) < 16:
                self.errors.append(type(error).__name__)

    def connectionCall(self, phase, operation, connection, context, *args, **kwargs):
        # Executed in the thread that owns this native handle. in_transaction is
        # a native property, not additional SQL or a speculative lock sample.
        with self.lock:
            self.serial += 1
            call = self.serial
        row = dict(call=call, phase=phase, connection=context["connection_operation_token"],
            threadId=threading.get_native_id(), jobId=getattr(self.local, "jobId", self.jobId),
            successfulBegin=context["successful_begin_call_id_at_entry"],
            before=self.transactionState(connection), startNs=time.perf_counter_ns())
        try:
            result = operation(*args, **kwargs)
            row["outcome"] = "returned"
            return result, call
        except BaseException as error:
            row.update(outcome="raised", errorType=type(error).__name__,
                sqliteCode=getattr(error, "sqlite_errorcode", None),
                sqliteName=getattr(error, "sqlite_errorname", None))
            raise
        finally:
            row["endNs"] = time.perf_counter_ns()
            row["after"] = None if phase == "explicit_close" else self.transactionState(connection)
            self.record(row)

    def transactionState(self, connection):
        try:
            return connection.in_transaction
        except Exception as error:
            self._disable(error)
            return None

    def record(self, row):
        with self.lock:
            if len(self.rows) < self.limit:
                self.rows.append(row)
            else:
                self.omitted += 1

    def flush(self):
        value = dict(schema=1, pid=os.getpid(), processToken=self.processToken, jobId=self.jobId,
            clock="perf_counter_ns", omitted=self.omitted, observerErrors=self.errors,
            limitations=__doc__, rows=self.rows)
        path = self.directory / f"{os.getpid()}-{self.processToken}.json"
        with path.open("x", encoding="utf-8") as output:
            json.dump(value, output)


@contextmanager
def installed(directory, jobId=""):
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    ledger = Ledger(directory, jobId)
    connect, append = SqliteStore._connect, SqliteStore.appendJobEvent

    def observedConnect(store):
        native = connect(store)
        adapter = _Connection(native, ledger)
        ledger.record(dict(phase="connection_open", connection=adapter.operationToken,
            database=str(store.dbPath.resolve()), pid=os.getpid(), jobId=jobId,
            threadId=threading.get_native_id(), atNs=time.perf_counter_ns()))
        return adapter

    def observedAppend(store, *args, **kwargs):
        previous = getattr(ledger.local, "jobId", jobId)
        ledger.local.jobId = kwargs.get("jobId", args[0] if args else jobId)
        try:
            return append(store, *args, **kwargs)
        finally:
            ledger.local.jobId = previous

    SqliteStore._connect, SqliteStore.appendJobEvent = observedConnect, observedAppend
    try:
        yield ledger
    finally:
        SqliteStore._connect, SqliteStore.appendJobEvent = connect, append
        ledger.flush()


def observedWorker(spec, cancelEvent, eventQueue, *, directory):
    from emo_master.apps.runtime.jobs.worker_main import runJobProcess
    with installed(directory, spec.jobId):
        return runJobProcess(spec, cancelEvent, eventQueue)


def pytest_addoption(parser):
    parser.addoption("--sqlite-ledger", help="New private output directory for parent and spawned worker ledgers")


@pytest.fixture(scope="session", autouse=True)
def sqliteLedger(request):
    directory = request.config.getoption("sqlite_ledger")
    if not directory:
        yield
        return
    path = Path(directory).resolve()
    path.mkdir(parents=True, exist_ok=False)
    from emo_master.apps.runtime.jobs import supervisor
    original = supervisor.runJobProcess
    supervisor.runJobProcess = partial(observedWorker, directory=str(path))
    try:
        with installed(path):
            yield
    finally:
        supervisor.runJobProcess = original
