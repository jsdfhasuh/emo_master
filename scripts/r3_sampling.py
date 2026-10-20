"""Bounded background native sampling for opt-in measurement observers."""
import os
import threading
import time

from scripts.r3_resources import resources


class BoundedSampler:
    """One owner, one in-flight request, no GUI-native enumeration or backlog."""
    def __init__(self, sample=resources, limit=256):
        self.sample, self.limit = sample, limit
        self.condition = threading.Condition()
        self.pending = None
        self.busy = False
        self.stopping = False
        self.rows = []
        self.requested = self.coalesced = self.overflow = 0
        self.errors = []
        self.thread = threading.Thread(target=self._run, name="r3-policy-resources")
        self.thread.start()

    def request(self, processes, stamp=None):
        with self.condition:
            self.requested += 1
            if self.stopping or self.busy or self.pending is not None:
                self.coalesced += 1
                return False
            if len(self.rows) >= self.limit:
                self.overflow += 1
                return False
            self.pending = (dict(processes), stamp if stamp is not None else time.perf_counter_ns())
            self.condition.notify()
            return True

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending is not None or self.stopping)
                if self.pending is None:
                    return
                processes, requested = self.pending
                self.pending = None
                self.busy = True
            start = time.perf_counter_ns()
            values, timings, boundaries = {}, {}, {}
            for label, pid in processes.items():
                begin = time.perf_counter_ns()
                try:
                    values[label] = self.sample(pid)
                except (OSError, KeyError) as error:
                    values[label] = {"pid": pid, "status": "EXITED_OR_UNAVAILABLE", "error": repr(error)}
                except Exception as error:
                    values[label] = {"pid": pid, "status": "ERROR", "error": repr(error)}
                    with self.condition:
                        if len(self.errors) < self.limit:
                            self.errors.append({"process": label, "error": repr(error)})
                        else:
                            self.overflow += 1
                endProcess = time.perf_counter_ns()
                timings[label] = (endProcess - begin) / 1e6
                boundaries[label] = {"start_ns": begin, "end_ns": endProcess}
            end = time.perf_counter_ns()
            with self.condition:
                self.rows.append({"requested_ns": requested, "start_ns": start, "end_ns": end,
                    "elapsed_ms": (end-start)/1e6, "per_process_ms": timings,
                    "per_process_timestamps": boundaries, "processes": values})
                self.busy = False
                self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return list(self.rows)

    def waitIdle(self, timeout=10):
        with self.condition:
            if not self.condition.wait_for(lambda: not self.busy and self.pending is None, timeout):
                raise TimeoutError("native sampler request is still in flight")

    def sampleNow(self, processes, timeout=10):
        """Fresh bounded acquisition for setup/cleanup, never an active GUI tick."""
        deadline = time.monotonic()+timeout
        self.waitIdle(max(0, deadline-time.monotonic()))
        requested = time.perf_counter_ns()
        if not self.request(processes, requested):
            raise RuntimeError("fresh native sample was not admitted")
        self.waitIdle(max(0, deadline-time.monotonic()))
        row = self.snapshot()[-1]
        if row["requested_ns"] != requested:
            raise RuntimeError("fresh native sample identity mismatch")
        return row

    def report(self):
        with self.condition:
            observed = sorted({label for row in self.rows for label, value in row["processes"].items()
                               if value.get("status") == "OBSERVED"})
            requestedRoles = sorted({label for row in self.rows for label in row["processes"]})
            return {"samples": list(self.rows), "observed_roles": observed,
                "resource_coverage_complete": bool(requestedRoles) and observed == requestedRoles, "requested": self.requested, "coalesced": self.coalesced,
                "overflow": self.overflow, "errors": list(self.errors), "capacity": self.limit,
                "native_enumeration_thread": self.thread.name,
                "retired": not self.thread.is_alive()}

    def close(self, timeout=10):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise RuntimeError("resource sampler still owns native enumeration")


class CachedResources:
    """Per-PID compatibility callable: cached, timestamped, never GUI I/O.

    Existing benchmark callers ask for one PID at a time. Register each observed
    PID into a bounded batch, and explicitly expose pending/stale values rather
    than presenting cached data as a new native sample. This is measurement-only.
    """
    def __init__(self, sample=resources, limit=256, pidLimit=8):
        self.sampler = BoundedSampler(sample, limit)
        self.pids = {}
        self.pidLimit = pidLimit
        self.registryOverflow = 0
        self.nextRequestNs = 0
        self.rateLimitedReads = 0
        self.closed = False

    def __call__(self, pid=None):
        pid = pid or os.getpid()
        stamp = time.perf_counter_ns()
        if pid not in self.pids:
            if len(self.pids) < self.pidLimit:
                self.pids[pid] = pid
            else:
                self.registryOverflow += 1
                return {"pid": pid, "status": "SAMPLER_PID_LIMIT", "observed_ns": stamp}
        if not self.closed:
            if stamp < self.nextRequestNs:
                self.rateLimitedReads += 1
            elif self.sampler.request({str(key): value for key, value in self.pids.items()}, stamp):
                self.nextRequestNs = stamp+400_000_000
        rows = self.sampler.snapshot()
        row = next((item for item in reversed(rows) if str(pid) in item["processes"]), None)
        return self._value(pid, row)

    def _value(self, pid, row):
        stamp = time.perf_counter_ns()
        if row is None:
            return {"pid": pid, "status": "SAMPLE_PENDING", "observed_ns": stamp,
                    "sample_age_ms": None, "sample_end_ns": None}
        boundary = row["per_process_timestamps"][str(pid)]
        return {**row["processes"][str(pid)], "observed_ns": stamp,
                "sample_requested_ns": row["requested_ns"], "sample_start_ns": boundary["start_ns"],
                "sample_end_ns": boundary["end_ns"], "sample_age_ms": (stamp-boundary["end_ns"])/1e6,
                "sample_batch_end_ns": row["end_ns"], "sample_batch_ms": row["elapsed_ms"],
                "cached": True, "sampler_closed": self.closed}

    def sampleNow(self, pid=None, timeout=10):
        """Bounded fresh read outside the active GUI observation window."""
        if self.closed:
            raise RuntimeError("native sampler is closed")
        pid = pid or os.getpid()
        if pid not in self.pids and len(self.pids) >= self.pidLimit:
            raise RuntimeError("native sampler PID registry is full")
        self.pids[pid] = pid
        row = self.sampler.sampleNow({str(pid): pid}, timeout)
        return {**self._value(pid, row), "fresh_request": True}

    def close(self, timeout=10):
        self.sampler.close(timeout)
        self.closed = True

    def report(self):
        report = self.sampler.report()
        covered = set(report["observed_roles"])
        return {**report, "resource_coverage_complete": bool(self.pids) and all(str(pid) in covered for pid in self.pids),
                "registered_pids": list(self.pids),
                "pid_limit": self.pidLimit, "registry_overflow": self.registryOverflow,
                "minimum_request_interval_ms": 400, "rate_limited_cache_reads": self.rateLimitedReads,
                "semantics": "Samples are asynchronous native reads. Cached observations include sample end and age; pending is not a zero/current reading."}
