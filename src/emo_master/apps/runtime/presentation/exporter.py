"""Two fixed spawn slots. Timeout never returns a slot before process death.

Only bounded descriptors travel over the Pipe. Raw input lives in preallocated
shared memory; the child writes a bounded staging file, never a large Pipe reply.
"""
import multiprocessing
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
                pixels = np.ndarray(tuple(task["shape"]), np.uint8, buffer=memory.buf)
                ok, encoded = cv2.imencode(".png", pixels)
                if not ok or encoded.nbytes > RAW_LIMIT:
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
                        "queue": queue.Queue(maxsize=1), "index": index, "process": None,
                        "connection": None, "busy": False}
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
        return [{"name": s["memory"].name, "free": s["free"], "index": s["index"]} for s in self.slots]

    def submit(self, descriptor):
        slot = self.slots[descriptor["slot"]]
        slot["busy"] = True
        slot["queue"].put_nowait(dict(descriptor, deadline=time.monotonic() + .5))

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
            path = self.root / f"slot-{slot['index']}.png"
            outcome = "EXPORT_FAILED"
            reusable = True
            try:
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
                    self.errors.append(repr(reapError))
            try:
                self.callback(task, path if outcome == "AVAILABLE" else None, outcome)
            finally:
                # Staging deletion follows acknowledgement or proven child death.
                if reusable:
                    path.unlink(missing_ok=True)
                    if slot["process"] is None and not self.stop.is_set():
                        try:
                            self._spawn(slot)
                        except Exception as error:
                            reusable = False
                            self.errors.append(repr(error))
                    if reusable:
                        slot["free"].release()
                        slot["busy"] = False

    def close(self):
        self.stop.set()
        for slot in self.slots:
            thread = slot.get("thread")
            if thread:
                thread.join(12)
                if thread.is_alive():
                    raise RuntimeError("export owner still running")
            self._reap(slot)
            slot["memory"].close()
            slot["memory"].unlink()
