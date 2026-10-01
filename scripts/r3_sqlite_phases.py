"""Opt-in call timings for one store; no added SQL, connection or close.

The adapter forwards the store's native connection context manager, which commits
or rolls back but does not close. Native cursor objects and failures are returned
unchanged. It does not intercept native finalization, busy handlers, VFS calls or
automatic checkpoints, so commit time alone cannot distinguish those causes.
"""
from collections import deque
import sqlite3
import threading
import time
import weakref


BUCKETS_NS = (1_000_000, 10_000_000, 100_000_000, 1_000_000_000, 5_000_000_000)
_MISSING_ATTRIBUTE = object()


def sqlPhase(sql):
    # Classify only a bounded SQL prefix; never retain SQL or its arguments.
    prefix = " ".join(sql[:256].split()).upper()
    if prefix == "BEGIN IMMEDIATE":
        return "begin_immediate"
    if prefix.startswith("SELECT") and "MAX(SEQUENCE)" in prefix:
        return "sequence_select"
    if prefix.startswith("INSERT INTO JOBEVENTS"):
        return "event_insert"
    command = prefix.split(" ", 1)[0]
    return command.lower() if command in {"SELECT", "INSERT", "UPDATE", "DELETE", "PRAGMA", "VACUUM", "BEGIN"} else "execute_other"


class SqlitePhases:
    MAX_ACTIVE = 32
    MAX_STAGES = 24
    MAX_SLOW = 16
    SLOW_NS = 100_000_000

    def __init__(self, *, details=False):
        self.lock = threading.Lock()
        self.stages = {}
        self.active = {}
        self.slow = deque(maxlen=self.MAX_SLOW)
        self.slowCount = 0
        self.slowEvicted = 0
        self.errors = deque(maxlen=8)
        self.enabled = True
        self.callId = 0
        self.patch = None
        self.details = details
        self.connectionSerial = 0

    def connectionToken(self):
        with self.lock:
            self.connectionSerial += 1
            return self.connectionSerial

    def _disable(self, error):
        # A diagnostic failure may disable future observations, never the call.
        self.enabled = False
        try:
            self.errors.append(type(error).__name__)
        except BaseException:
            pass

    def _begin(self, phase):
        ident = threading.get_ident()
        start, cpu = time.monotonic_ns(), time.thread_time_ns()
        with self.lock:
            if phase not in self.stages:
                if len(self.stages) >= self.MAX_STAGES:
                    raise OverflowError("diagnostic stage capacity")
                self.stages[phase] = {"entered": 0, "returned": 0, "failed": 0,
                    "wall_ns": 0, "thread_cpu_ns": 0, "max_wall_ns": 0,
                    "histogram": [0] * (len(BUCKETS_NS) + 1)}
            previous = self.active.get(ident)
            if previous is None and len(self.active) >= self.MAX_ACTIVE:
                raise OverflowError("diagnostic active capacity")
            self.callId += 1
            row = {"call_id": self.callId, "phase": phase, "thread_id": ident,
                   "native_thread_id": threading.get_native_id(), "start_ns": start}
            self.active[ident] = row
            self.stages[phase]["entered"] += 1
        return row, cpu, previous

    def _finish(self, token, failure):
        active, cpu, previous = token
        wall, used = time.monotonic_ns() - active["start_ns"], time.thread_time_ns() - cpu
        with self.lock:
            row = self.stages[active["phase"]]
            row["failed" if failure is not None else "returned"] += 1
            row["wall_ns"] += wall
            row["thread_cpu_ns"] += used
            row["max_wall_ns"] = max(row["max_wall_ns"], wall)
            if self.details and "connection_operation_token" in active:
                perf = active.get("call_perf_wall_ns")
                if isinstance(perf, int):
                    row["call_perf_ns"] = row.get("call_perf_ns", 0) + perf
                    row["call_perf_count"] = row.get("call_perf_count", 0) + 1
                    row["call_perf_max_ns"] = max(row.get("call_perf_max_ns", perf), perf)
                else:
                    row["call_perf_unavailable"] = row.get("call_perf_unavailable", 0) + 1
            bucket = next((index for index, upper in enumerate(BUCKETS_NS) if wall <= upper), len(BUCKETS_NS))
            row["histogram"][bucket] += 1
            if wall >= self.SLOW_NS or failure is not None:
                self.slowCount += 1
                self.slowEvicted += int(len(self.slow) == self.MAX_SLOW)
                detail = dict(active, wall_ns=wall, thread_cpu_ns=used, failed=failure is not None)
                if failure is not None:
                    detail["error_type"] = type(failure).__name__
                    code = getattr(failure, "sqlite_errorcode", None)
                    if isinstance(code, int):
                        detail["sqlite_errorcode"] = code
                self.slow.append(detail)
            if previous is None:
                self.active.pop(active["thread_id"], None)
            else:
                self.active[active["thread_id"]] = previous

    def call(self, phase, operation, *args, **kwargs):
        return self._invoke(phase, operation, args, kwargs)

    def connectionCall(self, phase, operation, connection, context, *args, **kwargs):
        result = self._invoke(phase, operation, args, kwargs, context, connection)
        return result, context.get("call_id")

    def _invoke(self, phase, operation, args, kwargs, context=None, connection=None):
        token = None
        if self.enabled:
            try:
                token = self._begin(phase)
            except BaseException as error:
                self._disable(error)
        detail = token[0] if token is not None and context is not None else None
        if detail is not None:
            try:
                context["call_id"] = detail["call_id"]
                detail.update(context)
                detail["in_transaction_before"] = _transactionReading(connection, context)
            except BaseException as error:
                detail["in_transaction_before"] = {"status": "UNAVAILABLE", "error_type": type(error).__name__}
        failure = None
        try:
            if detail is not None and self.enabled:
                detail["call_perf_start_ns"] = _perfReading()
            return operation(*args, **kwargs)
        except BaseException as error:
            failure = error
            raise
        finally:
            if detail is not None and self.enabled:
                detail["call_perf_end_ns"] = _perfReading()
                start, end = detail.get("call_perf_start_ns"), detail["call_perf_end_ns"]
                detail["call_perf_wall_ns"] = end-start if isinstance(start, int) and isinstance(end, int) else None
                try:
                    detail["in_transaction_after"] = (
                        {"status": "UNAVAILABLE", "reason": "close_operation"}
                        if phase == "explicit_close" else _transactionReading(connection, context))
                except BaseException as error:
                    detail["in_transaction_after"] = {"status": "UNAVAILABLE", "error_type": type(error).__name__}
            if token is not None and self.enabled:
                try:
                    self._finish(token, failure)
                except BaseException as error:
                    self._disable(error)

    def install(self, store):
        if self.patch is not None:
            raise RuntimeError("diagnostic already installed")
        original = store._connect
        ownOriginal = vars(store).get("_connect", _MISSING_ATTRIBUTE)

        def connect(*args, **kwargs):
            connection = self.call("connect_configure", original, *args, **kwargs)
            try:
                return _Connection(connection, self)
            except BaseException as error:
                self._disable(error)
                return connection

        # Keep provenance, not a resolved bound method. Restoring the latter
        # onto an instance would leave a new self -> bound-method -> self cycle.
        # Register before assignment so a setter that mutates then raises can
        # still be retired by close().
        self.patch = store, ownOriginal, connect
        store._connect = connect

    def snapshot(self):
        try:
            with self.lock:
                stamp = time.monotonic_ns()
                result = {"enabled": self.enabled, "observed_ns": stamp,
                    "active": [dict(row, elapsed_ns=stamp-row["start_ns"]) for row in self.active.values()] if self.enabled else [],
                    "stages": {phase: dict(row, histogram=list(row["histogram"])) for phase, row in self.stages.items()},
                    "slow_calls": list(self.slow), "errors": list(self.errors),
                    "slow_or_failed_call_count": self.slowCount,
                    "evicted_slow_or_failed_calls": self.slowEvicted,
                    "histogram_upper_bounds_ns": BUCKETS_NS,
                    "limitations": "Cumulative phase totals cover all calls to this store, across every Job. They are not per-Job totals. SQL and outer event snapshots are separate, non-atomic observations and are not joined. Matching thread_id alone does not establish Job/event attribution. Call timings only; connect includes unchanged PRAGMA setup. No extra SQL, native finalization, busy-handler, checkpoint or filesystem tracing. Commit wall time is not proof of any specific native wait. CPU is measured only when a call returns. No SQL text or parameters."}
                if self.details:
                    result["connection_detail_limitations"] = (
                        "Connection tokens identify observed adapter operations, not native-live handles. "
                        "The successful BEGIN anchor is a prior observed call, not a transaction count or lock owner. "
                        "in_transaction is native autocommit state; false does not exclude an active SELECT reader. "
                        "QPC endpoints bracket the forwarded Python call, including possible scheduling/GIL return delay. "
                        "Original monotonic/CPU windows include observation overhead without subtraction. "
                        "executescript and native cursor operations may contain unobserved transactions.")
                return result
        except BaseException as error:
            self._disable(error)
            return {"enabled": False, "errors": list(self.errors)}

    def close(self):
        if self.patch is not None:
            store, ownOriginal, replacement = self.patch
            if vars(store).get("_connect", _MISSING_ATTRIBUTE) is replacement:
                if ownOriginal is _MISSING_ATTRIBUTE:
                    delattr(store, "_connect")
                else:
                    store._connect = ownOriginal
            self.patch = None


class _Connection:
    """No destructor and no native owner retained by the observer."""
    def __init__(self, connection, observer):
        self.connection, self.observer = connection, observer
        if observer.details:
            self.operationToken = observer.connectionToken()
            self.operationThreadId = threading.get_ident()
            self.operationThreadRef = weakref.ref(threading.current_thread())
            self.beginAnchor = None

    def _call(self, phase, operation, *args, **kwargs):
        if not self.observer.details:
            return self.observer.call(phase, operation, *args, **kwargs)
        context = {"connection_operation_token": self.operationToken,
                   "connection_operation_thread_id": self.operationThreadId,
                   "creating_thread_matches": self.operationThreadRef() is threading.current_thread(),
                   "successful_begin_call_id_at_entry": self.beginAnchor}
        try:
            result, callId = self.observer.connectionCall(
                phase, operation, self.connection, context, *args, **kwargs)
            if phase == "begin_immediate":
                self.beginAnchor = callId
            return result
        finally:
            if phase in {"commit", "rollback", "context_exit", "executescript", "explicit_close", "begin"}:
                # An observed boundary can hide implicit work or fail. Do not
                # infer that the native transaction ended or remained alive.
                self.beginAnchor = None

    def execute(self, sql, *args, **kwargs):
        try:
            phase = sqlPhase(sql)
            if self.observer.details and phase == "pragma" and " ".join(sql[:256].split()).upper().startswith("PRAGMA WAL_CHECKPOINT"):
                phase = "explicit_checkpoint"
        except BaseException as error:
            self.observer._disable(error)
            phase = "execute_other"
        return self._call(phase, self.connection.execute, sql, *args, **kwargs)

    def executescript(self, *args, **kwargs):
        return self._call("executescript", self.connection.executescript, *args, **kwargs)

    def commit(self, *args, **kwargs):
        return self._call("commit", self.connection.commit, *args, **kwargs)

    def rollback(self, *args, **kwargs):
        return self._call("rollback", self.connection.rollback, *args, **kwargs)

    def close(self, *args, **kwargs):
        return self._call("explicit_close", self.connection.close, *args, **kwargs)

    def __enter__(self):
        self._call("context_enter", self.connection.__enter__)
        return self

    def __exit__(self, *args):
        return self._call("context_exit", self.connection.__exit__, *args)

    def __getattr__(self, name):
        return getattr(self.connection, name)


def _perfReading():
    try:
        return time.perf_counter_ns()
    except BaseException:
        return None


def _transactionReading(connection, context):
    if type(connection) is not sqlite3.Connection:
        return {"status": "UNAVAILABLE", "reason": "not_exact_native_connection"}
    if not context["creating_thread_matches"]:
        return {"status": "UNAVAILABLE", "reason": "different_operation_thread"}
    try:
        return {"status": "OBSERVED", "value": connection.in_transaction}
    except BaseException as error:
        return {"status": "UNAVAILABLE", "error_type": type(error).__name__}
