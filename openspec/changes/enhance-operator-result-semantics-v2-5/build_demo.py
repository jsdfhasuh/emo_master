"""Generate the device-free PR20 desktop walkthrough project from this checkout."""
from argparse import ArgumentParser
from pathlib import Path

import emo_master
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.dependencies import validateOperatorDependencies
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler


NUMBER = "vision.value.number"
COMPARE = "vision.compare.number"
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = ROOT / "manual_test_workspace" / "pr20-demo" / "run-details.emoproj"


def buildDemo(target: Path) -> ProjectDocument:
    scan = PluginRegistry(emo_master.__version__).scan(Path(emo_master.__file__).parent / "plugins")
    registered = scan.activeOperators
    store = WorkflowStore()
    workflow = store.workflows[store.entryWorkflowId]
    workflow.name = "数值比较演示"
    workflow.outputs = {"result": "boolean"}

    def operator(nodeId, operatorId, displayName, params):
        manifest = registered[operatorId].manifest
        return dict(nodeId=nodeId, kind="operator", operatorId=operatorId, displayName=displayName,
                    params=params, paramSchema=manifest.paramSchema,
                    inputPorts={key: spec["type"] for key, spec in manifest.inputPorts.items()},
                    outputPorts={key: spec["type"] for key, spec in manifest.outputPorts.items()})

    zero = operator("zero", NUMBER, "数值 0", {"value": 0})
    compare = operator("compare", COMPARE, "比较阈值", {"operator": "gt", "rightValue": 999})
    compare["globalVariableBindings"] = [{"parameterPath": ["rightValue"], "variableId": "demo-limit"}]
    workflow.nodes.extend((zero, compare))
    workflow.edges.extend((
        dict(fromNode="zero", fromPort="value", toNode="compare", toPort="left"),
        dict(fromNode="compare", fromPort="result", toNode="__workflow_output__", toPort="result"),
    ))
    workflow.layout["nodePositions"] = {
        "__workflow_input__": {"x": 40, "y": 220}, "zero": {"x": 40, "y": 30},
        "compare": {"x": 320, "y": 30}, "__workflow_output__": {"x": 620, "y": 30},
    }
    store.projectExtensions["globalVariables"] = {
        "demo-limit": {"name": "演示阈值", "type": "number", "kind": "constant", "initialValue": 5},
    }
    store.dependencies["operators"] = [
        {"operatorId": key, "version": registered[key].manifest.version} for key in (NUMBER, COMPARE)
    ]

    document = ProjectDocument.model_validate(store.toPayload("PR20 算子运行详情演示"))
    validateOperatorDependencies(document, registered)
    WorkflowCompiler(registered, deviceTypes=scan.deviceTypes).compile(document)
    saveProject(target, document.model_dump(mode="json"))
    reloaded = loadProjectDocument(target)
    if reloaded != document:
        raise RuntimeError("Saved project does not match the validated document")
    return reloaded


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", type=Path, default=DEFAULT_PATH)
    path = parser.parse_args().project
    buildDemo(path)
    print(path.resolve())
