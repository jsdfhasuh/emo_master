import hashlib
import json
from types import SimpleNamespace
from zipfile import ZipFile

from PySide2.QtCore import Qt

from emo_master.apps.designer.services.debug_artifacts import exportRawResult, exportResult
from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.debug_values import ValueTree
from emo_master.apps.designer.ui.log_record_detail import LogRecordDetail
from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from emo_master.apps.runtime.operator_debug.data import materialize, prepareInputs
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.runtime.test_operator_debug_data import execute
from tests.runtime.test_operator_debug_rpc import openRequest, request, runtime as runtime
from tests.runtime.test_operator_debug_sessions import waitFor


def testDebugVariableOutputQueryCopyExportAndReference(designerApplication, runtime, tmp_path):
    _checkVariableOutput(designerApplication, runtime, tmp_path, asset=False)


def testDebugJsonAssetPreviewSafePackageAndOriginalReference(designerApplication, runtime, tmp_path):
    _checkVariableOutput(designerApplication, runtime, tmp_path, asset=True)


def _checkVariableOutput(designerApplication, runtime, tmp_path, *, asset):
    operatorId = "vision.state.variable_read"
    source = "rtsp://fixture-user:fixture-password@camera.invalid/live?token=fixture-token&q=business"
    safe = "rtsp://<redacted>@camera.invalid/live?token=<redacted>&q=business"
    if asset:
        source += " " + "x" * 4100
        safe += " " + "x" * 4100
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
    assert record["outputs"] == ({} if asset else {"value": safe})

    detail = LogRecordDetail()
    detail.setDebugResult(record, sessionId=opened.session_id, generation=opened.generation)
    rawText = detail.rawTree.toPlainText()
    detail.copyRecordButton.click()
    copied = designerApplication.clipboard().text()
    assert json.loads(rawText) == json.loads(copied) == record

    selected = SimpleNamespace(records=[record], selectedOutputPort=lambda: "value",
                               versions=SimpleNamespace(currentIndex=lambda: 0))
    selection = OperatorDebugWindow.exportSelection(selected)
    connection = DebugConnection(RuntimeClient(runtime))
    connection.identity = dict(runtime_instance_id=runtime.runtimeInstanceId, **identity)
    rawPath, archivePath = tmp_path / "output.json", tmp_path / "output.zip"
    exportRawResult(rawPath, selection, connection)
    exportResult(archivePath, selection, connection)
    exported = rawPath.read_text(encoding="utf-8")
    with ZipFile(archivePath) as archive:
        resultText = archive.read("value.json").decode()
        metadata = archive.read("metadata.json").decode()
    assert json.loads(resultText) == safe
    assert json.loads(exported) == (source if asset else safe)
    package = json.loads(metadata)
    assert package["identity"]["executionId"] == record["executionId"]
    assert package["bytes"] == len(resultText.encode())
    assert package["sha256"] == hashlib.sha256(resultText.encode()).hexdigest()
    if asset:
        assetId = record["outputAssets"]["value"]["assetId"]
        downloaded = connection.download(assetId)
        preview = SimpleNamespace(disposed=False, closing=False, outputTree=ValueTree(),
                                  outputPorts=SimpleNamespace(currentData=lambda: assetId),
                                  refreshControls=lambda: None, drainOutput=lambda: None)
        OperatorDebugWindow.completed(preview, "download:" + assetId, downloaded, None)
        assert preview.outputTree.topLevelItem(0).data(1, Qt.UserRole + 1) == safe
        assert downloaded["content"] == rawPath.read_bytes()
    for private in ("fixture-user", "fixture-password", "fixture-token"):
        assert private not in rawText + copied + resultText + metadata

    manager = runtime.operatorDebugManager
    with manager.lock:
        session = manager.sessions[opened.session_id]
        executionId = record["executionId"]
        assert session.executions[executionId]["outputs"] == ({} if asset else {"value": source})
        frozen, sources = prepareInputs(
            {"input": {"outputRef": {"executionId": executionId, "port": "value"}}},
            {"input": "string"}, session.assets, session.executions,
        )
        assert sources["input"]["captureId"] == executionId
        assert materialize(frozen, session.assets) == {"input": source}
