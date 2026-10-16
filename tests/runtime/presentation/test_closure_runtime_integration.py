"""Isolated actual Runtime/spawn/monitor integration with injected capture faults."""
import json

import pytest

from emo_master.apps.operator_runtime.controller import ProductionRuntime
from tests.runtime.test_production_boundaries import productionProject, waitFor


@pytest.mark.parametrize("fault", ["variable_read", "closure_model"])
def testContinuousCaptureBeyondScopeQuotaAfterFailureAndActualRetirement(tmp_path, monkeypatch, fault):
    root = tmp_path / "project"
    productionProject(root, save=False)
    owner = ProductionRuntime(tmp_path / "data")
    channel = owner.presentation
    injected = []
    if fault == "variable_read":
        originalAttach = channel._attach
        def attach(*args, **kwargs):
            config = originalAttach(*args, **kwargs)
            plan = json.loads(config["plan"])
            plan["sources"]["count"].update(kind="global_variable", variableId="injected-missing-variable")
            config["plan"] = json.dumps(plan)
            injected.append("actual spawned collector variable read")
            return config
        monkeypatch.setattr(channel, "_attach", attach)
    else:
        originalClose = channel.store.close
        def close(key, sources, terminal):
            item = channel.store.open.get(key)
            if not injected and item is not None and item["identity"]["resultOrdinal"] == 3:
                injected.append(key)
                sources = [dict(source, reasonCode="INJECTED_INVALID_REASON") if source["sourceId"] == "count"
                           else source for source in sources]
            return originalClose(key, sources, terminal)
        monkeypatch.setattr(channel.store, "close", close)
    try:
        owner.load(root)
        job = owner.start()
        waitFor(lambda: any(result.identity.resultOrdinal >= 12 for result in channel.store.latest.values()))
        assert injected and channel.monitor.is_alive() and channel.readers[job].is_alive()
        history = [result for result in channel.store.history if result.identity.jobId == job]
        assert len(history) >= 12, "actual continuous capture must survive the original eight-credit cap"
        if fault == "variable_read":
            assert all(next(source for source in result.sources if source.sourceId == "count").reasonCode == "INVALID_VALUE"
                       for result in history)
            assert any("VARIABLE_UNAVAILABLE" in source.reason for result in history for source in result.sources
                       if source.sourceId == "count")
        else:
            failed = next(result for result in history if result.identity.resultOrdinal == 3)
            assert failed.status == "INCOMPLETE" and all(source.reasonCode == "INVALID_VALUE" for source in failed.sources)
            assert any(result.status == "COMPLETE" and result.identity.resultOrdinal > 3 for result in history)
            assert any(record["stage"] == "result_validation" and record["identity"]["resultOrdinal"] == 3
                       for record in channel.diagnostics)
        owner.stop()
        waitFor(lambda: owner.status()["canStart"])
        waitFor(lambda: not channel.pending and not channel.store.open and not channel.readers[job].is_alive())
        waitFor(lambda: not any(slot["busy"] for slot in channel.exporter.slots))
        assert not owner.runtime.jobSupervisor.ownsJobResources(job)
        credits = channel.jobs[job]["credits"]
        assert all(credits.acquire(False) for _ in range(8)) and not credits.acquire(False)
        for _ in range(8):
            credits.release()
        channel.release(job)
        assert job not in channel.jobs and channel.assets.stats()["assets"] == 0
        assert not (channel.root / "jobs" / job).exists()
    finally:
        owner.close()
    assert not channel.monitor.is_alive()
    assert all(slot["process"] is None for slot in channel.exporter.slots)
