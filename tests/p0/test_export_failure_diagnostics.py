"""A timed-out export keeps its precise phase without replaying the task."""
import threading

import numpy as np

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
        assert outcome["stage"] in {"start", "completion"}
        assert outcome["detail"] == {"start": "start deadline", "completion": "running export deadline"}[outcome["stage"]]
        assert outcome["task_id"] == submitted["task_id"]
        assert outcome["budget_ms"] == 500 == BUDGET.export_seconds * 1000
        assert outcome["elapsed_ms"] >= outcome["copy_ms"] >= 0
        assert outcome["queue_ms"] >= 0
        assert exporter.reaped and not any(resources.used.values())
    finally:
        exporter.close()
        assets.close()
    assert not resources.tokens
