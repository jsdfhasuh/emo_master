from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3

import pytest

from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables, legacyDefinitions
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.core.project.global_variables import VariableDefinition, VariableError
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from emo_master.plugins.builtins.number_value.operator import NumberValueOperator
from tests.runtime.test_workflow_loop_contracts_v2 import _whilePayload, _IncrementOperator


def variable(value=0, type="integer", lifetime="persistent", kind="variable", name="value"):
    return dict(name=name, type=type, initialValue=value, lifetime=lifetime, kind=kind)


def service(tmp_path, values=None, job="job"):
    store = SqliteStore(tmp_path / "state.db")
    store.initialize()
    accessor = ProjectGlobalVariables(store, "project", values or {"v": variable()}, job)
    accessor.synchronize()
    accessor.initializeJob()
    return accessor


@pytest.mark.parametrize("type,value", [("boolean", 1), ("integer", True), ("integer", 2**63),
                                        ("number", float("nan")), ("string", False)])
def testStrictVariableTypes(type, value):
    with pytest.raises(ValueError):
        VariableDefinition.model_validate(variable(value, type))


def testConstantsAndPreviewAreReadOnly(tmp_path):
    accessor = service(tmp_path, {"v": variable(True, "boolean", kind="constant")})
    assert accessor.get("v") is True
    for action in (lambda: accessor.set("v", False), lambda: accessor.reset("v"), lambda: accessor.increment("v")):
        with pytest.raises(VariableError):
            action()
    preview = accessor.snapshot()
    assert preview.get("v") is True
    with pytest.raises(VariableError, match="preview"):
        preview.set("v", False)


def testPersistenceInitialValueAndCompareSet(tmp_path):
    accessor = service(tmp_path)
    before = accessor.records(["v"])["v"]
    accessor.set("v", 42, before.revision)
    with pytest.raises(VariableError, match="refresh"):
        accessor.set("v", 99, before.revision)
    reopened = service(tmp_path, {"v": variable(7)})
    assert reopened.get("v") == 42
    assert reopened.reset("v") == 7


def testJobValuesAreIsolatedAndInitializedOnce(tmp_path):
    a = service(tmp_path, {"v": variable(1, lifetime="job")}, "a")
    a.set("v", 4)
    a.initializeJob()
    b = service(tmp_path, {"v": variable(1, lifetime="job")}, "b")
    assert a.get("v") == 4 and b.get("v") == 1
    with pytest.raises(VariableError, match="select a job"):
        ProjectGlobalVariables(a.store, "project", a.definitions).get("v")


def testConcurrentIncrementsShareStateAndLegacyAdapter(tmp_path):
    accessor = service(tmp_path)
    with ThreadPoolExecutor(4) as executor:
        list(executor.map(lambda _: accessor.increment("v"), range(40)))
    assert accessor.get("v") == 40
    counter = accessor.store.setGlobalCounter("project", "parts", 41)
    legacy = ProjectGlobalVariables(accessor.store, "project", legacyDefinitions(accessor.store, "project"))
    assert legacy.get("counter:parts") == 41
    legacy.increment("counter:parts")
    assert accessor.store.getGlobalCounter("project", "parts").value == 42
    assert counter.updatedAtMs > 0
    accessor.store.initialize()
    assert accessor.store.getGlobalCounter("project", "parts").value == 42


def boundProject():
    source = _whilePayload()
    source["workflowOrder"] = ["main"]
    source["workflows"] = {"main": {"name": "Main", "inputs": {}, "outputs": {"value": "number"},
        "nodes": [{"nodeId": "input", "kind": "workflow_input"},
                  {"nodeId": "number", "kind": "operator", "operatorId": "vision.value.number", "params": {"value": 0}},
                  {"nodeId": "output", "kind": "workflow_output"}],
        "edges": [{"fromNode": "number", "fromPort": "value", "toNode": "output", "toPort": "value"}]}}
    payload = migrateProjectPayload(source, enableGlobalVariables=True)
    payload["globalVariables"] = {"v": variable(0.5, "number")}
    payload["workflows"]["main"]["nodes"][1]["globalVariableBindings"] = [{"parameterPath": ["value"], "variableId": "v"}]
    return payload


def testBindingReadsBeforeEveryExecutionAndPreservesLiterals(tmp_path):
    payload = boundProject()
    compiled = WorkflowCompiler({"vision.value.number": NumberValueOperator}).compile(payload)
    accessor = service(tmp_path, payload["globalVariables"])
    runner = WorkflowRunner(compiled, {"vision.value.number": NumberValueOperator}, globalVariables=accessor)
    def run():
        return runner.run("main", {}, RunContext.root("job", "main"), CancellationToken()).outputs["value"]
    assert run() == 0.5
    accessor.set("v", 0.8)
    assert run() == 0.8
    assert payload["workflows"]["main"]["nodes"][1]["params"]["value"] == 0
    assert compiled.workflows["main"].nodeById["number"].params["value"] == 0


def testBindingUnknownAndTypeMismatchFailCompilation():
    for value in ({}, {"v": variable(True, "boolean")}):
        payload = boundProject()
        payload["globalVariables"] = value
        with pytest.raises(WorkflowCompileError):
            WorkflowCompiler({"vision.value.number": NumberValueOperator}).compile(payload)


def testGlobalWhileReadsTheLiveFlagEachIteration(tmp_path):
    source = _whilePayload()
    payload = migrateProjectPayload(source, enableGlobalVariables=True)
    payload["workflowOrder"] = ["main", "body"]
    del payload["workflows"]["condition"]
    payload["globalVariables"] = {"enabled": variable(True, "boolean")}
    payload["workflows"]["main"]["nodes"][1]["loop"].update(conditionMode="globalVariable", conditionVariableId="enabled")
    accessor = service(tmp_path, payload["globalVariables"])
    class Increment(_IncrementOperator):
        def executeNode(self, inputs, params, context):
            result = super().executeNode(inputs, params, context)
            if result["outputs"]["count"] == 2:
                context["globalVariables"].set("enabled", False)
            return result
    compiled = WorkflowCompiler({"test.increment": Increment}).compile(payload)
    runner = WorkflowRunner(compiled, {"test.increment": Increment}, globalVariables=accessor)
    assert runner.run("main", {"count": 0}, RunContext.root("job", "main"), CancellationToken()).outputs == {"count": 2}
    assert runner.run("main", {"count": 0}, RunContext.root("job", "main"), CancellationToken()).outputs == {"count": 0}


def testOldProjectDoesNotUpgradeOnRead():
    original = _whilePayload()
    canonical = migrateProjectPayload(original)
    assert canonical["schemaVersion"] == "2.1"
    assert "globalVariables" not in canonical
    assert all("globalVariableBindings" not in node for w in canonical["workflows"].values() for node in w["nodes"])
    before = deepcopy(original)
    upgraded = migrateProjectPayload(original, enableGlobalVariables=True)
    assert original == before
    assert upgraded["schemaVersion"] == "2.4"
    assert upgraded["production"]["autoStart"] is False


def testMigrationPreservesOldCounterTimestamp(tmp_path):
    store = SqliteStore(tmp_path / "old.db")
    store.initialize()
    with sqlite3.connect(store.dbPath) as connection:
        connection.execute("DROP VIEW globalCounters")
        connection.execute("ALTER TABLE globalCountersLegacy RENAME TO globalCounters")
        connection.execute("INSERT INTO globalCounters VALUES ('p', 'old', 17, 123)")
        connection.execute("DELETE FROM schemaMigrations WHERE version=5")
    store.initialize()
    record = store.getGlobalCounter("p", "old")
    assert (record.value, record.updatedAtMs) == (17, 123)
