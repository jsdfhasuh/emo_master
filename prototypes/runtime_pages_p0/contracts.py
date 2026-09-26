"""Bounded ownership, detection order and finalization experiments.

All clocks are perf_counter_ns on one Windows host. No Qt or device access.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
import math
import threading
import time
from uuid import uuid4

import numpy as np


@dataclass(frozen=True)
class Budget:
    source_bytes: int = 256 * 1024
    result_bytes: int = 1024 * 1024
    elements: int = 4096
    depth: int = 12
    nodes: int = 16384
    sources: int = 16
    open_scopes: int = 8
    image_bytes: int = 8 * 1024 * 1024
    result_image_bytes: int = 16 * 1024 * 1024
    export_slots: int = 2
    export_pending: int = 0
    job_memory: int = 128 * 1024 * 1024
    runtime_memory: int = 256 * 1024 * 1024
    staging_disk: int = 64 * 1024 * 1024
    cache_disk: int = 256 * 1024 * 1024
    lease_disk: int = 64 * 1024 * 1024
    history: int = 32
    seal_seconds: float = 0.5
    export_seconds: float = 0.5
    reap_seconds: float = 1.0
    read_seconds: float = 0.5
    lease_seconds: float = 30.0
    control_seconds: float = 0.2


BUDGET = Budget()


class Unavailable(ValueError):
    pass


class Ledger:
    """Reserve before copying; leases include transient/IPC/encoding allowance."""

    def __init__(self, limit):
        self.limit, self.used, self.peak = limit, 0, 0
        self.lock = threading.Lock()

    def reserve(self, size):
        with self.lock:
            if size < 0 or self.used + size > self.limit:
                raise Unavailable("memory budget")
            self.used += size
            self.peak = max(self.peak, self.used)

    def release(self, size):
        with self.lock:
            assert 0 <= size <= self.used
            self.used -= size


def freeze(value, budget=BUDGET):
    """Two passes: bounded validation/accounting, then immutable value creation.

    Requires plugin ownership until capture returns. No arbitrary deepcopy or
    pickle. Charge conservative Python node/string overhead, not just JSON size.
    """
    seen = set()
    count = size = 0

    def walk(item, depth, copy):
        nonlocal count, size
        count += 1
        size += 64
        if depth > budget.depth or count > budget.nodes or size > budget.source_bytes:
            raise Unavailable("tree budget")
        if type(item) in (list, tuple, dict):
            if id(item) in seen:
                raise Unavailable("cycle")
            if len(item) > budget.elements:
                raise Unavailable("collection budget")
            seen.add(id(item))
            try:
                if type(item) is dict:
                    if any(type(key) is not str for key in item):
                        raise Unavailable("non-string key")
                    pairs = ((walk(key, depth + 1, copy), walk(v, depth + 1, copy))
                             for key, v in item.items())
                    return tuple(pairs) if copy else consume(pairs)
                values = (walk(v, depth + 1, copy) for v in item)
                return tuple(values) if copy else consume(values)
            finally:
                seen.remove(id(item))
        if type(item) not in (str, bool, int, float, type(None)):
            raise Unavailable("unknown type")
        if type(item) is float and not math.isfinite(item):
            raise Unavailable("nonfinite")
        if type(item) is int and item.bit_length() > 64:
            raise Unavailable("integer budget")
        if type(item) is str:
            size += len(item) * 4
        if size > budget.source_bytes:
            raise Unavailable("byte budget")
        return item

    def consume(iterator):
        for _ in iterator:
            pass

    walk(value, 0, False)
    charged = size
    count = size = 0
    return walk(value, 0, True), charged


def freeze_image(image, ledger, budget=BUDGET):
    if (type(image) is not np.ndarray or image.dtype != np.uint8
            or image.ndim not in (2, 3)
            or (image.ndim == 3 and image.shape[2] not in (1, 3, 4))
            or image.nbytes == 0 or image.nbytes > budget.image_bytes):
        raise Unavailable("image budget/type")
    # Raw immutable copy + conversion/IPC + worst-case encoder scratch/output.
    charge = image.nbytes * 6
    ledger.reserve(charge)
    try:
        return (image.shape, image.tobytes(order="C")), charge
    except BaseException:
        ledger.release(charge)
        raise


@dataclass(frozen=True)
class Snapshot:
    key: str
    identity: tuple
    ordinal: int
    cursor: int
    status: str
    values: tuple
    scope_end_ns: int
    commit_ns: int


@dataclass
class Pending:
    identity: tuple
    ordinal: int
    expected: tuple
    values: dict = field(default_factory=dict)
    seal_ns: int = 0
    scope_end_ns: int = 0
    failed: bool = False


class Results:
    def __init__(self, budget=BUDGET):
        self.budget = budget
        self.lock = threading.RLock()
        self.open = {}
        self.history = OrderedDict()
        self.latest = {}
        self.ordinals = {}
        self.cursor = 0
        self.fenced = set()
        self.gaps = 0

    def begin(self, identity, expected, key=None):
        # identity = runtime, generation, project, job, scope, stable call path.
        with self.lock:
            if len(expected) > self.budget.sources or len(set(expected)) != len(expected):
                raise Unavailable("source budget/duplicate")
            if identity[:4] in self.fenced:
                raise Unavailable("job fenced")
            if len(self.open) >= self.budget.open_scopes:
                self.gaps += 1
                if identity in self.ordinals:
                    # Preserve the new detection identity even when capture is
                    # refused; a health snapshot must not expose old OK as live.
                    self.ordinals[identity] += 1
                    self.cursor += 1
                    stamp = time.perf_counter_ns()
                    snapshot = Snapshot(str(uuid4()), identity, self.ordinals[identity], self.cursor,
                                        "INCOMPLETE", (("gap", ("UNAVAILABLE", "open scope quota")),), stamp, stamp)
                    self.latest[identity] = snapshot
                    self.history[snapshot.key] = snapshot
                    while len(self.history) > self.budget.history:
                        self.history.popitem(last=False)
                raise Unavailable("open scope gap; live must be invalidated")
            if identity not in self.ordinals and len(self.ordinals) >= 16:
                raise Unavailable("scope identity budget")
            key = key or str(uuid4())
            if key in self.open or key in self.history:
                raise Unavailable("duplicate result key")
            ordinal = self.ordinals.get(identity, 0) + 1
            self.ordinals[identity] = ordinal
            self.open[key] = Pending(identity, ordinal, tuple(expected))
            return key

    def value(self, key, source, value):
        with self.lock:
            pending = self.open.get(key)
            if pending is None or source not in pending.expected:
                return False
            # Only owned immutable values may cross this boundary.
            pending.values.setdefault(source, value)
            self._commit(key)
            return True

    def seal(self, key, scope_end_ns=None, failed=False):
        with self.lock:
            pending = self.open.get(key)
            if pending is not None and not pending.seal_ns:
                pending.seal_ns = time.perf_counter_ns()
                pending.scope_end_ns = scope_end_ns or pending.seal_ns
                pending.failed = failed
            self._commit(key)

    def _commit(self, key, force=False):
        pending = self.open.get(key)
        if pending is None or not pending.seal_ns:
            return
        missing = set(pending.expected) - pending.values.keys()
        expired = time.perf_counter_ns() - pending.seal_ns >= self.budget.seal_seconds * 1e9
        if missing and not (force or expired):
            return
        unavailable = any(v[0] == "UNAVAILABLE" for v in pending.values.values())
        status = "INCOMPLETE" if missing or force or unavailable else "FAILED" if pending.failed else "COMMITTED"
        self.cursor += 1
        snapshot = Snapshot(key, pending.identity, pending.ordinal, self.cursor, status,
                            tuple((s, pending.values.get(s, ("UNAVAILABLE", "deadline")))
                                  for s in pending.expected),
                            pending.scope_end_ns, time.perf_counter_ns())
        self.history[key] = snapshot
        previous = self.latest.get(pending.identity)
        if previous is None or previous.ordinal < snapshot.ordinal:
            self.latest[pending.identity] = snapshot
        del self.open[key]
        while len(self.history) > self.budget.history:
            self.history.popitem(last=False)

    def tick(self):
        with self.lock:
            for key in list(self.open):
                self._commit(key)

    def terminate(self, job_identity):
        with self.lock:
            self.fenced.add(job_identity)
            for key, pending in list(self.open.items()):
                if pending.identity[:4] == job_identity:
                    pending.seal_ns = pending.seal_ns or time.perf_counter_ns()
                    pending.scope_end_ns = pending.scope_end_ns or pending.seal_ns
                    self._commit(key, force=True)

    def snapshot(self, identity):
        with self.lock:
            return self.latest.get(identity), self.cursor


class Consumer:
    def __init__(self, session):
        self.session = session
        self.live = {}
        self.cursor = 0
        self.pinned = None

    def receive(self, snapshot):
        if snapshot.identity[:4] != self.session:
            return False
        old = self.live.get(snapshot.identity)
        self.cursor = max(self.cursor, snapshot.cursor)
        if old is None or snapshot.ordinal > old.ordinal:
            self.live[snapshot.identity] = snapshot
            return True
        return False

    def switch(self, session):
        self.session, self.live, self.cursor, self.pinned = session, {}, 0, None
