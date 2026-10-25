"""Schema 2.7 drafts for PatchCore Runtime and Designer integration tests."""
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.project.models import ProjectDocument
from emo_master.plugins.builtins.flow_if.operator import FlowIfOperator
from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator
from emo_master.plugins.builtins.patchcore_inference.operator import PatchCoreInferenceOperator
from emo_master.plugins.builtins.result_writer.operator import ResultWriterOperator
from tests.runtime.test_builtin_vision_operator_workflows import _edge


def project(modelPath, *, outputPath=None, **params):
    store = WorkflowStore()
    workflow = store.get("main")
    workflow.inputs = {"image": "image"}
    workflow.outputs = {"score": "number", "isAnomaly": "boolean", "label": "string"}
    start, end = [next(n["nodeId"] for n in workflow.nodes if n["kind"] == kind)
                  for kind in ("workflow_input", "workflow_output")]

    def add(nodeId, meta, values):
        workflow.nodes.insert(-1, dict(nodeId=nodeId, operatorId=meta.operatorId, displayName=meta.displayName,
            params=values, inputPorts={k: normalizePortType(v) for k, v in meta.inputPorts.items()},
            outputPorts={k: normalizePortType(v) for k, v in meta.outputPorts.items()}, paramSchema=meta.paramSchema))
        if not any(row["operatorId"] == meta.operatorId for row in store.dependencies["operators"]):
            store.dependencies["operators"].append(dict(operatorId=meta.operatorId, version=meta.version))

    add("patchcore", PatchCoreInferenceOperator.meta, dict(modelPath=str(modelPath), **params))
    workflow.edges = [_edge(start, "image", "patchcore", "image"),
                      *[_edge("patchcore", port, end, port) for port in workflow.outputs]]
    if outputPath is not None:
        add("save", ImageSaverOperator.meta, dict(outputPath=str(outputPath)))
        add("score", ResultWriterOperator.meta, dict(inputType="numberValue", relativePath="results/score", overwrite=True))
        add("branch", FlowIfOperator.meta, dict(mode="bool"))
        for label in ("OK", "NG"):
            add(label, ResultWriterOperator.meta, dict(inputType="booleanValue", relativePath="results/" + label, overwrite=True))
        workflow.edges.extend([
            _edge("patchcore", "overlay", "save", "image"),
            _edge("patchcore", "score", "score", "numberValue"),
            _edge("patchcore", "isAnomaly", "branch", "value"),
            _edge("branch", "true", "NG", "booleanValue"),
            _edge("branch", "false", "OK", "booleanValue"),
        ])
    workflow.layout["nodePositions"] = {
        node["nodeId"]: {"x": float(20 + index * 360), "y": 80.0}
        for index, node in enumerate(workflow.nodes)
    }
    document = ProjectDocument.model_validate(store.toPayload("PatchCore Test"))
    assert document.schemaVersion == "2.7" and document.devices.definitions == {}
    return document
