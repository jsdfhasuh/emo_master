"""Soak measurements must not turn native observer latency into GUI latency."""
import math
import threading
import time
from types import SimpleNamespace

import pytest

from scripts import r3_soak
from scripts.r3_soak import SoakSampler, postCleanupResources, retireOwned


class Rows:
    def __init__(self):
        self.rows = []

    def write(self, kind, value):
        self.rows.append((kind, value))


def nativeValues(pid):
    return {"pid": pid, "status": "OBSERVED", "rss_bytes": 123456, "native_threads": 7,
            "handles": 9, "cpu_seconds": 1.25}


def testSlowNativeSamplingNeverWaitsOnTheGuiObserverAndReportsCoalescence():
    entered, release = threading.Event(), threading.Event()
    threads = []

    def slow(pid):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(2), "the observer blocked waiting for its native sampler"
        return nativeValues(pid)

    sampler, evidence = SoakSampler(slow), Rows()
    try:
        values, request = sampler.observe({"owner": 1, "job": 2}, evidence)
        assert request["request_admitted"]
        assert values["owner"]["status"] == "SAMPLE_PENDING"
        assert values["owner"]["sample_age_ms"] is None
        assert entered.wait(1)
        values, request = sampler.observe({"owner": 1, "job": 2}, evidence)
        assert not request["request_admitted"]
        assert values["job"]["status"] == "SAMPLE_PENDING"
        assert sampler.report()["coalesced"] == 1
        assert sampler.sampler.pending is None and sampler.sampler.busy
        release.set()
        sampler.sampler.waitIdle()
        sampler.close()
        sampler.flush(evidence)
        assert len(evidence.rows) == 1
        assert all(thread != threading.get_ident() for thread in threads)
        assert sampler.report()["resource_coverage_complete"]
        assert sampler.report()["retired"]
    finally:
        release.set()
        sampler.close()


def testCachedSamplesRetainNativeMetricsTimestampsAndNeverReuseAnotherPid():
    sampler, evidence = SoakSampler(nativeValues), Rows()
    try:
        sampler.observe({"owner": 1}, evidence)
        sampler.sampler.waitIdle()
        first = sampler.flush(evidence)[0]
        values, _ = sampler.observe({"owner": 1, "job": 2}, evidence)
        owner = values["owner"]
        for key, value in nativeValues(1).items():
            assert owner[key] == value
        assert owner["cached"]
        assert owner["sample_start_ns"] <= owner["sample_end_ns"] <= owner["observed_ns"]
        assert owner["sample_age_ms"] == (owner["observed_ns"] - owner["sample_end_ns"]) / 1e6
        assert owner["sample_requested_ns"] >= first["requested_ns"]
        sampler.sampler.waitIdle()
        values, _ = sampler.observe({"owner": 3}, evidence)
        # A batch may finish immediately; the old owner's PID must never leak.
        assert values["owner"]["pid"] == 3
        assert values["owner"]["status"] in {"SAMPLE_PENDING", "OBSERVED"}
        sampler.close()
        sampler.flush(evidence)
        sampler.flush(evidence)
        report = sampler.report()
        assert len(evidence.rows) == report["completed_samples"] == report["logged_samples"]
        assert report["resource_coverage_complete"]
        assert "samples" not in report  # native batches are streamed once, not duplicated
    finally:
        sampler.close()


def testSoakSamplerRetainsTheWholeMaximumAllowedObservationWindow():
    # Original loop admits at t=0, then no faster than the chosen interval, and
    # rejects elapsed > seconds+45 before requesting another sample.
    for seconds in (5, 1800):
        for interval in (1, 1.5, 30):
            assert math.floor((seconds + 45) / interval) + 1 <= SoakSampler.MAX_SAMPLES
    sampler, evidence = SoakSampler(nativeValues), Rows()
    try:
        for _ in range(SoakSampler.MAX_SAMPLES):
            assert sampler.sampler.request({"owner": 1})
            sampler.sampler.waitIdle()
        rows = sampler.flush(evidence)
        assert len(rows) == SoakSampler.MAX_SAMPLES == 1846
        assert rows[0]["processes"]["owner"]["rss_bytes"] == 123456
        assert not sampler.sampler.request({"owner": 1})
        assert sampler.report()["overflow"] == 1
        assert len(sampler.sampler.snapshot()) == SoakSampler.MAX_SAMPLES
        assert len(evidence.rows) == SoakSampler.MAX_SAMPLES
    finally:
        sampler.close()


@pytest.mark.parametrize("status", ["NOT_RUN", "EXITED_OR_UNAVAILABLE", "ERROR"])
def testUnavailableNativeMetricsRemainVisibleAndCannotClaimCoverage(status):
    def unavailable(pid):
        if status == "EXITED_OR_UNAVAILABLE":
            raise OSError("injected unavailable PID")
        if status == "ERROR":
            raise ValueError("injected native failure")
        return {"pid": pid, "status": status, "reason": "unsupported platform"}

    sampler, evidence = SoakSampler(unavailable), Rows()
    try:
        sampler.observe({"owner": 1}, evidence)
        sampler.close()
        sampler.flush(evidence)
        report = sampler.report()
        assert not report["resource_coverage_complete"]
        assert report["unobserved_processes"] == [{"role": "owner", "pid": 1}]
        assert evidence.rows[0][1]["processes"]["owner"]["status"] == status
        assert bool(report["errors"]) == (status == "ERROR")
    finally:
        sampler.close()


def testCoalescedProcessIdentityDoesNotMasqueradeAsObservedCoverage():
    entered, release = threading.Event(), threading.Event()

    def held(pid):
        entered.set()
        assert release.wait(2)
        return nativeValues(pid)

    sampler, evidence = SoakSampler(held), Rows()
    try:
        sampler.observe({"owner": 1}, evidence)
        assert entered.wait(1)
        _, request = sampler.observe({"owner": 1, "job": 2}, evidence)
        assert not request["request_admitted"]
        release.set()
        sampler.close()
        sampler.flush(evidence)
        report = sampler.report()
        assert not report["resource_coverage_complete"]
        assert report["unobserved_processes"] == [{"role": "job", "pid": 2}]
    finally:
        release.set()
        sampler.close()


def testFailedIndependentOwnerStillRetiresSamplerBeforeFreshCleanupRead(monkeypatch):
    calls = []
    sampler = SoakSampler(nativeValues)

    def fail():
        calls.append("client")
        raise RuntimeError("injected close failure")

    def native(pid=None):
        assert sampler.report()["retired"]
        calls.append("fresh-native-read")
        return nativeValues(pid or 1)

    monkeypatch.setattr(r3_soak, "resources", native)
    try:
        with pytest.raises(RuntimeError, match="must retire"):
            postCleanupResources(sampler)
        assert calls == []
        runtime = SimpleNamespace(StopJob=lambda request, context: calls.append("stop"),
                                  close=lambda: calls.append("runtime"))
        errors = retireOwned([], [SimpleNamespace(close=fail)], runtime,
            SimpleNamespace(close=lambda: calls.append("server")), "job", sampler=sampler)
        assert calls == ["client", "stop", "server", "runtime"]
        assert errors[0]["owner"] == "client-0"
        owner = postCleanupResources(sampler)
        assert owner["sampler_retired"] and not owner["cached"]
        assert owner["sample_start_ns"] <= owner["sample_end_ns"] <= time.perf_counter_ns()
        assert calls[-1] == "fresh-native-read"
    finally:
        sampler.close()


def testFailedSamplerJoinRemainsAnOwnedCleanupFailure():
    def fail():
        raise RuntimeError("still enumerating")

    errors = retireOwned([], [], None, None, "", sampler=SimpleNamespace(close=fail))
    assert errors == [{"owner": "resource-sampler", "error": "RuntimeError('still enumerating')"}]


def testEvidenceRetainsTheExisting32MiBHardLimit(tmp_path):
    path = tmp_path / "bounded.jsonl"
    evidence = r3_soak.Evidence(path)
    try:
        evidence.bytes = 32 * 1024 * 1024 - 1
        with pytest.raises(RuntimeError, match="32 MiB"):
            evidence.write("sample", {"processes": {}})
        assert path.stat().st_size == 0
    finally:
        evidence.file.close()
