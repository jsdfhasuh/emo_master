import json
from pathlib import Path
import multiprocessing

import pytest

from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.preview.global_variables import previewParameters
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.global_variables import VariableError
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.variable_read.operator import ReadVariableOperator
from emo_master.plugins.builtins.variable_write.operator import WriteVariableOperator
from emo_master.plugins.builtins.number_value.operator import NumberValueOperator
from tests.runtime.test_global_variables import boundProject, service, variable


def _incrementProcess(path):
    state = ProjectGlobalVariables(SqliteStore(Path(path)), "project", {"v": variable()})
    for _ in range(20):
        state.increment("v")


def testSharedDatabaseAcrossSpawnProcesses(tmp_path):
    state = service(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    children = [ctx.Process(target=_incrementProcess, args=(str(state.store.dbPath),)) for _ in range(2)]
    for process in children:
        process.start()
    for process in children:
        process.join(20)
        assert process.exitcode == 0
    assert state.get("v") == 40


def testGlobalVariableRpcJobIsolationCasAndLegacyState(tmp_path):
    payload = boundProject()
    payload["globalVariables"].update(flag=variable(True, "boolean", "job", name="runEnabled"),
                                      always=variable(True, "boolean", kind="constant", name="alwaysTrue"))
    projectId = payload["project"]["projectId"]
    file = tmp_path / "project.json"
    file.write_text(json.dumps(payload), encoding="utf-8")
    runtime = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(file)), None).ok
        def request(**kwargs):
            return pb.GlobalVariablesRequest(project_id=projectId, **kwargs)
        response = runtime.ListGlobalVariables(request(), None)
        rows = {row["variableId"]: row for row in json.loads(response.variables_json)}
        assert rows["flag"]["state"] == "initial"
        assert rows["flag"]["value"] is None
        assert runtime.GetGlobalVariable(request(variable_id="flag"), None).code == "E_VARIABLE_JOB_REQUIRED"
        changed = runtime.SetGlobalVariable(request(variable_id="v", value_json="0.7", expected_revision=1), None)
        assert changed.ok
        assert runtime.SetGlobalVariable(request(variable_id="v", value_json="0.9", expected_revision=1), None).code == "E_VARIABLE_CONFLICT"
        assert runtime.SetGlobalVariable(request(variable_id="always", value_json="false", expected_revision=0), None).code == "E_VARIABLE_READ_ONLY"
        assert runtime.SetGlobalVariable(request(variable_id="v", value_json="true", expected_revision=2), None).code == "E_VARIABLE_TYPE"
        assert runtime.SetGlobalVariable(request(variable_id="v", value_json="0.9"), None).code == "E_VARIABLE_REVISION_REQUIRED"
        job = runtime.jobManager.createJob(projectId, 1, "main")
        accessor = ProjectGlobalVariables(runtime.sqliteStore, projectId, payload["globalVariables"], job.jobId)
        accessor.initializeJob()
        assert runtime.SetGlobalVariable(request(variable_id="flag", job_id=job.jobId, value_json="false", expected_revision=1), None).ok
        runtime.jobRepository.update(job.jobId, status="COMPLETED")
        assert runtime.ResetGlobalVariable(request(variable_id="flag", job_id=job.jobId, expected_revision=2), None).code == "E_VARIABLE_JOB_ENDED"
        assert runtime.GetGlobalVariable(request(variable_id="flag", job_id="other"), None).code == "E_VARIABLE_JOB_UNKNOWN"
        runtime.sqliteStore.setGlobalCounter(projectId, "parts", 37)
        legacy = runtime.GetGlobalVariable(request(variable_id="counter:parts"), None)
        assert json.loads(legacy.variables_json)[0]["value"] == 37
        payload["globalVariables"]["v"]["initialValue"] = 9.0
        file.write_text(json.dumps(payload), encoding="utf-8")
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(file)), None).ok
        assert json.loads(runtime.GetGlobalVariable(request(variable_id="v"), None).variables_json)[0]["value"] == .7
        assert runtime.ResetGlobalVariable(request(variable_id="v", expected_revision=2), None).ok
        assert json.loads(runtime.GetGlobalVariable(request(variable_id="v"), None).variables_json)[0]["value"] == 9.0
    finally:
        runtime.close()


def testPreviewSnapshotDoesNotInitializeOrWrite(tmp_path):
    payload = boundProject()
    state = service(tmp_path, payload["globalVariables"])
    state.set("v", .7)
    document = ProjectDocument.model_validate(payload)
    # Align project namespace with the fixture service.
    document.project.projectId = "project"
    schema = NumberValueOperator.meta.paramSchema
    snapshot = previewParameters(state.store, document, "main", "number", {"value": 0}, schema)
    state.set("v", .9)
    assert snapshot == {"value": .7}
    assert previewParameters(state.store, document, "main", "number", {"value": 0}, schema) == {"value": .9}
    node = document.workflows["main"].nodes[1]
    node.operatorId = "vision.state.variable_write"
    with pytest.raises(VariableError, match="preview cannot"):
        previewParameters(state.store, document, "main", "number", {}, {})
    assert state.get("v") == .9


@pytest.mark.parametrize("value,valueType", [(True, "boolean"), (23, "integer"), (.25, "number"), ("hello", "string")])
def testTypedVariableReadAndRequiredWrite(value, valueType, tmp_path):
    payload = boundProject()
    payload["globalVariables"] = {"v": variable(value, valueType)}
    workflow = payload["workflows"]["main"]
    workflow["inputs"] = {"value": valueType}
    workflow["outputs"] = {"value": valueType}
    workflow["nodes"][1] = {"nodeId": "number", "kind": "operator", "operatorId": "vision.state.variable_write", "params": {"variableId": "v"}}
    workflow["edges"].insert(0, dict(fromNode="input", fromPort="value", toNode="number", toPort="value"))
    registry = {"vision.state.variable_write": WriteVariableOperator, "vision.state.variable_read": ReadVariableOperator}
    compiled = WorkflowCompiler(registry).compile(payload)
    state = service(tmp_path, payload["globalVariables"])
    runner = WorkflowRunner(compiled, registry, globalVariables=state)
    assert runner.run("main", {"value": value}, RunContext.root("job", "main"), CancellationToken()).outputs == {"value": value}
    assert compiled.workflows["main"].nodeById["number"].inputPorts["value"]["required"] is True
    workflow["nodes"][1]["operatorId"] = "vision.state.variable_read"
    workflow["edges"].pop(0)
    runner = WorkflowRunner(WorkflowCompiler(registry).compile(payload), registry, globalVariables=state)
    assert runner.run("main", {"value": value}, RunContext.root("job", "main"), CancellationToken()).outputs == {"value": value}


def testSingleInvocationSnapshotAndNextInvocationRefresh(tmp_path):
    payload = boundProject()
    state = service(tmp_path, payload["globalVariables"])
    class ChangesDuringExecution(NumberValueOperator):
        def executeNode(self, inputs, params, context):
            context["globalVariables"].set("v", .9)
            return super().executeNode(inputs, params, context)
    registry = {"vision.value.number": ChangesDuringExecution}
    runner = WorkflowRunner(WorkflowCompiler(registry).compile(payload), registry, globalVariables=state)
    assert runner.run("main", {}, RunContext.root("job", "main"), CancellationToken()).outputs["value"] == .5
    assert runner.run("main", {}, RunContext.root("job", "main"), CancellationToken()).outputs["value"] == .9


def testBindingResourceOverlapAndFullParameterValidation():
    from emo_master.core.workflow.errors import WorkflowCompileError
    payload = boundProject()
    payload["resources"]["siteBindings"] = [{"field": "external", "purpose": "device_address", "target": {
        "workflowId": "main", "nodeId": "number", "parameterPath": ["value"]}}]
    with pytest.raises(WorkflowCompileError) as raised:
        WorkflowCompiler({"vision.value.number": NumberValueOperator}).compile(payload)
    assert any(issue.code == "E_VARIABLE_BINDING_CONFLICT" for issue in raised.value.issues)


@pytest.mark.parametrize("flag,limit", [(False, False), (True, True)])
def testBooleanConstantWhileZeroIterationsOrSafetyLimit(flag, limit):
    from emo_master.core.project.migration import migrateProjectPayload
    from emo_master.apps.runtime.workflow.loop_runner import LoopExecutionError
    from tests.runtime.test_workflow_loop_contracts_v2 import _whilePayload, _IncrementOperator
    payload = migrateProjectPayload(_whilePayload(), enableGlobalVariables=True)
    payload["workflowOrder"] = ["main", "body"]
    del payload["workflows"]["condition"]
    payload["globalVariables"] = {"constant": variable(flag, "boolean", kind="constant")}
    payload["workflows"]["main"]["nodes"][1]["loop"].update(conditionMode="globalVariable", conditionVariableId="constant", maxIterations=2)
    registry = {"test.increment": _IncrementOperator}
    runner = WorkflowRunner(WorkflowCompiler(registry).compile(payload), registry)
    if limit:
        with pytest.raises(LoopExecutionError) as raised:
            runner.run("main", {"count": 0}, RunContext.root("job", "main"), CancellationToken())
        assert raised.value.code == "E_LOOP_LIMIT_REACHED"
    else:
        assert runner.run("main", {"count": 0}, RunContext.root("job", "main"), CancellationToken()).outputs == {"count": 0}
    cancel = CancellationToken()
    cancel.cancel()
    from emo_master.apps.runtime.workflow.cancellation import CancellationRequested
    with pytest.raises(CancellationRequested):
        runner.run("main", {"count": 0}, RunContext.root("job", "main"), cancel)
