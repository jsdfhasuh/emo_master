"""Event-gated mailbox pressure; only original parent closure returns scope credit."""
from contextlib import contextmanager
from copy import deepcopy
import json
import multiprocessing
from multiprocessing.shared_memory import SharedMemory
import queue

import numpy as np
import pytest

from emo_master.apps.runtime.presentation.assets import AssetStore
from emo_master.apps.runtime.presentation.collector import ResultCollector
from emo_master.apps.runtime.presentation.mailbox import SharedMailbox
from emo_master.apps.runtime.presentation.service import PresentationService
from emo_master.apps.runtime.presentation.store import ResultStore
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.workflow.models import CompiledEdge, CompiledNode, CompiledProject, CompiledWorkflow


class _ExportOwner:
    """Synchronous test encoder; ownership and callbacks follow the original path."""
    def __init__(self, harness):
        self.harness = harness
        self.hold = False
        self.tasks = []

    def submit(self, task):
        self.tasks.append(task)
        if not self.hold:
            self.finish()

    def finish(self):
        import cv2
        while self.tasks:
            task = self.tasks.pop(0)
            slot = self.harness.config["slots"][0]
            pixels = np.ndarray(tuple(task["shape"]), np.uint8, buffer=self.harness.memory.buf)
            ok, encoded = cv2.imencode(".png", pixels)
            assert ok
            path = self.harness.root / "image.png"
            path.write_bytes(encoded.tobytes())
            try:
                self.harness.service._exported(task, path, "AVAILABLE")
            finally:
                path.unlink(missing_ok=True)
                slot["free"].release()


class _Harness:
    def __init__(self, root, *, scopes=1, image=True, measure=False):
        self.root = root
        context = multiprocessing.get_context("spawn")
        plan = {"scopes": {}, "sources": {}}
        for index in range(scopes):
            scope = f"scope-{index}"
            plan["scopes"][scope] = {"scopeWorkflowId": "detect", "callPath": []}
            for port in ("count", "image") if image and index == 0 else ("count",):
                plan["sources"][f"{scope}-{port}"] = {"resultScopeId": scope,
                    "kind": "workflow_output", "port": port, "fieldPath": [],
                    "expectedType": "image" if port == "image" else "integer"}
        self.memory = SharedMemory(create=True, size=12) if image else None
        self.config = {"plan": json.dumps(plan), "versions": {}, "capture": True, "measure": measure,
            "credits": context.BoundedSemaphore(8), "queue": SharedMailbox(context),
            "scopeIds": list(plan["scopes"]), "ordinals": context.Array("Q", 16, lock=False),
            "rejected": context.Value("Q", 0), "imageLaneBySource": {},
            "slots": ([{"index": 0, "name": self.memory.name, "free": context.BoundedSemaphore(1),
                        "capacity": 12}] if image else []),
            "identity": {"runtimeInstanceId": "runtime", "jobId": "job", "mode": "runtime",
                         "executionRevision": "a" * 64, "capturePlanRevision": "b" * 64}}
        self.service = PresentationService.__new__(PresentationService)
        self.service.store = ResultStore()
        self.service.pending = {}
        self.service.jobs = {"job": self.config}
        self.service.timings = {"job": []}
        self.service.assets = AssetStore(root / "assets")
        self.service.exporter = _ExportOwner(self) if image else None
        self.events = []
        self.block_seals = False
        self.collector = ResultCollector(self.config, self.emit)
        ports = {"count": "integer", "image": "image"}
        first = CompiledNode("input", "workflow_input", None, {}, ports, {})
        last = CompiledNode("output", "workflow_output", None, ports, ports, {})
        edges = tuple(CompiledEdge("input", key, "output", key) for key in ports)
        workflow = CompiledWorkflow("detect", "Detect", ports, ports, (first, last), edges,
            {"input": first, "output": last}, {"output": edges}, {"input": edges}, ("input", "output"))
        project = CompiledProject("probe", 1, "detect", {"detect": workflow}, {}, {})
        self.runner = WorkflowRunner(project, {}, resultCollector=self.collector)

    def emit(self, event):
        if self.block_seals and event["eventType"] == "display.seal":
            raise queue.Full
        self.config["queue"].put_nowait(event)
        self.events.append(deepcopy(event))

    def run(self, ordinal):
        result = self.runner.run("detect", {"count": ordinal, "image": np.zeros((2, 2, 3), np.uint8)},
            RunContext("job", "detect", f"invocation-{ordinal}"), CancellationToken())
        assert result.outputs["count"] == ordinal

    def drain(self):
        # The explicit caller-controlled gate replaces time/scheduler assumptions.
        while True:
            try:
                event = self.config["queue"].get(timeout=0)
            except queue.Empty:
                return
            self.service.consume("job", event)

    def results(self):
        return list(self.service.store.history)

    def credits(self):
        # Read-only test instrumentation; acquisition/release remain product actions.
        return self.config["credits"].get_value()

    def close(self):
        self.service._fence("job", "COMPLETED")
        if self.service.exporter is not None:
            self.service.exporter.finish()
        self.service.assets.close()
        if self.memory is not None:
            self.memory.close()
            self.memory.unlink()


@pytest.fixture
def harness(tmp_path):
    @contextmanager
    def create(**kwargs):
        item = _Harness(tmp_path, **kwargs)
        try:
            yield item
        finally:
            item.close()
    return create


def testNormalFullMailboxRecoversTheEighthSealAfterDrain(harness):
    with harness() as h:
        for ordinal in range(1, 18):
            h.run(ordinal)
        assert h.credits() == 0
        before = {"deferred": len(getattr(h.collector, "pendingSeals", {})),
            "open": len(h.collector.open), "rejected": h.config["rejected"].value,
            "errors": h.runner.captureErrors, "ordinal": h.config["ordinals"][0], "events": len(h.events)}
        h.drain()
        assert h.credits() == 7
        assert [r.identity.resultOrdinal for r in h.results()] == list(range(1, 8))
        h.run(18)  # Retries the frozen eighth seal before new admission.
        h.drain()
        assert [r.identity.resultOrdinal for r in h.results()] == [*range(1, 9), 18]
        assert before == {"deferred": 1, "open": 0, "rejected": 9,
                          "errors": 0, "ordinal": 17, "events": 16}
        assert all(s.reasonCode != "IPC_ERROR" for r in h.results() for s in r.sources)
        assert h.credits() == 8 and not h.collector.pendingSeals
        assert len([e for e in h.events if e["eventType"] == "display.seal"]) == 9


@pytest.mark.parametrize("image", [False, True])
def testMeasuredEightScopesRecoverWithoutTimingBacklog(harness, image):
    # Eight scalars and at most one image stay inside the public normal profile.
    with harness(scopes=8, image=image, measure=True) as h:
        h.run(1)
        assert h.collector.telemetry == {}
        assert h.collector.open == {}
        assert len(h.collector.pendingSeals) == (1 if image else 0)
        assert h.runner.captureErrors == 1  # Optional measurements were actually dropped.
        h.drain()
        assert h.credits() == (7 if image else 8)
        h.run(2)
        h.drain()
        assert h.credits() == 8 and not h.collector.pendingSeals
        assert h.collector.telemetry == {}
        assert len([r for r in h.results() if r.identity.resultOrdinal == 1]) == 8
        assert any(r.identity.resultOrdinal == 2 for r in h.results())


@pytest.mark.parametrize("measure", [False, True])
def testRepeatedFullRetriesDoNotCreateAdmissionsOrGrowTelemetry(harness, measure):
    with harness(scopes=8, image=False, measure=measure) as h:
        h.block_seals = True
        h.run(1)
        h.drain()
        assert h.credits() == 0 and len(h.collector.pendingSeals) == 8
        errors = h.runner.captureErrors
        h.runner._capture("end", RunContext("job", "detect", "invocation-1"), "COMPLETED")
        assert h.runner.captureErrors == errors  # Retrying end has no new timing loss.
        for ordinal in range(2, 12):
            h.run(ordinal)
            assert h.collector.telemetry == {}
            assert h.collector.open == {}
            assert len(h.collector.pendingSeals) == 8
        assert list(h.config["ordinals"])[:8] == [11] * 8
        losses = 11 if measure else 0
        assert h.runner.captureErrors == losses  # Only real optional timing loss.
        assert h.config["rejected"].value == losses + 8 * 10
        assert len(h.events) == 8 and h.credits() == 0
        h.block_seals = False
        h.run(12)
        # Publication is not parent closure and cannot return scope credit.
        assert h.credits() == 0 and h.collector.open == {}
        assert not h.collector.pendingSeals
        assert len([e for e in h.events if e["eventType"] == "display.open"]) == 8
        h.drain()
        assert h.credits() == 8
        h.run(13)
        h.drain()
        assert len(h.results()) == 16 and h.credits() == 8


@pytest.mark.parametrize("terminal", ["COMPLETED", "FAILED", "CANCELLED"])
def testDeferredSealKeepsOriginalFrozenValuesTerminalAndTimes(harness, terminal):
    with harness(image=False) as h:
        context = RunContext("job", "detect", "invocation-1")
        h.collector.begin(context)
        values = {"count": 1}
        h.collector.output(context, values, workflow=True)
        former_value = next(iter(h.collector.open.values()))["values"]["scope-0-count"]
        h.block_seals = True
        h.collector.end(context, terminal)
        sealed = deepcopy(next(iter(h.collector.pendingSeals.values())))
        values["count"] = 99
        former_value["valueJson"] = "99"
        h.collector.output(context, {"count": 42}, workflow=True)
        h.drain()
        assert h.credits() == 7
        h.block_seals = False
        h.run(2)
        emitted = [e for e in h.events if e["eventType"] == "display.seal" and e["key"] == sealed["key"]]
        assert emitted == [sealed]
        assert emitted[0]["sources"][0]["valueJson"] == "1"
        assert emitted[0]["terminal"] == terminal
        h.collector.end(context, terminal)  # Repeated end is not repeated publication.
        h.drain()
        assert len([e for e in h.events if e["eventType"] == "display.seal" and e["key"] == sealed["key"]]) == 1
        assert h.credits() == 8


@pytest.mark.parametrize("published", [False, True])
def testUnexpectedSealErrorPropagatesAndIsNeverRetried(harness, published):
    with harness(image=False) as h:
        native_emit = h.collector.emit
        failed = []
        def emit(event):
            if event["eventType"] == "display.seal" and not failed:
                failed.append(deepcopy(event))
                if published:
                    native_emit(event)
                raise ValueError("unexpected IPC failure")
            native_emit(event)
        h.collector.emit = emit
        context = RunContext("job", "detect", "invocation-1")
        h.collector.begin(context)
        h.collector.output(context, {"count": 1}, workflow=True)
        with pytest.raises(ValueError, match="unexpected IPC failure"):
            h.collector.end(context, "COMPLETED")
        assert not h.collector.pendingSeals
        assert h.credits() == 7
        h.drain()
        h.run(2)
        h.drain()
        assert len([e for e in h.events if e["eventType"] == "display.seal" and e["key"] == failed[0]["key"]]) == int(published)
        assert h.credits() == (8 if published else 7)
        h.service._fence("job", "COMPLETED")
        assert h.credits() == 8


def testUnexpectedFirstSealErrorPreservesAllOtherEndedScopes(harness):
    with harness(scopes=8, image=False, measure=True) as h:
        native_emit = h.collector.emit
        failed = []
        def emit(event):
            if event["eventType"] == "display.seal" and not failed:
                failed.append(event["key"])
                raise ValueError("first packet is ambiguous")
            native_emit(event)
        h.collector.emit = emit
        h.run(1)
        assert h.runner.captureErrors == 1
        assert len(h.collector.pendingSeals) == 7
        assert h.collector.open == {} and h.collector.telemetry == {}
        h.drain()
        assert h.credits() == 0
        h.run(2)
        assert not h.collector.pendingSeals and h.credits() == 0
        h.drain()
        assert len(h.results()) == 7 and h.credits() == 7
        assert failed[0] in h.service.pending
        assert not any(e.get("key") == failed[0] for e in h.events)
        h.service._fence("job", "COMPLETED")
        assert h.credits() == 8


def testUnexpectedTimingErrorCannotSkipSealsOrRetainMeasurements(harness):
    with harness(scopes=8, image=False, measure=True) as h:
        native_emit = h.collector.emit
        def emit(event):
            if event["eventType"] == "display.timing":
                raise ValueError("unexpected measurement failure")
            native_emit(event)
        h.collector.emit = emit
        h.run(1)
        assert h.runner.captureErrors == 1
        assert h.collector.telemetry == {} and h.collector.open == {}
        assert not h.collector.pendingSeals
        h.drain()
        assert len(h.results()) == 8 and h.credits() == 8


def testDeferredImageKeepsOriginalExportDeadline(harness, monkeypatch):
    import time
    with harness() as h:
        h.service.exporter.hold = True
        h.block_seals = True
        h.run(1)
        sealed = deepcopy(next(iter(h.collector.pendingSeals.values())))
        h.drain()
        monkeypatch.setattr(time, "monotonic", lambda: sealed["sealedAt"] + .6)
        h.block_seals = False
        h.run(2)
        h.drain()
        old = next(r for r in h.results() if r.identity.resultOrdinal == 1)
        assert old.sources[1].reasonCode == "EXPORT_TIMEOUT"
        assert old.timing.scopeEndedNs == sealed["scopeEndedNs"]
        assert h.credits() == 8


def testLateOlderImageClosureCannotMoveLatestBackward(harness):
    with harness() as h:
        h.service.exporter.hold = True
        h.block_seals = True
        h.run(1)
        h.drain()
        h.block_seals = False
        h.run(2)
        h.drain()
        assert h.service.store.latest[("job", "scope-0")].identity.resultOrdinal == 2
        h.service.exporter.finish()
        assert {r.identity.resultOrdinal for r in h.results()} == {1, 2}
        assert h.service.store.latest[("job", "scope-0")].identity.resultOrdinal == 2
        assert h.credits() == 8


@pytest.mark.parametrize("terminal, reason", [("COMPLETED", "IPC_ERROR"), ("CANCELLED", "EXECUTION_CANCELLED")])
def testNoLaterBoundaryStillUsesOriginalTerminalFence(harness, terminal, reason):
    with harness(image=False) as h:
        h.block_seals = True
        h.run(1)
        h.drain()
        assert h.credits() == 7 and len(h.collector.pendingSeals) == 1
        h.service._fence("job", terminal)
        assert h.credits() == 8
        assert h.results()[0].sources[0].reasonCode == reason
        assert h.results()[0].executionTerminal == terminal


@pytest.mark.parametrize("deferred", [False, True])
def testReaderErrorFenceCannotExpandLocalOwnership(harness, deferred):
    with harness(scopes=8, image=False) as h:
        context = RunContext("job", "detect", "invocation-1")
        h.collector.begin(context)
        h.drain()
        if deferred:
            h.block_seals = True
            h.collector.end(context, "COMPLETED")
        assert len(h.collector.open) + len(h.collector.pendingSeals) == 8
        assert h.credits() == 0
        # A real reader CRC/length failure can fence before producer retirement.
        h.service._fence("job", "UNKNOWN")
        assert h.credits() == 8
        h.collector.begin(RunContext("job", "detect", "invocation-2"))
        assert len(h.collector.open) + len(h.collector.pendingSeals) == 8
        assert h.credits() == 8  # Local rejection must not acquire or release it.
        assert h.config["rejected"].value == 8
        assert list(h.config["ordinals"])[:8] == [2] * 8
        assert len([e for e in h.events if e["eventType"] == "display.open"]) == 8
