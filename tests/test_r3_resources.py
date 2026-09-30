"""Native offline measurement counters; no device or field-SLA assertion."""
import os

from scripts.r3_resources import resources


def testCurrentProcessResourceSnapshotHasNativeOwnershipCounters():
    result = resources()
    assert result["pid"] == os.getpid()
    if result["status"] == "NOT_RUN":
        assert result["reason"]
        return
    assert result["rss_bytes"] > 0
    assert result["peak_rss_bytes"] >= result["rss_bytes"]
    assert result["handles"] >= 0
    assert result["native_threads"] >= 1
    assert result["python_threads"] >= 1
    assert result["cpu_seconds"] >= 0
