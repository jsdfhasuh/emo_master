import json
from types import SimpleNamespace

from emo_master.apps.runtime.presentation.normal_capture import freezeNormalCapture
from emo_master.apps.runtime.presentation.collector import ResultCollector
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.core.project.models import ProjectDocument
from emo_master.core.presentation.validation import validateBindings
from emo_master.apps.designer.page_designer.editing import outputChoices
from tests.runtime.test_global_variables import boundProject, service


def testVariablePageSourceIsTypedAndCapturedWithoutOutputAddress(tmp_path):
    payload = boundProject()
    payload["presentation"] = {
        "pageOrder": ["page"], "defaultPageId": "page",
        "resultScopes": {"scope": {"entryWorkflowId": "main", "scopeWorkflowId": "main"}},
        "dataSources": {"source": {"kind": "global_variable", "resultScopeId": "scope", "variableId": "v", "expectedType": "number"}},
        "pages": {"page": {"name": "Variables", "components": [{"componentId": "value", "type": "number", "bindings": {"value": "source"}}]}},
    }
    document = ProjectDocument.model_validate(payload)
    assert not validateBindings(document, {})
    choices = outputChoices(document, {})
    assert any(choice.source.kind == "global_variable" and choice.source.expectedType == "number" for choice in choices)
    registry = {"vision.value.number": SimpleNamespace(manifest=SimpleNamespace(version="1", outputPorts={"value": "number"}))}
    frozen = freezeNormalCapture(document, registry, tmp_path, "main")
    assert json.loads(frozen.sourceJson)["sources"]["source"]["variableId"] == "v"
    state = service(tmp_path, payload["globalVariables"])
    collector = ResultCollector({"plan": frozen.sourceJson}, lambda event: None)
    collector.globalVariables = state
    context = RunContext.root("job", "main")
    collector.open[(context.workflowRunId, "scope")] = {
        "identity": {"resultKey": "key"}, "expected": ["source"], "values": {}, "bytes": 0,
        "sources": json.loads(frozen.sourceJson)["sources"],
    }
    captured = []
    collector.emit = captured.append
    collector.output(context, {"value": 12}, workflow=True)
    state.set("v", .9)
    collector.end(context, "COMPLETED")
    assert json.loads(captured[0]["sources"][0]["valueJson"]) == .9
    document.presentation.dataSources["source"].expectedType = "boolean"
    assert validateBindings(document, {})
