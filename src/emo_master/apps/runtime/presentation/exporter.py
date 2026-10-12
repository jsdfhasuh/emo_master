"""Owned spawn slots, grown on admission. Timeout waits for process death.

Only bounded descriptors travel over the Pipe. Raw input lives in preallocated
shared memory; the child writes a bounded staging file, never a large Pipe reply.
A verified private-snapshot acknowledgment ends shared raw ownership. One child
snapshot (at most one lane, within the encoder reservation) survives encoding.
The serial owner retains the staging file and Job's slab until adoption/cleanup,
while the producer may fill the same raw lane for one queued successor.
"""
import multiprocessing
import math
from collections import deque
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
import queue
import threading
import time
from uuid import uuid4


RAW_LIMIT = 8 * 1024 * 1024


def encodeWorker(connection, memoryName):
    import cv2
    import numpy as np
    memory = SharedMemory(name=memoryName)
    try:
        connection.send("ready")
        while True:
            task = connection.recv()
            if task is None:
                return
            pixels = encoded = None
            try:
                rawBytes = math.prod(task["shape"])
                if not 0 < rawBytes <= task.get("capacity", RAW_LIMIT) <= RAW_LIMIT:
                    raise ValueError("snapshot exceeds image lane budget")
                pixels = np.ndarray(tuple(task["shape"]), np.uint8, buffer=memory.buf,
                                    offset=task.get("offset", 0)).copy()
                connection.send({"kind": "snapshot", "ok": True, "taskId": task["taskId"],
                                 "rawBytes": pixels.nbytes, "copiedNs": time.monotonic_ns()})
                encodeStartedNs = time.monotonic_ns()
                ok, encoded = cv2.imencode(".png", pixels)
                if not ok or encoded.nbytes > task.get("capacity", RAW_LIMIT):
                    raise ValueError("encoded image budget exceeded")
                encodedNs = time.monotonic_ns()
                Path(task["path"]).write_bytes(encoded.tobytes())
                connection.send({"kind": "complete", "ok": True, "taskId": task["taskId"],
                    "encodeStartedNs": encodeStartedNs, "encodedNs": encodedNs,
                    "stagedNs": time.monotonic_ns()})
            except Exception as error:
                connection.send({"kind": "complete", "ok": False, "taskId": task["taskId"], "error": type(error).__name__})
            finally:
                # Never retain the previous private input while allocating the
                # next. The Pipe contains descriptors/acks only, never pixels.
                pixels = encoded = None
    finally:
        memory.close()
        connection.close()


class ExportPool:
    def __init__(self, root: Path, callback, worker=encodeWorker):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.callback = callback
        self.worker = worker
        self.context = multiprocessing.get_context("spawn")
        self.stop = threading.Event()
        self.slots: list[dict] = []
        self.errors: list[str] = []
        self.diagnostics: deque[dict] = deque(maxlen=64)
        self.stats = {"completed": 0, "timed_out": 0, "reaped": 0, "failed": 0}
        try:
            for _ in range(2):
                self.addSlot()
        except BaseException:
            self.close()
            raise

    def addSlot(self):
        """Caller holds admission lock. Existing Jobs survive allocation failure."""
        if self.stop.is_set():
            raise RuntimeError("export pool closing")
        index = len(self.slots)
        memory = SharedMemory(create=True, size=RAW_LIMIT)
        slot: dict = {"memory": memory, "free": self.context.BoundedSemaphore(1),
            "queue": queue.Queue(maxsize=2), "index": index, "process": None,
            "connection": None, "busy": False, "active": False, "pending": set(), "laneCount": 1,
            "lock": threading.RLock(), "quarantined": self.context.Value("b", False, lock=False)}
        slot["laneFree"] = [slot["free"], self.context.BoundedSemaphore(1)]
        self.slots.append(slot)
        try:
            self._spawn(slot)
            slot["thread"] = threading.Thread(target=self._run, args=(slot,), name=f"display-export-{index}")
            slot["thread"].start()
        except BaseException:
            self._quarantine(slot)
            # Keep a failed partial allocation owned until close proves reaping.
            raise
        return index

    def _spawn(self, slot):
        parent, child = self.context.Pipe()
        process = self.context.Process(target=self.worker, args=(child, slot["memory"].name), name="display-png-export")
        slot.update(process=process, connection=parent)
        process.start()
        child.close()
        if not parent.poll(10) or parent.recv() != "ready":
            self._reap(slot)
            raise RuntimeError("exporter startup failed")

    def descriptors(self):
        return [self._descriptor(slot, 0) for slot in self.slots]

    def _descriptor(self, slot, lane):
        capacity = RAW_LIMIT // slot["laneCount"]
        return {"name": slot["memory"].name, "free": slot["laneFree"][lane],
                "index": slot["index"], "lane": lane, "capacity": capacity,
                "offset": lane * capacity, "quarantined": slot["quarantined"]}

    def configureLanes(self, index, count):
        """Called only for an unowned slab, before the owning Job is spawned."""
        if count not in (1, 2):
            raise ValueError("image lane budget exceeded")
        slot = self.slots[index]
        with slot["lock"]:
            if slot["busy"] or slot["pending"] or slot["quarantined"].value:
                raise ValueError("image slab still owned or quarantined")
            slot["laneCount"] = count
            return [self._descriptor(slot, lane) for lane in range(count)]

    def releaseSlot(self, index):
        """Caller must first prove detector/IPC retirement, including partial copies."""
        slot = self.slots[index]
        with slot["lock"]:
            if slot["busy"] or slot["pending"] or slot["quarantined"].value:
                raise ValueError("export slot still owned or quarantined")
            for credit in slot["laneFree"]:
                while credit.acquire(False):
                    pass
                credit.release()

    def _quarantine(self, slot):
        with slot["lock"]:
            slot["quarantined"].value = True
            slot["busy"] = True
            for credit in slot["laneFree"]:
                while credit.acquire(False):
                    pass

    def submit(self, descriptor):
        slot = self.slots[descriptor["slot"]]
        lane = descriptor.get("lane", 0)
        with slot["lock"]:
            if slot["quarantined"].value or not 0 <= lane < slot["laneCount"]:
                raise ValueError("image slab unavailable")
            if lane in slot["pending"]:
                raise ValueError("duplicate outstanding image lane")
            layout = self._descriptor(slot, lane)
            if (descriptor.get("offset", layout["offset"]) != layout["offset"]
                    or not 0 < math.prod(descriptor["shape"]) <= layout["capacity"]):
                raise ValueError("image descriptor exceeds its lane")
            credit = slot["laneFree"][lane]
            if credit.acquire(False):
                credit.release()
                raise ValueError("image descriptor has no reserved lane")
            slot["pending"].add(lane)
            slot["busy"] = True
            try:
                submitted = time.monotonic()
                slot["queue"].put_nowait(dict(descriptor, lane=lane,
                    offset=layout["offset"], capacity=layout["capacity"],
                    taskId=str(uuid4()), submittedNs=time.monotonic_ns(), deadline=submitted + .5))
            except BaseException:
                slot["pending"].remove(lane)
                slot["busy"] = bool(slot["pending"]) or slot.get("active", False)
                raise

    def _reap(self, slot):
        process = slot["process"]
        if process is None:
            return
        started = time.monotonic()
        if process.is_alive():
            process.terminate()
        process.join(.5)
        if process.is_alive():
            process.kill()
            process.join(max(0, 1 - (time.monotonic() - started)))
        if process.is_alive():
            raise RuntimeError("exporter could not be reaped; slot remains quarantined")
        process.close()
        slot["connection"].close()
        slot.update(process=None, connection=None)
        self.stats["reaped"] += 1

    def _releaseLane(self, slot, task, diagnostic):
        with slot["lock"]:
            slot["pending"].remove(task["lane"])
            slot["laneFree"][task["lane"]].release()
            diagnostic["laneReleased"] = True
            diagnostic["laneReleasedNs"] = time.monotonic_ns()
            # busy includes the active callback/file owner. Clearing it here
            # would allow releaseSlot/configureLanes to reassign a live slab.

    def _run(self, slot):
        while not self.stop.is_set() or not slot["queue"].empty():
            try:
                task = slot["queue"].get(timeout=.02)
            except queue.Empty:
                continue
            with slot["lock"]:
                slot["active"] = True
            path = self.root / f"slot-{slot['index']}-lane-{task['lane']}.png"
            outcome = "EXPORT_FAILED"
            reusable = not slot["quarantined"].value
            stage = "queue_wait"
            diagnostic = {"path": "local-presentation-export", "clock": "host_monotonic",
                "taskId": task.get("taskId"), "resultKey": task.get("key"), "jobId": task.get("jobId"),
                "sourceId": task.get("sourceId"), "slot": slot["index"], "lane": task["lane"],
                "invocationId": task.get("invocationId"), "resultScopeId": task.get("resultScopeId"),
                "resultOrdinal": task.get("resultOrdinal"),
                "budgetMs": 500, "frozenAt": task.get("frozenAt"), "copyMs": task.get("copyMs"),
                "submittedNs": task.get("submittedNs"), "ownerStartedNs": time.monotonic_ns(),
                "deadline": task["deadline"], "laneReleased": False}
            try:
                if not reusable:
                    raise RuntimeError("image slab quarantined")
                if time.monotonic() >= task["deadline"]:
                    # Waiting behind another lane consumes the same deadline.
                    outcome = "EXPORT_TIMEOUT"
                    self.stats["timed_out"] += 1
                else:
                    stage = "dispatch"
                    diagnostic["dispatchNs"] = time.monotonic_ns()
                    slot["connection"].send(dict(task, path=str(path)))
                    for phase in ("snapshot", "complete"):
                        stage = "snapshot_wait" if phase == "snapshot" else "completion_wait"
                        prefix = "snapshot" if phase == "snapshot" else ""
                        diagnostic[prefix + "waitStartedNs"] = time.monotonic_ns()
                        remaining = max(0, task["deadline"] - time.monotonic())
                        diagnostic[prefix + "remainingMs"] = remaining * 1000
                        ready = slot["connection"].poll(remaining)
                        diagnostic[prefix + "waitEndedNs"] = time.monotonic_ns()
                        if not ready:
                            raise TimeoutError("export deadline expired waiting for " + phase)
                        stage = "snapshot_reply" if phase == "snapshot" else "encoder_reply"
                        reply = slot["connection"].recv()
                        diagnostic["snapshotReplyNs" if phase == "snapshot" else "replyNs"] = time.monotonic_ns()
                        if isinstance(reply, dict):
                            if reply.get("taskId") != task["taskId"]:
                                diagnostic["actualTaskId"] = str(reply.get("taskId"))[:160]
                                raise ValueError("encoder reply task identity mismatch")
                            for field in ("copiedNs", "encodeStartedNs", "encodedNs", "stagedNs"):
                                if type(reply.get(field)) is int:
                                    diagnostic[field] = reply[field]
                            if reply.get("error"):
                                diagnostic["encoderErrorType"] = str(reply["error"])[:80]
                        if not isinstance(reply, dict) or reply.get("ok") is not True or reply.get("kind") != phase:
                            raise ValueError("invalid export reply")
                        if time.monotonic() > task["deadline"]:
                            stage = "snapshot_after_deadline" if phase == "snapshot" else "completion_after_deadline"
                            raise TimeoutError("export reply after deadline")
                        if phase == "snapshot":
                            if type(reply.get("rawBytes")) is not int or reply["rawBytes"] != math.prod(task["shape"]):
                                raise ValueError("snapshot byte count mismatch")
                            diagnostic["snapshotBytes"] = reply["rawBytes"]
                            try:
                                self._releaseLane(slot, task, diagnostic)
                            except Exception:
                                reusable = False
                                self._quarantine(slot)
                                raise
                    outcome = "AVAILABLE"
                    self.stats["completed"] += 1
            except Exception as error:
                diagnostic["errorType"] = type(error).__name__
                diagnostic["detail"] = str(error)[:240]
                if isinstance(error, TimeoutError):
                    outcome = "EXPORT_TIMEOUT"
                    self.stats["timed_out"] += 1
                else:
                    self.stats["failed"] += 1
                    self.errors.append(repr(error))
                    self.errors[:] = self.errors[-32:]
                try:
                    diagnostic["reapStartedNs"] = time.monotonic_ns()
                    self._reap(slot)
                    diagnostic["reapedNs"] = time.monotonic_ns()
                except Exception as reapError:
                    diagnostic["reapErrorType"] = type(reapError).__name__
                    reusable = False
                    self._quarantine(slot)
                    self.errors.append(repr(reapError))
            diagnostic.update(stage=stage, outcome=outcome, callbackStartedNs=time.monotonic_ns())
            task["exportDiagnostic"] = dict(diagnostic)
            try:
                self.callback(task, path if outcome == "AVAILABLE" else None, outcome)
            except Exception as error:
                diagnostic["callbackErrorType"] = type(error).__name__
                self.errors.append("callback: " + type(error).__name__)
                self.errors[:] = self.errors[-32:]
            finally:
                diagnostic["callbackEndedNs"] = time.monotonic_ns()
                # A failed reap quarantines the entire slab, including the
                # other lane. No credit is reused until real owner disposal.
                try:
                    if reusable:
                        path.unlink(missing_ok=True)
                        if slot["process"] is None and not self.stop.is_set():
                            self._spawn(slot)
                        with slot["lock"]:
                            if not diagnostic["laneReleased"]:
                                self._releaseLane(slot, task, diagnostic)
                            # An admitted successor owns its own pending entry
                            # and reservation; never remove or release it here.
                            slot["active"] = False
                            slot["busy"] = bool(slot["pending"])
                except Exception as error:
                    self._quarantine(slot)
                    diagnostic["retirementErrorType"] = type(error).__name__
                    self.errors.append(repr(error))
                    self.errors[:] = self.errors[-32:]
                diagnostic["retirementEndedNs"] = time.monotonic_ns()
                self.diagnostics.append(diagnostic)

    def close(self):
        self.stop.set()
        for slot in self.slots:
            thread = slot.get("thread")
            if thread:
                thread.join(12)
                if thread.is_alive():
                    raise RuntimeError("export owner still running")
            self._reap(slot)
            with slot["lock"]:
                slot["pending"].clear()
                slot["active"] = False
                slot["busy"] = False
                slot["quarantined"].value = False
            slot["memory"].close()
            slot["memory"].unlink()
