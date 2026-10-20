"""A timed-out export keeps its precise phase without replaying the task."""
import threading
from collections import deque
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from prototypes.runtime_pages_p0.async_exports import AsyncExports
from prototypes.runtime_pages_p0.contracts import BUDGET
from prototypes.runtime_pages_p0.resources import Assets, Resources


def testAsyncTimeoutRetainsItsStageDetailAndOriginalBudget(tmp_path):
    resources = Resources()
    assets = Assets(tmp_path / "cache", resources)
    exporter = AsyncExports(tmp_path / "staging", resources, assets)
    outcomes = []
    completed = threading.Event()

    def callback(key, outcome):
        outcomes.append((key, outcome))
        completed.set()

    try:
        submitted = exporter.submit("job", "result", np.zeros((4, 4, 3), np.uint8), callback, mode="hang")
        assert completed.wait(5), "export owner did not report the forced timeout"
        exporter.wait_idle()
        assert len(outcomes) == 1 and outcomes[0][0] == "result"
        outcome = outcomes[0][1]
        assert outcome["status"] == "UNAVAILABLE" and outcome["reason"] == "TimeoutError"
        assert outcome["stage"] in {"start_wait", "completion_wait"}
        assert outcome["detail"] == {"start_wait": "start deadline", "completion_wait": "running export deadline"}[outcome["stage"]]
        assert outcome["task_id"] == submitted["task_id"]
        assert outcome["budget_ms"] == 500 == BUDGET.export_seconds * 1000
        assert outcome["elapsed_ms"] >= outcome["copy_ms"] >= 0
        assert outcome["queue_ms"] >= 0
        assert exporter.reaped and not any(resources.used.values())
    finally:
        exporter.close()
        assets.close()
    assert not resources.tokens


@pytest.mark.parametrize("fault,stage,reason", [
    ("start_timeout", "start_wait", "TimeoutError"),
    ("start_mismatch", "start_identity", "ValueError"),
    ("completion_timeout", "completion_wait", "TimeoutError"),
    ("completion_mismatch", "completion_identity", "ValueError"),
])
def testEachWaitAndIdentityPredicateHasDistinctCorrelatedEvidence(tmp_path, fault, stage, reason):
    """Deterministic IPC fault injection; the test above owns real spawned hang."""
    class Pipe:
        def __init__(self):
            self.polls = 0
            self.receives = 0
            self.task = ""

        def send(self, request):
            self.task = request[0]

        def poll(self, timeout):
            assert 0 <= timeout <= BUDGET.export_seconds
            self.polls += 1
            return not ((fault == "start_timeout" and self.polls == 1)
                        or (fault == "completion_timeout" and self.polls == 2))

        def recv(self):
            self.receives += 1
            if self.receives == 1:
                return ("STARTED", "wrong-task" if fault == "start_mismatch" else self.task)
            return ("DONE", "wrong-task")

    resources = Resources()
    assets = Assets(tmp_path / "cache", resources)
    exporter = object.__new__(AsyncExports)
    exporter.root = tmp_path / "staging"
    exporter.root.mkdir()
    exporter.resources, exporter.assets = resources, assets
    exporter.tasks, exporter.active, exporter.dead, exporter.finishing = {}, set(), set(), set()
    exporter.closed = False
    exporter.idle = threading.Condition()
    exporter.started, exporter.reaped, exporter.errors = [], [], []
    exporter.diagnostics = deque(maxlen=64)
    exporter.slots = [(SimpleNamespace(pid=42), Pipe())]
    exporter._retire = lambda index: exporter.dead.add(index)
    outcomes = []

    def completed(key, outcome):
        outcomes.append((key, outcome, deepcopy(outcome)))
        exporter.closed = True

    submitted = exporter.submit("job", "key", np.zeros((4, 4, 3), np.uint8), completed)
    exporter._own(0)
    key, outcome, atPublication = outcomes[0]
    assert outcome == atPublication, "published result metadata must not change during callback retirement"
    assert key == outcome["result_key"] == "key" and outcome["job_id"] == "job"
    assert outcome["task_id"] == submitted["task_id"]
    assert outcome["stage"] == stage and outcome["reason"] == reason
    assert outcome["budget_ms"] == 500 and not resources.tokens
    assert outcome["expected_identity"][1] == submitted["task_id"]
    if "mismatch" in fault:
        assert outcome["actual_identity"][1] == "wrong-task"
    timeline = outcome["timeline"]
    assert timeline["start_wait_started_ns"] <= timeline["start_wait_ended_ns"]
    if stage.startswith("completion"):
        assert timeline["completion_wait_started_ns"] <= timeline["completion_wait_ended_ns"]
    assert timeline["retirement_started_ns"] <= timeline["resources_released_ns"] <= timeline["callback_started_ns"]
    assert "callback_ended_ns" not in timeline
    assert exporter.diagnostics[-1]["timeline"]["callback_ended_ns"] >= timeline["callback_started_ns"]
    assert not exporter.active and not exporter.finishing
    assets.close()
