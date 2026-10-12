"""Raw image ownership ends at verified encoding, independently of adoption."""
import queue
import threading

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.presentation.collector import ResultCollector
from emo_master.apps.runtime.presentation.exporter import ExportPool
from tests.runtime.presentation.test_images import waitFor


@pytest.mark.parametrize("callbackFails", [False, True])
@pytest.mark.parametrize("laneCount", [1, 2])
def testNextCaptureDuringAdoptionKeepsBothImagesAndOneCredit(tmp_path, monkeypatch, callbackFails, laneCount):
    entered, resume = threading.Event(), threading.Event()
    results, originalBytes, checks = {}, [], []
    first = np.full((32, 48, 3), 19, np.uint8)
    second = np.full_like(first, 231)

    def callback(task, path, outcome):
        assert outcome == "AVAILABLE"
        if task["key"] == "first":
            originalBytes.append(path.read_bytes())
            entered.set()
            assert resume.wait(5), "test must unblock adoption"
            checks.append(path.read_bytes() == originalBytes[0])
        results[task["key"]] = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
        if task["key"] == "first" and callbackFails:
            raise ValueError("injected adoption callback failure")

    pool = ExportPool(tmp_path, callback)
    slots = pool.configureLanes(0, laneCount)
    slot = slots[-1]
    collector = ResultCollector({"plan": '{"sources":{},"scopes":{}}', "slots": slots,
        "imageLaneBySource": {"image": laneCount - 1}},
        lambda event: pool.submit(event["descriptor"]))

    def capture(key, pixels):
        return collector.image("image", pixels,
            {"identity": {"resultKey": key, "jobId": "job"}, "rawBytes": 0})

    try:
        assert capture("first", first).get("pendingImage")
        assert entered.wait(5), "first encoder must finish before adoption is gated"
        # File/callback ownership still prevents reassignment of this Job's slab.
        with pytest.raises(ValueError, match="owned"):
            pool.releaseSlot(0)
        with pytest.raises(ValueError, match="owned"):
            pool.configureLanes(0, 2)
        def rejectSubmission(*_):
            raise queue.Full
        with monkeypatch.context() as patch:
            patch.setattr(pool.slots[0]["queue"], "put_nowait", rejectSubmission)
            with pytest.raises(queue.Full):
                capture("rejected-submission", second)
        with pytest.raises(ValueError, match="owned"):
            pool.releaseSlot(0)
        # Actual producer copy is admitted while the old PNG is still owned.
        assert capture("second", second).get("pendingImage"), "completed PNG must not retain raw credit during adoption"
        blocked = capture("third", first)
        assert blocked["reasonCode"] == "BUDGET_EXCEEDED"
        assert not slot["free"].acquire(False), "queued image must retain its one raw reservation"
        resume.set()
        waitFor(lambda: not pool.slots[0]["busy"])
        assert set(results) == {"first", "second"} and checks == [True]
        np.testing.assert_array_equal(results["first"], first)
        np.testing.assert_array_equal(results["second"], second)
        assert pool.errors == (["callback: ValueError"] if callbackFails else [])
        assert [row["resultKey"] for row in pool.diagnostics] == ["first", "second"]
        assert all(row["replyNs"] <= row["laneReleasedNs"] <= row["callbackStartedNs"]
                   for row in pool.diagnostics)
        assert slot["free"].acquire(False) and not slot["free"].acquire(False)
        slot["free"].release()
        assert not pool.slots[0]["pending"]
        pool.releaseSlot(0)
    finally:
        resume.set()
        pool.close()
    assert all(slot["process"] is None and not slot["thread"].is_alive() for slot in pool.slots)
