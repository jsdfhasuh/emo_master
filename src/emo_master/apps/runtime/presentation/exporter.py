"""Two fixed spawn slots. Timeout never returns a slot before process death.

Only bounded descriptors travel over the Pipe. Raw input lives in preallocated
shared memory; the child writes a bounded staging file, never a large Pipe reply.
"""
import multiprocessing
import math
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
import queue
import threading
import time


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
            try:
                pixels = np.ndarray(tuple(task["shape"]), np.uint8, buffer=memory.buf,
                                    offset=task.get("offset", 0))
                ok, encoded = cv2.imencode(".png", pixels)
                if not ok or encoded.nbytes > task.get("capacity", RAW_LIMIT):
                    raise ValueError("encoded image budget exceeded")
                Path(task["path"]).write_bytes(encoded.tobytes())
                connection.send({"ok": True})
            except Exception as error:
                connection.send({"ok": False, "error": type(error).__name__})
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
        self.slots = []
        self.errors: list[str] = []
        self.stats = {"completed": 0, "timed_out": 0, "reaped": 0, "failed": 0}
        try:
            for index in range(2):
                memory = SharedMemory(create=True, size=RAW_LIMIT)
                slot: dict = {"memory": memory, "free": self.context.BoundedSemaphore(1),
                        "queue": queue.Queue(maxsize=2), "index": index, "process": None,
                        "connection": None, "busy": False, "pending": set(), "laneCount": 1,
                        "lock": threading.RLock(), "quarantined": self.context.Value("b", False, lock=False)}
                slot["laneFree"] = [slot["free"], self.context.BoundedSemaphore(1)]
                self.slots.append(slot)
                self._spawn(slot)
                slot["thread"] = threading.Thread(target=self._run, args=(slot,), name=f"display-export-{index}")
                slot["thread"].start()
        except BaseException:
            self.close()
            raise

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
                slot["queue"].put_nowait(dict(descriptor, lane=lane,
                    offset=layout["offset"], capacity=layout["capacity"],
                    deadline=time.monotonic() + .5))
            except BaseException:
                slot["pending"].remove(lane)
                slot["busy"] = bool(slot["pending"])
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

    def _run(self, slot):
        while not self.stop.is_set() or not slot["queue"].empty():
            try:
                task = slot["queue"].get(timeout=.02)
            except queue.Empty:
                continue
            path = self.root / f"slot-{slot['index']}-lane-{task['lane']}.png"
            outcome = "EXPORT_FAILED"
            reusable = not slot["quarantined"].value
            try:
                if not reusable:
                    raise RuntimeError("image slab quarantined")
                if time.monotonic() >= task["deadline"]:
                    # Waiting behind another lane consumes the same deadline.
                    outcome = "EXPORT_TIMEOUT"
                    self.stats["timed_out"] += 1
                else:
                    slot["connection"].send(dict(task, path=str(path)))
                    if not slot["connection"].poll(max(0, task["deadline"] - time.monotonic())):
                        outcome = "EXPORT_TIMEOUT"
                        self.stats["timed_out"] += 1
                        self._reap(slot)
                    else:
                        reply = slot["connection"].recv()
                        if not isinstance(reply, dict) or not reply.get("ok"):
                            raise ValueError("invalid export reply")
                        if time.monotonic() > task["deadline"]:
                            outcome = "EXPORT_TIMEOUT"
                        else:
                            outcome = "AVAILABLE"
                            self.stats["completed"] += 1
            except Exception as error:
                self.stats["failed"] += 1
                self.errors.append(repr(error))
                self.errors[:] = self.errors[-32:]
                try:
                    self._reap(slot)
                except Exception as reapError:
                    reusable = False
                    self._quarantine(slot)
                    self.errors.append(repr(reapError))
            try:
                self.callback(task, path if outcome == "AVAILABLE" else None, outcome)
            finally:
                # A failed reap quarantines the entire slab, including the
                # other lane. No credit is reused until real owner disposal.
                if reusable:
                    path.unlink(missing_ok=True)
                    if slot["process"] is None and not self.stop.is_set():
                        try:
                            self._spawn(slot)
                        except Exception as error:
                            reusable = False
                            self._quarantine(slot)
                            self.errors.append(repr(error))
                    if reusable:
                        with slot["lock"]:
                            slot["pending"].remove(task["lane"])
                            slot["laneFree"][task["lane"]].release()
                            slot["busy"] = bool(slot["pending"])

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
                slot["busy"] = False
                slot["quarantined"].value = False
            slot["memory"].close()
            slot["memory"].unlink()
