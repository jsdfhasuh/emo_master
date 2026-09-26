"""Two fixed owner threads supervising the original two spawn export units."""
from collections import deque
from multiprocessing import shared_memory
import threading
import time
from uuid import uuid4

import numpy as np

from .contracts import BUDGET, Unavailable
from .exporter import Exporters


class AsyncExports(Exporters):
    def __init__(self, root, resources, assets):
        super().__init__(root)
        self.resources, self.assets = resources, assets
        self.tasks = {}
        self.finishing = set()
        self.errors = deque(maxlen=64)
        self.owners = [threading.Thread(target=self._own, args=(i,), name=f"p0-export-owner-{i}") for i in range(2)]
        for owner in self.owners:
            owner.start()

    def submit(self, job, key, image, completed, mode="normal"):
        submitted = time.perf_counter_ns()
        if (type(image) is not np.ndarray or image.dtype != np.uint8 or image.ndim != 3
                or image.shape[2] != 3 or not 0 < image.nbytes <= BUDGET.image_bytes):
            raise Unavailable("selected BGR image exceeds budget/type")
        with self.idle:
            index = next((i for i in range(2) if i not in self.active and i not in self.dead), None)
            if self.closed or index is None:
                raise Unavailable("export quota: two executing, zero pending")
            self.active.add(index)
        memory = staging = segment = None
        try:
            memory = self.resources.reserve(job, "memory", image.nbytes * 6)
            staging = self.resources.reserve(job, "staging", BUDGET.image_bytes * 2)
            segment = shared_memory.SharedMemory(create=True, size=image.nbytes)
            target = np.ndarray(image.shape, image.dtype, buffer=segment.buf)
            np.copyto(target, image)  # the only producer-side image freeze
            del target
            copied = time.perf_counter_ns()
            task = str(uuid4())
            with self.idle:
                self.tasks[index] = (job, key, tuple(image.shape), segment, memory, staging, task,
                                     completed, mode, submitted, copied)
                self.idle.notify_all()
            return {"copy_ms": (copied-submitted)/1e6, "task_id": task}
        except BaseException:
            if segment:
                segment.close()
                segment.unlink()
            for token in (memory, staging):
                if token:
                    self.resources.release(token)
            with self.idle:
                self.active.remove(index)
                self.idle.notify_all()
            raise

    def _own(self, index):
        while True:
            with self.idle:
                self.idle.wait_for(lambda: index in self.tasks or self.closed)
                if index not in self.tasks:
                    return
                info = self.tasks.pop(index)
            job, key, shape, segment, memory, staging, task, callback, mode, submitted, copied = info
            path = self.root / f"{task}.png"
            broken = False
            outcome = {"status": "UNAVAILABLE", "reason": "cancelled", "task_id": task}
            try:
                process, pipe = self.slots[index]
                dispatch = time.perf_counter_ns()
                pipe.send((task, segment.name, shape, str(path), mode))
                def remaining():
                    return max(0, BUDGET.export_seconds - (time.perf_counter_ns()-submitted)/1e9)
                if not pipe.poll(remaining()) or pipe.recv() != ("STARTED", task):
                    raise TimeoutError("start deadline")
                self.started.append((task, process.pid, segment.name))
                # Diagnostic identifiers are bounded, not a second task history.
                del self.started[:-64]
                if not pipe.poll(remaining()):
                    raise TimeoutError("running export deadline")
                reply = pipe.recv()
                if reply[:2] != ("DONE", task):
                    raise ValueError("IPC identity")
                adopted_at = time.perf_counter_ns()
                asset = self.assets.adopt(job, key, path, reply[2], reply[3], shape)
                outcome = dict(status="VALID", asset=asset, raw_sha256=reply[5], task_id=task,
                               copy_ms=(copied-submitted)/1e6, queue_ms=(dispatch-copied)/1e6,
                               encode_read_ms=(reply[7]-reply[6])/1e6,
                               adoption_ms=(time.perf_counter_ns()-adopted_at)/1e6)
            except Exception as error:
                broken = True
                outcome["reason"] = type(error).__name__
            finally:
                if broken:
                    self._retire(index)  # slot remains quarantined until dead
                segment.close()
                segment.unlink()
                path.unlink(missing_ok=True)
                path.with_suffix(".part").unlink(missing_ok=True)
                self.resources.release(memory)
                self.resources.release(staging)
                # Callback cannot retain raw images or SharedMemory. Admission
                # is reopened before callback to avoid capture/result lock cycles.
                with self.idle:
                    self.active.remove(index)
                    self.finishing.add(index)
                    self.idle.notify_all()
                try:
                    callback(key, outcome)
                except BaseException as error:
                    self.errors.append(repr(error))
                finally:
                    with self.idle:
                        self.finishing.remove(index)
                        self.idle.notify_all()
                if broken and not self.closed:
                    try:
                        slot = self._spawn()
                        with self.idle:
                            if self.closed:
                                self._reap(slot)
                            else:
                                self.slots[index] = slot
                                self.dead.remove(index)
                    except BaseException as error:
                        self.errors.append(repr(error))
                del self.reaped[:-64]

    def wait_idle(self, timeout=4):
        with self.idle:
            if not self.idle.wait_for(lambda: not self.active and not self.finishing, timeout):
                raise TimeoutError("export cleanup")

    def close(self):
        # During super().__init__ failure the owner threads do not exist yet.
        if not hasattr(self, "owners"):
            return super().close()
        with self.idle:
            self.closed = True
            self.idle.notify_all()
        for index in range(2):
            self._retire(index)
        for owner in self.owners:
            owner.join(3)
            if owner.is_alive():
                raise RuntimeError("export owner still running; quota retained")
        self.wait_idle()
