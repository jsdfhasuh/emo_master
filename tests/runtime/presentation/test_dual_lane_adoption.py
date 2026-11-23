"""Separate bounded staging ownership from the serial dual-image encoder."""
from pathlib import Path
from threading import Event, Thread, current_thread

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.presentation.exporter import ExportPool
from tests.runtime.presentation.test_images import waitFor


def testSlowAdoptionDoesNotBlockSiblingOrOverwriteSameLane(tmp_path):
    firstEntered, letFirstFinish = Event(), Event()
    results = {}

    def adopt(task, path, outcome):
        if task["key"] == "first":
            firstEntered.set()
            assert letFirstFinish.wait(10)
        image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE) if path else None
        results[task["key"]] = (outcome, None if image is None else int(image[0, 0]))

    pool = ExportPool(tmp_path, adopt)
    slot = pool.slots[0]
    lanes = pool.configureLanes(0, 2)

    def submit(lane, key, value):
        descriptor = lanes[lane]
        assert descriptor["free"].acquire(False)
        slot["memory"].buf[descriptor["offset"]] = value
        pool.submit(dict(slot=0, lane=lane, shape=[1, 1], jobId="job", key=key))

    try:
        submit(0, "first", 11)
        assert firstEntered.wait(5)
        submit(0, "successor", 99)  # queued ahead of sibling, but first still owns its staging path
        submit(1, "sibling", 22)
        sibling = waitFor(lambda: next((entry for entry in pool.ownershipSnapshot("job")["slots"]
                          if any(d["resultKey"] == "sibling" for d in entry.get("publishing", []))), None),
                          timeout=3)
        assert not results and slot["busy"]
        assert [row["key"] for row in sibling["pendingRaw"]] == ["successor"]
        staged = {path.name for path in tmp_path.iterdir()}
        assert staged == {"slot-0-lane-0.png", "slot-0-lane-1.png"}
        with pytest.raises(ValueError, match="owned"):
            pool.releaseSlot(0)
        with pytest.raises(ValueError, match="owned"):
            pool.configureLanes(0, 1)
        letFirstFinish.set()
        waitFor(lambda: len(results) == 3 and not slot["busy"])
        assert results["first"] == ("AVAILABLE", 11)
        assert results["sibling"] == ("AVAILABLE", 22)
        assert results["successor"] in {("AVAILABLE", 99), ("EXPORT_TIMEOUT", None)}
        assert not list(tmp_path.iterdir())
        pool.releaseSlot(0)
    finally:
        letFirstFinish.set()
        pool.close()


def testCloseWaitsForStagedOwnerEvenAfterEncoderStops(tmp_path):
    entered, resume = Event(), Event()
    errors = []

    def adopt(_task, path, _outcome):
        entered.set()
        assert resume.wait(10)
        assert path.is_file()

    pool = ExportPool(tmp_path, adopt)
    slot = pool.slots[0]
    lane = pool.configureLanes(0, 2)[0]
    assert lane["free"].acquire(False)
    pool.submit(dict(slot=0, lane=0, shape=[1, 1], key="first"))

    def close():
        try:
            pool.close()
        except Exception as error:
            errors.append(error)

    closer = Thread(target=close)
    try:
        assert entered.wait(5)
        closer.start()
        waitFor(lambda: not slot["thread"].is_alive())
        assert closer.is_alive() and slot["busy"] and slot["process"] is not None
        assert (tmp_path / "slot-0-lane-0.png").exists()
    finally:
        resume.set()
        if closer.ident is not None:
            closer.join(10)
        else:
            pool.close()
    assert not errors and not closer.is_alive()
    assert slot["disposed"] and not list(tmp_path.iterdir())


def testFailedAdoptionRetirementQuarantinesBothLaneCredits(tmp_path, monkeypatch):
    orig = Path.unlink

    def unlink(path, *args, **kwargs):
        if path.name == "slot-0-lane-0.png" and current_thread().name == "display-adopt-0":
            raise OSError("injected staging retirement failure")
        return orig(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    pool = ExportPool(tmp_path, lambda *_: None)
    slot = pool.slots[0]
    lanes = pool.configureLanes(0, 2)
    try:
        assert lanes[0]["free"].acquire(False)
        pool.submit(dict(slot=0, lane=0, shape=[1, 1], key="first"))
        retired = waitFor(lambda: pool.diagnostics and pool.diagnostics[-1])
        assert retired["ownerPhase"] == "quarantined" and retired["retirementErrorType"] == "OSError"
        assert slot["quarantined"].value and slot["busy"]
        assert all(not lane["free"].acquire(False) for lane in lanes)
        with pytest.raises(ValueError, match="quarantined"):
            pool.releaseSlot(0)
        assert (tmp_path / "slot-0-lane-0.png").exists()
    finally:
        pool.close()
    assert slot["disposed"] and not list(tmp_path.iterdir())


def testStopCannotMissSubmissionThatAlreadyPassedStopCheck():
    from collections import deque
    from queue import Queue
    from threading import BoundedSemaphore, RLock
    from types import SimpleNamespace

    enqueueStarted, resumeEnqueue, readerUnlocked, resumeReader, readerDone = (Event() for _ in range(5))
    outcomes = []

    class GatedQueue(Queue):
        def put_nowait(self, task):
            enqueueStarted.set()
            if not resumeEnqueue.wait(5):
                raise RuntimeError("test must release its enqueue gate")
            return super().put_nowait(task)

    class GatedLock:
        def __init__(self):
            self.lock = RLock()

        def __enter__(self):
            self.lock.acquire()

        def __exit__(self, *_error):
            self.lock.release()
            if current_thread().name == "test-export-reader" and not readerUnlocked.is_set():
                readerUnlocked.set()
                if not resumeReader.wait(5):
                    raise RuntimeError("test must release its reader gate")

    credit = BoundedSemaphore(1)
    assert credit.acquire(False)
    slot = dict(index=0, queue=GatedQueue(), lock=GatedLock(), laneCount=1,
                laneFree=[credit], pending=set(), pendingDescriptors={},
                memory=SimpleNamespace(name="fake"), quarantined=SimpleNamespace(value=False))
    pool = object.__new__(ExportPool)
    pool.slots, pool.stop = [slot], Event()
    sender = Thread(target=pool.submit, args=(dict(slot=0, lane=0, shape=[1, 1], key="admitted"),))

    def receive():
        outcomes.append(pool._nextTask(slot, deque()))
        readerDone.set()

    reader = Thread(target=receive, name="test-export-reader")
    reader.start()
    try:
        assert readerUnlocked.wait(5)
        sender.start()
        assert enqueueStarted.wait(5)
        pool.stop.set()
        resumeReader.set()
        assert not readerDone.wait(.15)
    finally:
        resumeReader.set()
        resumeEnqueue.set()
        if sender.ident is not None:
            sender.join(5)
        reader.join(5)
    assert not sender.is_alive() and not reader.is_alive()
    assert len(outcomes) == 1 and outcomes[0]["key"] == "admitted"


def testFailedSiblingCannotPublishAheadOfStagedSuccess(tmp_path):
    entered, resume, failedPublished = Event(), Event(), Event()
    results = []

    def adopt(task, _path, outcome):
        if task["key"] == "first":
            entered.set()
            assert resume.wait(10)
        results.append((task["key"], outcome))
        if outcome == "EXPORT_FAILED":
            failedPublished.set()

    pool = ExportPool(tmp_path, adopt)
    lanes = pool.configureLanes(0, 2)
    try:
        assert lanes[0]["free"].acquire(False)
        pool.submit(dict(slot=0, lane=0, shape=[1, 1], key="first"))
        assert entered.wait(5)
        assert lanes[1]["free"].acquire(False)
        # Within raw budget but unsupported by PNG: actual encoder returns failure.
        pool.submit(dict(slot=0, lane=1, shape=[1, 1, 7], key="failed"))
        waitFor(lambda: pool.stats["failed"] == 1)
        assert not failedPublished.wait(.15) and not results
        resume.set()
        waitFor(lambda: len(results) == 2)
        assert results == [("first", "AVAILABLE"), ("failed", "EXPORT_FAILED")]
    finally:
        resume.set()
        pool.close()


def testCloseRetryDoesNotDisposeEarlierSlotWhileLaterPublisherIsBlocked(tmp_path, monkeypatch):
    entered, resume = Event(), Event()

    def adopt(_task, _path, _outcome):
        entered.set()
        assert resume.wait(10)

    pool = ExportPool(tmp_path, adopt)
    first, second = pool.slots
    lane = pool.configureLanes(1, 2)[0]
    publisher = second["publishThread"]
    join = publisher.join
    monkeypatch.setattr(publisher, "join", lambda _timeout: join(.10))
    try:
        assert lane["free"].acquire(False)
        pool.submit(dict(slot=1, lane=0, shape=[1, 1], key="later-owner"))
        assert entered.wait(5)
        with pytest.raises(RuntimeError, match="adoption owner still running"):
            pool.close()
        assert first["process"] is not None and second["process"] is not None
        assert not first.get("disposed") and not second.get("disposed")
        assert (tmp_path / "slot-1-lane-0.png").exists()
    finally:
        resume.set()
        monkeypatch.setattr(publisher, "join", join)
        pool.close()
    assert all(slot["disposed"] for slot in pool.slots)
    assert not list(tmp_path.iterdir())
