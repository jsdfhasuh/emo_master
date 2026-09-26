from dataclasses import replace
from itertools import permutations
import time

import cv2
import numpy as np
import pytest

from prototypes.runtime_pages_p0.contracts import (
    BUDGET, Consumer, Ledger, Results, Unavailable, freeze, freeze_image,
)
from prototypes.runtime_pages_p0.isolation import DebugState
from prototypes.runtime_pages_p0.runner_probe import Capture, Mutator, Source, image_project
from emo_master.apps.runtime.context.global_counters import ProjectGlobalCounters
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.image_loader.operator import ImageLoaderOperator
from emo_master.plugins.builtins.global_counter.operator import GlobalCounterOperator


IDENTITY = ("runtime", 1, "project", "job", "scope", ("main", "call-a"))


@pytest.mark.parametrize("order", list(permutations(range(3))))
@pytest.mark.parametrize("new_status", ["COMMITTED", "INCOMPLETE", "FAILED"])
def test_all_six_completion_orders_on_server_and_two_consumers(order, new_status):
    store = Results(replace(BUDGET, seal_seconds=0))
    store.ordinals[IDENTITY] = 100
    keys = [store.begin(IDENTITY, ("count",)) for _ in range(3)]
    consumers = [Consumer(IDENTITY[:4]), Consumer(IDENTITY[:4])]
    committed = []
    for index in order:
        key = keys[index]
        if index != 2 or new_status != "INCOMPLETE":
            store.value(key, "count", ("VALID", 101 + index))
        store.seal(key, failed=index == 2 and new_status == "FAILED")
        committed.append(index)
        snapshot = store.history[key]
        for consumer in consumers:
            consumer.receive(snapshot)
            consumer.receive(snapshot)  # replay, never re-count
            assert consumer.live[IDENTITY].ordinal == 101 + max(committed)
        assert store.snapshot(IDENTITY)[0].ordinal == 101 + max(committed)
    assert store.snapshot(IDENTITY)[0].status == new_status
    assert store.cursor == 3
    store.seal(keys[2])
    assert not store.value(keys[2], "count", ("VALID", 0))
    assert store.cursor == 3


def test_scope_call_job_generation_and_locked_detail_are_independent():
    store = Results()
    consumer = Consumer(IDENTITY[:4])
    for scope, path in [("scope", ("a",)), ("scope", ("b",)), ("other", ("a",))]:
        identity = IDENTITY[:4] + (scope, path)
        key = store.begin(identity, ())
        store.seal(key)
        consumer.receive(store.history[key])
    assert len(consumer.live) == 3
    old = next(iter(consumer.live.values()))
    consumer.pinned = old.key
    key = store.begin(old.identity, ())
    store.seal(key)
    consumer.receive(store.history[key])
    assert consumer.pinned == old.key
    for session in [("runtime", 1, "project", "job-2"), ("runtime", 2, "project", "job-2")]:
        consumer.switch(session)
        assert not consumer.receive(old)
        key = store.begin(session + IDENTITY[4:], ())
        store.seal(key)
        assert consumer.receive(store.history[key])
        assert store.history[key].ordinal == 1


def test_102_incomplete_then_101_ok_never_rolls_back():
    store = Results(replace(BUDGET, seal_seconds=0))
    store.ordinals[IDENTITY] = 100
    a, b = (store.begin(IDENTITY, ("count",)) for _ in range(2))
    store.seal(b)
    consumer = Consumer(IDENTITY[:4])
    consumer.receive(store.history[b])
    store.value(a, "count", ("VALID", 1))
    store.seal(a)
    consumer.receive(store.history[a])
    assert store.snapshot(IDENTITY)[0] is consumer.live[IDENTITY]
    assert consumer.live[IDENTITY].ordinal == 102
    assert consumer.live[IDENTITY].status == "INCOMPLETE"


def test_seal_before_data_timeout_fence_and_last_notification_recovery():
    store = Results(replace(BUDGET, seal_seconds=.01))
    a = store.begin(IDENTITY, ("a", "b"))
    store.seal(a)
    store.seal(a)
    store.value(a, "a", ("VALID", 0))
    assert a not in store.history
    store.value(a, "b", ("VALID", False))
    assert store.history[a].status == "COMMITTED"
    b = store.begin(IDENTITY, ("a",))
    store.seal(b)
    time.sleep(.02)
    store.tick()
    assert store.history[b].status == "INCOMPLETE"
    consumer = Consumer(IDENTITY[:4])
    consumer.receive(store.snapshot(IDENTITY)[0])  # last push lost; explicit health pull
    assert consumer.live[IDENTITY].key == b
    c = store.begin(IDENTITY, ("a",))
    store.terminate(IDENTITY[:4])
    assert not store.open and store.history[c].status == "INCOMPLETE"
    assert not store.value(c, "a", ("VALID", 1))
    with pytest.raises(Unavailable):
        store.begin(IDENTITY, ())


@pytest.mark.parametrize("value", [float("nan"), object(), list(range(4097)), "x" * 70000, 2**100],
                         ids=["nan", "object", "elements", "bytes", "integer"])
def test_invalid_or_excess_values_rejected(value):
    with pytest.raises(Unavailable):
        freeze(value)


def test_cycle_depth_nodes_image_and_open_scope_budgets():
    cyclic = []
    cyclic.append(cyclic)
    deep = 1
    for _ in range(14):
        deep = [deep]
    for value in [cyclic, deep, [[0] * 4000] * 5]:
        with pytest.raises(Unavailable):
            freeze(value)
    ledger = Ledger(1024)
    with pytest.raises(Unavailable):
        freeze_image(np.zeros((100, 100, 3), np.uint8), ledger)
    assert ledger.used == 0
    store = Results()
    for _ in range(BUDGET.open_scopes):
        store.begin(IDENTITY, ())
    with pytest.raises(Unavailable):
        store.begin(IDENTITY, ())
    assert store.gaps == 1
    assert store.snapshot(IDENTITY)[0].status == "INCOMPLETE"
    assert store.snapshot(IDENTITY)[0].ordinal == BUDGET.open_scopes + 1
    store.terminate(IDENTITY[:4])
    assert not store.open


@pytest.mark.parametrize("repeated,fail", [(False, False), (True, False), (False, True)])
def test_real_runner_local_image_scopes_freeze_and_failure(tmp_path, repeated, fail):
    image_path = tmp_path / "input.png"
    assert cv2.imwrite(str(image_path), np.full((32, 48, 3), 127, np.uint8))
    registry = {"vision.io.image_loader": ImageLoaderOperator, "p0.source": Source, "p0.mutate": Mutator}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(
        ProjectDocument.model_validate(image_project(image_path, repeated, fail=fail)))
    capture = Capture(compiled)
    runner = WorkflowRunner(compiled, registry, eventPublisher=capture.publish, previewSnapshotStore=capture)
    try:
        if fail:
            with pytest.raises(RuntimeError, match="controlled failure"):
                runner.run("main", {}, RunContext.root("job", "main"), CancellationToken())
        else:
            runner.run("main", {}, RunContext.root("job", "main"), CancellationToken())
        assert not capture.results.open
        snapshots = list(capture.results.history.values())
        assert len(snapshots) == (5 if repeated else 1)
        consumers = [Consumer(capture.session), Consumer(capture.session)]
        for snapshot in snapshots:
            for consumer in consumers:
                consumer.receive(snapshot)
            values = dict(snapshot.values)
            if "source.items" in values:
                assert values["source.items"] == ("VALID", ((("count", 7),),))
                assert set(values["load.image"][1][1]) == {127}
                assert set(values["source.image"][1][1]) == {127}
            assert snapshot.status == ("FAILED" if fail else "COMMITTED")
        assert consumers[0].live == consumers[1].live
        if repeated:
            leaves = [s for s in snapshots if s.identity[4] == "leaf"]
            assert sorted(s.ordinal for s in leaves) == [1, 1, 1, 2]
            assert len({s.identity[5] for s in leaves}) == 3
            assert {ctx.iterationPath for event, ctx in capture.events if event == "workflow.started"} >= {(), (0,), (1,)}
        tokens = [token for token, _, _ in capture.frames.values() if token]
        assert len(tokens) == len(set(tokens)) == (4 if repeated else 1)
        assert all(token is None for (key, source), (token, _, _) in capture.frames.items() if source == "source.image")
    finally:
        capture.close()
    assert capture.ledger.used == 0


def test_debug_counters_and_output_are_isolated_without_second_runtime(tmp_path):
    release_store = SqliteStore(tmp_path / "release.db")
    release_store.initialize()
    release = ProjectGlobalCounters(release_store, "same-project")
    release.set("parts", 100)
    output = tmp_path / "release-output"
    output.mkdir()
    (output / "result.txt").write_text("release", encoding="utf-8")
    state = DebugState.create(tmp_path)
    debug = state.counters("same-project")
    operator = GlobalCounterOperator()
    assert operator.executeNode({"increment": True}, {"name": "parts"}, {"globalCounters": debug})["outputs"]["count"] == 1
    assert release.get("parts").value == 100
    assert operator.executeNode({"reset": True}, {"name": "parts"}, {"globalCounters": debug})["outputs"]["count"] == 0
    state.file("result.txt").write_text("debug", encoding="utf-8")
    assert release.get("parts").value == 100
    assert (output / "result.txt").read_text() == "release"
    with pytest.raises(ValueError):
        state.file("../release-output/result.txt")
    # Legacy explicit counter accessor retains its original shared-state behavior.
    assert ProjectGlobalCounters(release_store, "same-project").apply("parts", increment=True).value == 101
