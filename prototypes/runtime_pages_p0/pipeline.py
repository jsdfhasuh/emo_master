"""Long-lived real-Runner capture -> supervised export -> immutable asset result."""
from collections import deque
from dataclasses import asdict
import threading
import time

from .async_exports import AsyncExports
from .contracts import BUDGET, Results, Unavailable, freeze
from .resources import Assets, Resources


class PipelineResults(Results):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def _commit(self, key, force=False):
        was_open = key in self.open
        super()._commit(key, force)
        if was_open and key not in self.open:
            self.owner.committed(self.history[key])


class Pipeline:
    """Fixed experimental capture plan: root load.image and source.items.

    Only these two declared sources are collected; legacy preview still captures
    its original ports. At most two Jobs and 32 result metadata records survive.
    """
    def __init__(self, root):
        self.resources = Resources()
        self.assets = Assets(root / "cache", self.resources)
        self.results = PipelineResults(self)
        self.lock = self.results.lock
        self.exporter = AsyncExports(root / "export-staging", self.resources, self.assets)
        self.runs = {}
        self.metadata = {}
        self.sessions = {}
        self.session_tokens = {}
        self.events = deque(maxlen=64)
        self.generated = self.sealed = self.final = self.rejected = 0
        self.condition = threading.Condition(self.lock)
        self.commit_sink = None
        self.modes = {}  # bounded fault injection selected explicitly by test
        self.stopping = threading.Event()
        self.timer = threading.Thread(target=self._tick, name="p0-seal-deadlines")
        self.timer.start()

    def identity(self, job):
        with self.lock:
            if job not in self.sessions:
                if len(self.sessions) >= 2:
                    raise Unavailable("two Job quota")
                # Four concurrent control snapshots can outlive the handler
                # while gRPC serializes/sends. Keep their worst-case copies
                # charged for the entire session, not just asdict().
                self.session_tokens[job] = self.resources.reserve(job, "memory", 16 * BUDGET.result_bytes)
                self.sessions[job] = ("p0-continuous", 1, "p0-project", job, "main", ())
            return self.sessions[job]

    def publish(self, eventType, context, **kwargs):
        if context.workflowId != "main":
            return
        with self.lock:
            run = context.workflowRunId
            self.events.append((eventType, run))
            if eventType == "workflow.started":
                identity = self.identity(context.jobId)
                self.generated += 1
                try:
                    key = self.results.begin(identity, ("image", "items"))
                except Unavailable:
                    self.rejected += 1
                    self.condition.notify_all()
                    return
                try:
                    token = self.resources.reserve(context.jobId, "memory", BUDGET.source_bytes * 2 + 64 * 1024)
                    self.metadata[key] = token
                except Unavailable:
                    self.results.value(key, "image", ("UNAVAILABLE", "metadata budget"))
                    self.results.value(key, "items", ("UNAVAILABLE", "metadata budget"))
                self.runs[run] = key
            elif eventType in ("workflow.completed", "workflow.failed"):
                key = self.runs.pop(run, None)
                if key is None:
                    return
                self.sealed += 1
                if eventType.endswith("failed"):
                    pending = self.results.open.get(key)
                    if pending:
                        for source in pending.expected:
                            if source not in pending.values:
                                self.results.value(key, source, ("FAILED", "execution"))
                self.results.seal(key, time.perf_counter_ns(), failed=eventType.endswith("failed"))

    def capture(self, node, outputs, context):
        if context.workflowId != "main":
            return
        with self.lock:
            key = self.runs.get(context.workflowRunId)
            if not key or key not in self.metadata:
                return
            if node.nodeId == "load":
                try:
                    self.exporter.submit(context.jobId, key, outputs["image"], self.export_done,
                                         mode=self.modes.pop(context.jobId, "normal"))
                except Unavailable as error:
                    self.rejected += 1
                    self.results.value(key, "image", ("UNAVAILABLE", str(error)))
            elif node.nodeId == "source":
                try:
                    value, _ = freeze(outputs["items"])
                    self.results.value(key, "items", ("VALID", value))
                except Unavailable as error:
                    self.results.value(key, "items", ("UNAVAILABLE", str(error)))

    def export_done(self, key, outcome):
        with self.lock:
            accepted = self.results.value(key, "image", (outcome["status"], outcome))
            if not accepted and outcome["status"] == "VALID":
                # A sealed timeout/fence cannot be retroactively repaired.
                with self.assets.lock:
                    self.assets._delete(outcome["asset"]["asset_id"])

    def committed(self, snapshot):
        self.final += 1
        latest = self.results.latest[snapshot.identity]
        if latest.key == snapshot.key:
            image = dict(snapshot.values)["image"]
            asset_id = image[1]["asset"]["asset_id"] if image[0] == "VALID" else None
            self.assets.set_latest(snapshot.identity[3], asset_id)
        retained = set(self.results.history) | {s.key for s in self.results.latest.values()} | set(self.results.open)
        for key in list(self.metadata):
            if key not in retained:
                self.resources.release(self.metadata.pop(key))
        self.condition.notify_all()
        if self.commit_sink:
            self.commit_sink(snapshot)

    def snapshot_json(self, request):
        snapshot, cursor = self.results.snapshot(self.identity(request["job"]))
        return {"latest": asdict(snapshot) if snapshot else None, "cursor": cursor}

    def follow(self, request, context):
        cursor = request.get("cursor", 0)
        identity = self.identity(request["job"])
        while context.is_active():
            with self.condition:
                entries = [s for s in self.results.history.values() if s.identity == identity and s.cursor > cursor]
                if not entries:
                    self.condition.wait(.02)
                    continue
                oldest = next(iter(self.results.history.values())).cursor
                snapshot = entries[0]
                reset = cursor > 0 and cursor < oldest - 1
                cursor = snapshot.cursor
                token = self.resources.reserve(request["job"], "memory", BUDGET.result_bytes * 4)
                try:
                    message = {"latest": asdict(snapshot), "cursor": cursor, "reset_required": reset}
                except BaseException:
                    self.resources.release(token)
                    raise
            try:
                yield message
            finally:
                del message, snapshot, entries
                self.resources.release(token)

    def _tick(self):
        while not self.stopping.wait(.01):
            self.results.tick()
            with self.assets.lock:
                self.assets._expire()

    def stats(self):
        with self.lock:
            return dict(generated=self.generated, sealed=self.sealed, final=self.final, rejected=self.rejected,
                        open=len(self.results.open), history=len(self.results.history), runs=len(self.runs),
                        metadata=len(self.metadata), diagnostics=len(self.events), assets=len(self.assets.entries),
                        evictions=self.assets.evictions, resources=self.resources.snapshot())

    def close(self):
        self.exporter.close()
        self.stopping.set()
        self.timer.join(2)
        assert not self.timer.is_alive()
        with self.lock:
            for identity in self.sessions.values():
                self.results.terminate(identity[:4])
            self.results.history.clear()
            self.results.latest.clear()
            self.runs.clear()
            for token in self.metadata.values():
                self.resources.release(token)
            self.metadata.clear()
            self.assets.close()
            for token in self.session_tokens.values():
                self.resources.release(token)
            self.session_tokens.clear()
        assert not self.exporter.errors, list(self.exporter.errors)
        assert all(value == 0 for value in self.resources.used.values()), self.resources.snapshot()
