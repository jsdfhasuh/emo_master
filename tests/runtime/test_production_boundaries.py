import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.core.project.delivery_store import DirectoryOwner
from emo_master.core.project.models import ProjectDocument
from tests.runtime.production_fixture import productionProject, saveDocument, waitFor


def testProductionDiagnosticWindowKeepsMonotonicCursorAndTerminal(tmp_path):
    store = SqliteStore(tmp_path / "runtime.sqlite3")
    store.initialize()
    store.jobEventRetention = 7
    events = EventStore(store, retentionPerJob=7)
    for index in range(30):
        events.append("session", "workflow.completed", str(index))
    assert len(store.listJobEventsAfter("session")) <= 13
    terminal = events.append("session", "job.completed", "completed")
    retained = store.listJobEventsAfter("session")
    assert len(retained) == 7
    assert retained[-1].sequence == terminal.sequence == 31
    assert store.getTerminalJobEventSequence("session") == 31
    # Retention must not affect the next sequence if the Runtime reconnects.
    assert EventStore(store).append("session", "diagnostic", "after").sequence == 32


def testOrdinarySqliteStoreDoesNotDropHistory(tmp_path):
    store = SqliteStore(tmp_path / "runtime.sqlite3")
    store.initialize()
    events = EventStore(store, retentionPerJob=3)
    for index in range(12):
        events.append("ordinary", "diagnostic", str(index))
    assert len(events.read("ordinary")) == 3
    assert len(store.listJobEventsAfter("ordinary")) == 12


def testOccupiedProjectSwitchKeepsPreviousDirectoryOwnership(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    productionProject(first, save=False)
    productionProject(second, save=False)
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(first)
        previous = owner.projectOwner
        with DirectoryOwner(second):
            with pytest.raises(RuntimeError, match="in use"):
                owner.load(second)
        assert owner.projectOwner is previous and previous.stream is not None
        assert owner.runtime.loadedProjectPath == str(first)
        assert owner.status()["canStart"]
        with pytest.raises(RuntimeError, match="in use"):
            DirectoryOwner(first).acquire()
        job = owner.start()
        waitFor(lambda: any(e.eventType == "workflow.completed" for e in owner.runtime.eventStore.read(job)))
        owner.stop()
    finally:
        owner.close()


def testInvalidProjectSwitchClearsStartPermissionAndReleasesDirectories(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    productionProject(first, save=False)
    productionProject(second, save=False)
    (second / "project.json").write_text("{}", encoding="utf-8")
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(first)
        with pytest.raises(ValueError, match="invalid project"):
            owner.load(second)
        assert owner.document is None and owner.projectOwner is None
        assert owner.status()["state"] == "EMPTY" and not owner.status()["canStart"]
        with pytest.raises(ValueError, match="project not loaded"):
            owner.start()
        for root in (first, second):
            with DirectoryOwner(root):
                pass
        owner.load(first)
        assert owner.status()["canStart"]
    finally:
        owner.close()


@pytest.mark.parametrize("reload", [False, True])
def testLoadExceptionClearsStartPermissionAndDirectoryOwnership(tmp_path, monkeypatch, reload):
    first, second = tmp_path / "first", tmp_path / "second"
    productionProject(first, save=False)
    productionProject(second, save=False)
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(first)
        def fail(*args):
            raise RuntimeError("load service failed")
        monkeypatch.setattr(owner.runtime, "LoadProject", fail)
        with pytest.raises(RuntimeError, match="load service failed"):
            owner.load(first if reload else second)
        assert owner.document is None and owner.projectOwner is None
        assert not owner.status()["canStart"] and owner.status()["canLoad"]
        with pytest.raises(ValueError, match="project not loaded"):
            owner.start()
        for root in (first, second):
            with DirectoryOwner(root):
                pass
    finally:
        owner.close()


def testSlowDiagnosticPersistenceDoesNotKillContinuousSpawnedWorker(tmp_path, monkeypatch):
    root = tmp_path / "project"
    productionProject(root, interval=1, save=False)
    owner = ProductionRuntime(tmp_path / "data")
    entered, release = threading.Event(), threading.Event()
    original = owner.runtime.eventStore.append

    def delayedAppend(*args, **kwargs):
        eventType = kwargs.get("eventType", args[1] if len(args) > 1 else "")
        if eventType == "job.started":
            entered.set()
            assert release.wait(15), "test did not release diagnostic persistence"
        return original(*args, **kwargs)

    monkeypatch.setattr(owner.runtime.eventStore, "append", delayedAppend)
    try:
        owner.load(root)
        job = owner.start()
        assert entered.wait(5)
        cell = owner.runtime.jobSupervisor._heartbeatCells[job]
        before = cell.read()
        assert before is not None
        def heartbeatContinues():
            sample = cell.read()
            return sample is not None and sample > before + 6000
        waitFor(heartbeatContinues, seconds=10)
        release.set()
        waitFor(lambda: any(e.eventType == "workflow.completed" for e in owner.runtime.eventStore.read(job)))
        assert owner.runtime.jobRepository.get(job).status == "RUNNING"
        assert owner.runtime.jobSupervisor.ownsJobResources(job)
        owner.stop()
        record = owner.runtime.jobRepository.get(job)
        assert record.status == "ABORTED" and record.errorCode == "E_CANCELLED"
        assert not owner.runtime.jobSupervisor.ownsJobResources(job)
    finally:
        release.set()
        owner.close()


def testStopDuringLongCycleWaitAndSingleDefaultForOldProject(tmp_path):
    root = tmp_path / "project"
    document = productionProject(root, interval=10000, save=False)
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(root)
        job = owner.start()
        waitFor(lambda: any(e.eventType == "workflow.completed" for e in owner.runtime.eventStore.read(job)))
        started = time.monotonic()
        owner.stop()
        assert time.monotonic() - started < 3
        assert len([e for e in owner.runtime.eventStore.read(job) if e.eventType == "workflow.completed"]) == 1
        payload = document.model_dump()
        payload.update(schemaVersion="2.2")
        payload.pop("production")
        (root / "project.json").write_text(json.dumps(payload), encoding="utf-8")
        owner.load(root)
        assert owner.settings.mode == "single" and not owner.settings.autoStart
        job = owner.start()
        waitFor(lambda: owner.status()["canStart"])
        assert owner.runtime.jobRepository.get(job).status == "COMPLETED"
        assert len([e for e in owner.runtime.eventStore.read(job) if e.eventType == "workflow.completed"]) == 1
    finally:
        owner.close()


def testSourceLauncherAndConfigurationScriptPreserveProjectOwnedSettings(tmp_path):
    root = tmp_path / "project"
    document = productionProject(root)
    # Device fields remain opaque project values, not strings interpreted as paths.
    document.devices.bindings["camera"] = {"serial": "12345", "address": "192.168.1.10"}
    saveDocument(root, document)
    script = Path(__file__).resolve().parents[2] / "scripts/configure_runtime_project.py"
    command = [sys.executable, str(script), str(root), "--auto-start", "--mode", "continuous",
               "--cycle-interval-ms", "250"]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    payload = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert payload["production"] == dict(autoStart=True, mode="continuous", cycleIntervalMs=250, inputs={})
    assert payload["devices"] == document.devices.model_dump()
    assert payload["workflows"] == document.model_dump()["workflows"]
    assert (root / "project.json.bak").exists()
    launcher = script.parent.parent / "start_runtime.cmd"
    result = subprocess.run(["cmd", "/c", str(launcher), str(root), "--check", "--data-root", str(tmp_path / "check")],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "no Job started" in result.stdout


def testOutputWriteFailureIsVisibleAndDoesNotFallback(tmp_path):
    root = tmp_path / "project"
    productionProject(root)
    (root / "outputs/result.png").mkdir(parents=True)
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(root)
        job = owner.start()
        waitFor(lambda: owner.status()["canLoad"])
        assert owner.status()["state"] == "FAULT"
        assert owner.runtime.jobRepository.get(job).status == "FAILED"
        assert (root / "outputs/result.png").is_dir()
        assert not list((tmp_path / "data").rglob("result.png"))
    finally:
        owner.close()


def testRealSpawnMovedUnicodeProjectReadsAndWritesFromProjectRoot(tmp_path, monkeypatch):
    root = tmp_path / "original"
    productionProject(root, mode="single")
    moved = tmp_path / "工程 现场"
    root.rename(moved)
    monkeypatch.chdir(tmp_path)
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(moved)
        job = owner.start()
        waitFor(lambda: owner.status()["canStart"])
        assert owner.runtime.jobRepository.get(job).status == "COMPLETED"
        assert (moved / "outputs/result.png").is_file()
        assert not (tmp_path / "outputs").exists()
    finally:
        owner.close()


def testBusinessNgDoesNotStopAndSharesInvocationWithImageAndCount(tmp_path):
    root = tmp_path / "project"
    payload = productionProject(root, save=False).model_dump()
    workflow = payload["workflows"]["main"]
    workflow["nodes"].append({"nodeId": "judge", "operatorId": "vision.compare.number",
                              "params": {"operator": "gt", "rightValue": 3.0}})
    workflow["edges"].append(dict(fromNode="count", fromPort="count", toNode="judge", toPort="left"))
    page = payload["presentation"]
    page["dataSources"]["judge"] = dict(page["dataSources"]["count"], nodeId="judge",
                                       port="result", expectedType="boolean")
    page["pages"]["main"]["components"].append({"componentId": "judge", "type": "indicator",
        "bindings": {"value": "judge"}, "layout": {"row": 2}, "props": {"indicatorStates": {
            "true": {"text": "OK", "color": "green"}, "false": {"text": "NG", "color": "red"}}}})
    saveDocument(root, ProjectDocument.model_validate(payload))
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(root)
        job = owner.start()
        waitFor(lambda: any(r.identity.resultOrdinal >= 3 for r in owner.presentation.store.latest.values()))
        result = next(iter(owner.presentation.store.latest.values()))
        assert result.status == "COMPLETE"
        assert {s.sourceId: s.valueJson for s in result.sources if s.valueJson is not None} == {"count": "2", "judge": "false"}
        assert {s.sourceId for s in result.sources} == {"count", "judge", "image"}
        waitFor(lambda: any(e.workflowRunId == result.identity.invocationId
                           and e.eventType == "workflow.completed"
                           for e in owner.runtime.eventStore.read(job)))
        assert owner.runtime.jobRepository.get(job).status == "RUNNING"
        owner.stop()
    finally:
        owner.close()
