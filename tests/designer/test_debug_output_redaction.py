import json
from types import SimpleNamespace
from zipfile import ZipFile

from emo_master.apps.designer.services.debug_artifacts import exportRawResult, exportResult
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.log_record_detail import LogRecordDetail
from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from emo_master.apps.runtime.operator_debug.data import materialize, prepareInputs
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.runtime.test_operator_debug_data import execute
from tests.runtime.test_operator_debug_rpc import openRequest, request, runtime as runtime
from tests.runtime.test_operator_debug_sessions import waitFor


def testDebugVariableOutputQueryCopyExportAndReference(designerApplication, runtime, tmp_path):
    operatorId = "vision.state.variable_read"
    source = "rtsp://fixture-user:fixture-password@camera.invalid/live?token=fixture-token&q=business"
    safe = "rtsp://<redacted>@camera.invalid/live?token=<redacted>&q=business"
    params = {"variableId": "stream"}
    payload = WorkflowStore().toPayload("Debug output redaction")
    payload["globalVariables"] = {"stream": {"name": "Stream", "type": "string", "initialValue": source}}
    payload["workflows"]["main"]["nodes"].append({"nodeId": "node", "operatorId": operatorId, "params": params})
    document = ProjectDocument.model_validate(payload)
    WorkflowCompiler(runtime.pluginScanResult.activeOperators).compile(document)
    opened = runtime.OpenOperatorDebugSession(openRequest(
        runtime, operatorId, project_json=json.dumps(payload), project_id=document.project.projectId,
    ), None)
    assert opened.ok, opened.message
    identity = dict(session_id=opened.session_id, generation=opened.generation)
    waitFor(lambda: runtime.GetOperatorDebugSession(request(runtime, **identity), None).state == "READY")

    record = execute(runtime, identity, params, {})
    assert record["status"] == "SUCCEEDED"
    assert record["outputs"] == {"value": safe}

    detail = LogRecordDetail()
    detail.setDebugResult(record, sessionId=opened.session_id, generation=opened.generation)
    rawText = detail.rawTree.toPlainText()
    detail.copyRecordButton.click()
    copied = designerApplication.clipboard().text()
    assert json.loads(rawText) == json.loads(copied) == record

    selected = SimpleNamespace(records=[record], selectedOutputPort=lambda: "value",
                               versions=SimpleNamespace(currentIndex=lambda: 0))
    selection = OperatorDebugWindow.exportSelection(selected)
    rawPath, archivePath = tmp_path / "output.json", tmp_path / "output.zip"
    exportRawResult(rawPath, selection, None)
    exportResult(archivePath, selection, None)
    exported = rawPath.read_text(encoding="utf-8")
    with ZipFile(archivePath) as archive:
        resultText = archive.read("value.json").decode()
        metadata = archive.read("metadata.json").decode()
    assert json.loads(exported) == json.loads(resultText) == safe
    assert json.loads(metadata)["identity"]["executionId"] == record["executionId"]
    for private in ("fixture-user", "fixture-password", "fixture-token"):
        assert private not in rawText + copied + exported + resultText + metadata

    manager = runtime.operatorDebugManager
    with manager.lock:
        session = manager.sessions[opened.session_id]
        executionId = record["executionId"]
        assert session.executions[executionId]["outputs"] == {"value": source}
        frozen, sources = prepareInputs(
            {"input": {"outputRef": {"executionId": executionId, "port": "value"}}},
            {"input": "string"}, session.assets, session.executions,
        )
        assert sources["input"]["captureId"] == executionId
        assert materialize(frozen, session.assets) == {"input": source}
