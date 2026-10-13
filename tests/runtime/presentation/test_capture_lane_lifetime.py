"""Shared raw ownership ends at a verified private copy, before encoding/adoption."""
import queue
import threading
from functools import partial
import multiprocessing
from multiprocessing.shared_memory import SharedMemory
import weakref

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.presentation.collector import ResultCollector
from emo_master.apps.runtime.presentation.exporter import ExportPool, encodeWorker
from tests.runtime.presentation.test_images import waitFor


def gatedEncoder(connection, memoryName, entered, resume):
    """Gate the real child inside imencode, before it reads the input pixels."""
    original = cv2.imencode

    def encode(*args, **kwargs):
        entered.set()
        if not resume.wait(5):
            raise RuntimeError("test must unblock encoding")
        return original(*args, **kwargs)

    cv2.imencode = encode
    try:
        encodeWorker(connection, memoryName)
    finally:
        cv2.imencode = original


@pytest.mark.parametrize("laneCount", [1, 2])
def testNextCaptureWhilePreviousIsStillEncodingPreservesBothPixels(tmp_path, laneCount):
    context = multiprocessing.get_context("spawn")
    entered, resume = context.Event(), context.Event()
    results = {}
    first = np.full((32, 48, 3), 19, np.uint8)
    second = np.full_like(first, 231)

    def callback(task, path, outcome):
        assert outcome == "AVAILABLE", task.get("exportDiagnostic")
        results[task["key"]] = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)

    pool = ExportPool(tmp_path, callback, worker=partial(gatedEncoder, entered=entered, resume=resume))
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
        assert entered.wait(5)
        # Wait for parent acknowledgment handling, never for encoding to finish.
        # This consumes the existing 500ms budget; it does not extend it.
        assert slot["free"].acquire(True, .15), "slow encoder must release shared raw after taking a private snapshot"
        slot["free"].release()
        assert capture("second", second).get("pendingImage")
        assert capture("third", first)["reasonCode"] == "BUDGET_EXCEEDED"
        assert not slot["free"].acquire(False)
        assert not results, "first encoder is still gated when shared raw is overwritten"
        with pytest.raises(ValueError, match="owned"):
            pool.releaseSlot(0)
        resume.set()
        waitFor(lambda: not pool.slots[0]["busy"])
        assert not pool.errors and set(results) == {"first", "second"}, list(pool.diagnostics)
        np.testing.assert_array_equal(results["first"], first)
        np.testing.assert_array_equal(results["second"], second)
        assert slot["free"].acquire(False) and not slot["free"].acquire(False)
        slot["free"].release()
        assert not pool.slots[0]["pending"]
    finally:
        resume.set()
        pool.close()
    assert all(slot["process"] is None and not slot["thread"].is_alive() for slot in pool.slots)


def testTimeoutAfterSnapshotReapsEncoderWithoutReleasingSuccessorReservation(tmp_path):
    context = multiprocessing.get_context("spawn")
    entered, resume = context.Event(), context.Event()
    completed = threading.Event()
    outcomes = []

    def callback(task, path, outcome):
        outcomes.append(outcome)
        assert path is None and pool.slots[0]["process"] is None
        assert not slot["free"].acquire(False), "timeout may not return the successor's raw reservation"
        pool.stop.set()
        completed.set()

    pool = ExportPool(tmp_path, callback, worker=partial(gatedEncoder, entered=entered, resume=resume))
    slot = pool.slots[0]
    try:
        assert slot["free"].acquire(False)
        pool.submit({"slot": 0, "shape": [2, 2, 3], "key": "first"})
        assert entered.wait(5)
        assert slot["free"].acquire(True, .15)
        # A producer has the next reservation but has not submitted it yet.
        slot["memory"].buf[:12] = b"\x71" * 12
        assert completed.wait(5)
        slot["thread"].join(5)
        assert not slot["thread"].is_alive()
        assert not slot["free"].acquire(False)
        assert outcomes == ["EXPORT_TIMEOUT"] and pool.stats["reaped"] == 1
        assert not pool.errors and bytes(slot["memory"].buf[:12]) == b"\x71" * 12
        slot["free"].release()
    finally:
        # A killed child may leave a multiprocessing.Condition wait in flight.
        # Do not notify that retired Event; close reaps any remaining owners.
        pool.close()


@pytest.mark.parametrize("failFirst", [False, True])
def testWorkerKeepsOnlyOneIndependentSnapshotAndDropsItAfterEachTask(tmp_path, monkeypatch, failFirst):
    memory = SharedMemory(create=True, size=12)
    snapshots, replies = [], []
    original = cv2.imencode
    count = [0]

    def encode(extension, pixels):
        assert pixels.flags.owndata and pixels.nbytes == 12
        assert np.all(pixels == count[0]), "ack allowed raw overwrite, but encoding must use the private image"
        snapshots.append(weakref.ref(pixels))
        if failFirst and count[0] == 1:
            raise ValueError("injected codec error")
        return original(extension, pixels)

    class Connection:
        def recv(self):
            assert all(ref() is None for ref in snapshots), "previous input must be freed before allocating the next"
            count[0] += 1
            if count[0] == 3:
                return None
            memory.buf[:12] = bytes([count[0]]) * 12
            return dict(taskId=str(count[0]), shape=[2, 2, 3], path=str(tmp_path / "output.png"))

        def send(self, reply):
            replies.append(reply)
            if isinstance(reply, dict) and reply.get("kind") == "snapshot":
                assert reply["rawBytes"] == 12
                memory.buf[:12] = b"\xff" * 12

        def close(self):
            pass

    monkeypatch.setattr(cv2, "imencode", encode)
    try:
        encodeWorker(Connection(), memory.name)
        assert [r["kind"] for r in replies[1:]] == ["snapshot", "complete", "snapshot", "complete"]
        assert replies[2]["ok"] == (not failFirst) and replies[4]["ok"]
    finally:
        memory.close()
        memory.unlink()


@pytest.mark.parametrize("callbackFails", [False, True])
@pytest.mark.parametrize("laneCount", [1, 2])
def testNextCaptureDuringAdoptionKeepsBothImagesAndOneCredit(tmp_path, monkeypatch, callbackFails, laneCount):
    entered, resume = threading.Event(), threading.Event()
    results, originalBytes, checks = {}, [], []
    first = np.full((32, 48, 3), 19, np.uint8)
    second = np.full_like(first, 231)

    def callback(task, path, outcome):
        assert outcome == "AVAILABLE", task.get("exportDiagnostic")
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
        assert set(results) == {"first", "second"} and checks == [True], list(pool.diagnostics)
        np.testing.assert_array_equal(results["first"], first)
        np.testing.assert_array_equal(results["second"], second)
        assert pool.errors == (["callback: ValueError"] if callbackFails else []), list(pool.diagnostics)
        assert [row["resultKey"] for row in pool.diagnostics] == ["first", "second"]
        assert all(row["snapshotReplyNs"] <= row["laneReleasedNs"] <= row["replyNs"] <= row["callbackStartedNs"]
                   for row in pool.diagnostics)
        assert slot["free"].acquire(False) and not slot["free"].acquire(False)
        slot["free"].release()
        assert not pool.slots[0]["pending"]
        pool.releaseSlot(0)
    finally:
        resume.set()
        pool.close()
    assert all(slot["process"] is None and not slot["thread"].is_alive() for slot in pool.slots)
