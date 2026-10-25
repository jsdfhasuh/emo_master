"""Schema 2.7 simulated Dispatch demo. No real device or face-model access."""
import argparse
from pathlib import Path

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.models import ProjectDocument


def demoProject(root, *, iterations=None, slots=("detection",)):
    if iterations is not None and iterations <= 0:
        raise ValueError("iterations must be positive; omit it for a continuous While")
    payload = WorkflowStore().toPayload("Simulated camera queue")
    payload["runtime"]["maxConcurrentJobs"] = len(slots) + 1
    payload["devices"]["definitions"]["camera"] = dict(name="Simulated camera",
        deviceTypeId="device.simulated.camera", driverVersion="1.0.0", connection={"serial": "QUEUE-DEMO"},
        defaults=dict(width=320, height=240, exposureUs=8000, gain=0))
    payload["globalVariables"]["running"] = dict(name="Keep running", type="boolean", initialValue=True,
        kind="variable", lifetime="job")
    loop = (dict(contractVersion=2, mode="repeat", repeatCount=iterations, maxIterations=iterations,
                 bodyWorkflowId="capture") if iterations else
            dict(contractVersion=2, mode="while", conditionMode="globalVariable", conditionVariableId="running",
                 unlimited=True, maxIterations=100, timeoutMs=0, cycleBoundary="iteration", bodyWorkflowId="capture"))
    payload["workflows"] = {
        "main": dict(name="Continuous producer", nodes=[dict(nodeId="input", kind="workflow_input"),
            dict(nodeId="loop", kind="loop", loop=loop), dict(nodeId="output", kind="workflow_output")], edges=[]),
        "capture": dict(name="Capture and enqueue", nodes=[dict(nodeId="input", kind="workflow_input"),
            dict(nodeId="camera", operatorId="vision.io.simulated_camera", deviceBindings={"camera": {"deviceId": "camera"}}),
            dict(nodeId="flip", operatorId="vision.preprocess.flip", params=dict(mode="both")),
            dict(nodeId="dispatch", kind="dispatch", inputPorts={"image": "image"}, params=dict(
                queueFullPolicy="wait", waitTimeoutMs=0, targets=[dict(targetSlotId=slot, workflowId="consumer",
                    inputs={"image": "image"}, queueCapacity=1) for slot in slots])),
            dict(nodeId="output", kind="workflow_output")], edges=[
                dict(fromNode="camera", fromPort="image", toNode="flip", toPort="image"),
                dict(fromNode="flip", fromPort="image", toNode="dispatch", toPort="image")]),
        "consumer": dict(name="Simulated analysis and save", inputs={"image": "image"},
            outputs={"image": "image", "count": "number"}, nodes=[dict(nodeId="input", kind="workflow_input"),
            dict(nodeId="count", operatorId="vision.value.number", params=dict(value=1)),
            dict(nodeId="save", operatorId="vision.io.image_saver",
                 params=dict(outputPath=str(Path(root).resolve() / "latest.png"), overwrite=True)),
            dict(nodeId="output", kind="workflow_output")], edges=[
                dict(fromNode="input", fromPort="image", toNode="save", toPort="image"),
                dict(fromNode="input", fromPort="image", toNode="output", toPort="image"),
                dict(fromNode="count", fromPort="value", toNode="output", toPort="count")]),
    }
    payload["workflowOrder"] = ["main", "capture", "consumer"]
    for workflow in payload["workflows"].values():
        workflow["layout"] = dict(nodePositions={node["nodeId"]: dict(x=40 + 240 * index, y=120)
                                                 for index, node in enumerate(workflow["nodes"])})
    versions = {"vision.io.simulated_camera": "1.0.0", "vision.preprocess.flip": "1.0.0",
                "vision.value.number": "1.0.0", "vision.io.image_saver": "1.1.0"}
    payload["dependencies"]["operators"] = [dict(operatorId=key, version=value) for key, value in versions.items()]
    payload["presentation"].update(defaultPageId="consumer", pageOrder=["consumer"],
        resultScopes={"consumption": dict(entryWorkflowId="consumer", scopeWorkflowId="consumer")},
        dataSources={key: dict(kind="workflow_output", resultScopeId="consumption", workflowId="consumer",
                               port=key, expectedType=value) for key, value in {"count": "number", "image": "image"}.items()},
        pages={"consumer": dict(name="Consumer results", components=[
            dict(componentId="count", type="number", bindings={"value": "count"}),
            dict(componentId="image", type="image", bindings={"image": "image"}, layout=dict(row=1))])})
    return ProjectDocument.model_validate(payload)


def saveDemo(root, **kwargs):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "dispatch-queue.emoproj"
    if path.exists():
        raise FileExistsError(path)
    document = demoProject(root, **kwargs)
    saveProject(path, document.model_dump(mode="json"))
    assert loadProjectDocument(path) == document
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--iterations", type=int)
    args = parser.parse_args()
    print(saveDemo(args.directory, iterations=args.iterations))
