"""Two warm spawn exporters, disposable IPC and parent-owned shared memory.

No Future cancellation claims: a timed-out worker is terminated, joined and
observed dead before its slot can be reused. Workers never own devices or DBs.
"""
import hashlib
import multiprocessing as mp
from multiprocessing import shared_memory
from pathlib import Path
import threading
import time
from uuid import uuid4

from .contracts import BUDGET, Ledger, Unavailable, freeze_image


def _worker(pipe):
    import cv2
    import numpy as np

    pipe.send(("READY", time.perf_counter_ns()))
    while True:
        command = pipe.recv()
        if command is None:
            return
        task, name, shape, path, mode = command
        pipe.send(("STARTED", task))
        if mode == "hang":
            threading.Event().wait()
        if mode == "ipc":
            pipe.send_bytes(b"broken-wire-payload")
            return
        segment = shared_memory.SharedMemory(name=name)
        try:
            image = np.ndarray(shape, dtype=np.uint8, buffer=segment.buf)
            ok, data = cv2.imencode(".png", image)
            if not ok or data.nbytes > BUDGET.image_bytes * 2:
                raise ValueError("encoded image budget")
            target = Path(path)
            partial = target.with_suffix(".part")
            partial.write_bytes(data.tobytes())
            partial.replace(target)
            read_start = time.perf_counter_ns()
            content = target.read_bytes()
            decoded = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_UNCHANGED)
            assert decoded is not None and tuple(decoded.shape) == tuple(shape)
            read_ms = (time.perf_counter_ns() - read_start) / 1e6
            if read_ms > BUDGET.read_seconds * 1000:
                raise TimeoutError("asset read/decode deadline")
            pipe.send(("DONE", task, len(content), hashlib.sha256(content).hexdigest(), read_ms))
        finally:
            segment.close()


class Exporters:
    def __init__(self, root, slots=BUDGET.export_slots):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.context = mp.get_context("spawn")
        self.ledger = Ledger(BUDGET.runtime_memory)
        self.lock = threading.Lock()
        self.idle = threading.Condition(self.lock)
        self.slots = []
        self.active = set()
        self.dead = set()
        self.closed = False
        self.reaped = []
        self.started = []
        self.clock_samples = []
        try:
            for _ in range(slots):
                self.slots.append(self._spawn())
        except BaseException:
            self.close()
            raise

    def _spawn(self):
        parent, child = self.context.Pipe()
        process = self.context.Process(target=_worker, args=(child,), name="p0-export")
        before = time.perf_counter_ns()
        try:
            process.start()
            child.close()
            if not parent.poll(10):
                raise TimeoutError("export startup")
            message = parent.recv()
            after = time.perf_counter_ns()
            assert message[0] == "READY" and before <= message[1] <= after
            self.clock_samples.append((before, message[1], after))
            return process, parent
        except BaseException:
            child.close()
            self._reap((process, parent))
            raise

    def _reap(self, slot):
        process, pipe = slot
        pid = process.pid
        try:
            if process.is_alive():
                process.terminate()
            process.join(BUDGET.reap_seconds / 2)
            if process.is_alive():
                process.kill()
                process.join(BUDGET.reap_seconds / 2)
            if process.is_alive():
                # Keep ownership; do NOT return this slot to admission.
                raise RuntimeError("export process could not be reaped")
            self.reaped.append(pid)
            process.close()
        finally:
            pipe.close()

    def execute(self, image, mode="normal"):
        with self.lock:
            if self.closed:
                raise Unavailable("closed")
            index = next((i for i in range(len(self.slots)) if i not in self.active and i not in self.dead), None)
            if index is None:
                raise Unavailable("export quota")
            self.active.add(index)
        segment = None
        charge = 0
        task = str(uuid4())
        path = self.root / f"{task}.png"
        broken = False
        try:
            (shape, raw), charge = freeze_image(image, self.ledger)
            segment = shared_memory.SharedMemory(create=True, size=len(raw))
            segment.buf[:] = raw
            del raw
            process, pipe = self.slots[index]
            start = time.perf_counter()
            pipe.send((task, segment.name, shape, str(path), mode))
            if not pipe.poll(BUDGET.export_seconds):
                raise TimeoutError("no STARTED")
            assert pipe.recv() == ("STARTED", task)
            self.started.append((task, process.pid, segment.name))
            remaining = max(0, BUDGET.export_seconds - (time.perf_counter() - start))
            if not pipe.poll(remaining):
                raise TimeoutError("running export deadline")
            # The private child is trusted. Corruption must retire this Pipe;
            # project packages and network clients never supply pickle bytes.
            reply = pipe.recv()
            if reply[:2] != ("DONE", task) or not path.is_file():
                raise ValueError("IPC identity")
            return {"status": "VALID", "task": task, "sha256": reply[3], "bytes": reply[2],
                    "read_decode_ms": reply[4], "total_ms": (time.perf_counter()-start)*1000}
        except Exception as error:
            broken = True
            return {"status": "UNAVAILABLE", "task": task, "reason": type(error).__name__}
        finally:
            if broken:
                self._retire(index)
            if segment is not None:
                segment.close()
                segment.unlink()
            if charge:
                self.ledger.release(charge)
            path.unlink(missing_ok=True)
            path.with_suffix(".part").unlink(missing_ok=True)
            with self.lock:
                # Restart only on the next explicit replenish, outside execution
                # deadline. Dead slots remain excluded until READY is observed.
                self.active.remove(index)
                self.idle.notify_all()

    def _retire(self, index):
        with self.lock:
            if index not in self.dead:
                self._reap(self.slots[index])
                self.dead.add(index)

    def replenish(self):
        for index in tuple(self.dead):
            if self.closed or index in self.active:
                raise Unavailable("cannot replenish running/closed exporter")
            self.slots[index] = self._spawn()
            self.dead.remove(index)

    def close(self):
        self.closed = True
        for index in range(len(self.slots)):
            self._retire(index)
        with self.idle:
            if not self.idle.wait_for(lambda: not self.active, timeout=BUDGET.reap_seconds):
                raise RuntimeError("export owner did not release buffers")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
