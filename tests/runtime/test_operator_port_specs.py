import json

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.plugin.models import (
    PluginDescriptor,
    PluginManifest,
    RegistryScanResult,
)
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler


_INPUT_SPEC = {
    "type": "geometry2d",
    "required": False,
    "nullable": True,
    "schemaVersion": "1.x",
}
_OUTPUT_SPEC = {
    "type": "detectionCollection",
    "required": True,
    "schemaVersion": "1.1",
}


class _SpecOperator:
    class Meta:
        operatorId = "vision.demo.spec"
        displayName = "Spec Operator"
        version = "0.1.0"
        inputPorts = {"roi": _INPUT_SPEC}
        outputPorts = {"detections": _OUTPUT_SPEC}
        paramSchema = {"type": "object"}

    meta = Meta()

    @staticmethod
    def validateParams(params):
        _ = params
        return None

    def executeNode(self, inputs, params, runtimeContext):
        _ = inputs
        _ = params
        _ = runtimeContext
        return {"status": "ok", "outputs": {}}


def _descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        PluginManifest(
            operatorId="vision.demo.spec",
            displayName="Spec Operator",
            version="0.1.0",
            entry="demo.operator:SpecOperator",
            category="测试",
            iconKey="spec",
            summary="port spec",
            inputPorts={"roi": dict(_INPUT_SPEC)},
            outputPorts={"detections": dict(_OUTPUT_SPEC)},
            paramSchema={"type": "object"},
            minCoreVersion="0.2.0",
            maxCoreVersion="1.x",
        ),
        _SpecOperator,
    )


def _project() -> ProjectDocument:
    return ProjectDocument.model_validate(
        {
            "schemaVersion": "2.1",
            "project": {
                "projectId": "port-spec-project",
                "name": "Port Spec",
                "revision": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": "Main",
                    "inputs": {},
                    "outputs": {},
                    "nodes": [
                        {"nodeId": "input", "kind": "workflow_input"},
                        {
                            "nodeId": "spec",
                            "kind": "operator",
                            "operatorId": "vision.demo.spec",
                        },
                        {"nodeId": "output", "kind": "workflow_output"},
                    ],
                    "edges": [],
                    "layout": {"nodePositions": {}},
                }
            },
            "runtime": {},
            "dependencies": {"operators": []},
            "devices": {"bindings": {}},
        }
    )


def testCompilerPreservesRegisteredPortSpecs() -> None:
    descriptor = _descriptor()
    compiled = WorkflowCompiler(
        operatorRegistry={descriptor.manifest.operatorId: descriptor}
    ).compile(_project())
    node = compiled.workflows["main"].nodeById["spec"]

    assert node.inputPorts["roi"] == _INPUT_SPEC
    assert node.outputPorts["detections"] == _OUTPUT_SPEC


def testListOperatorsKeepsLegacyTypesAndExposesFullSpecs() -> None:
    descriptor = _descriptor()
    service = object.__new__(RuntimeService)
    service.pluginScanResult = RegistryScanResult(
        activeOperators={descriptor.manifest.operatorId: descriptor},
        rejectedOperators={},
    )

    reply = RuntimeService.ListOperators(
        service, runtime_pb2.ListOperatorsRequest(), None
    )
    info = reply.operators[0]

    assert dict(info.input_ports) == {"roi": "geometry2d"}
    assert dict(info.output_ports) == {"detections": "detectionCollection"}
    assert json.loads(info.input_port_specs_json) == {"roi": _INPUT_SPEC}
    assert json.loads(info.output_port_specs_json) == {
        "detections": _OUTPUT_SPEC
    }

    class ServiceStub:
        def ListOperators(self, request, context):
            _ = request
            _ = context
            return reply

    definition = RuntimeClient(ServiceStub()).listOperators()[0]
    assert definition.inputPorts == {"roi": "geometry2d"}
    assert definition.outputPorts == {"detections": "detectionCollection"}
    assert definition.inputPortSpecs == {"roi": _INPUT_SPEC}
    assert definition.outputPortSpecs == {"detections": _OUTPUT_SPEC}


def testOptionalOnlySubflowExecutesWithoutInput() -> None:
    payload = _project().model_dump(mode="python")
    payload["workflowOrder"] = ["main", "child"]
    payload["workflows"] = {
        "main": {
            "name": "Main",
            "inputs": {},
            "outputs": {},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {
                    "nodeId": "child",
                    "kind": "subflow",
                    "targetWorkflowId": "child",
                },
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [],
            "layout": {"nodePositions": {}},
        },
        "child": {
            "name": "Child",
            "inputs": {"optional": {"type": "object", "required": False}},
            "outputs": {},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [],
            "layout": {"nodePositions": {}},
        },
    }
    document = ProjectDocument.model_validate(payload)
    compiled = WorkflowCompiler().compile(document)
    events: list[str] = []
    runner = WorkflowRunner(
        compiled,
        {},
        eventPublisher=lambda **event: events.append(str(event["eventType"])),
    )

    runner.run(
        "main",
        {},
        RunContext.root("job", "main"),
        CancellationToken(),
    )

    assert "subflow.started" in events
    assert "subflow.completed" in events
