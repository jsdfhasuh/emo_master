from copy import deepcopy

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.project.models import ProjectDocument


def fileVideoProject(registry):
    def edge(source, port, target, targetPort):
        return dict(fromNode=source, fromPort=port, toNode=target, toPort=targetPort)

    def operator(nodeId, operatorId, params):
        manifest = registry[operatorId].manifest
        return dict(nodeId=nodeId, kind="operator", operatorId=operatorId, params=params,
                    displayName=manifest.displayName, paramSchema=deepcopy(manifest.paramSchema),
                    inputPorts={key: normalizePortType(value) for key, value in manifest.inputPorts.items()},
                    outputPorts={key: normalizePortType(value) for key, value in manifest.outputPorts.items()})

    def workflow(name, inputs, outputs, nodes, edges):
        nodes = [dict(nodeId="input", kind="workflow_input", outputPorts=inputs), *nodes,
                 dict(nodeId="output", kind="workflow_output", inputPorts=outputs)]
        return dict(name=name, inputs=inputs, outputs=outputs, nodes=nodes, edges=edges,
                    layout=dict(nodePositions={node["nodeId"]: {"x": index * 300, "y": 100}
                                               for index, node in enumerate(nodes)}))

    state = dict(hasNext="boolean", videoPath="string", reset="boolean")
    workflows = {
        "main": workflow("List files", {}, {"videoPath": "list<string>"}, [
            operator("files", "vision.io.file_list", {"folderPath": "videos", "patterns": ["*.mp4", "*.avi"]}),
            dict(nodeId="batch", kind="subflow", targetWorkflowId="batch",
                 inputPorts={"items": "list<string>"}, outputPorts={"videoPath": "list<string>"}),
        ], [edge("files", "paths", "batch", "items"), edge("batch", "videoPath", "output", "videoPath")]),
        "batch": workflow("ForEach files", {"items": "list<string>"}, {"videoPath": "list<string>"}, [
            dict(nodeId="foreach", kind="loop", inputPorts={"items": "list<string>"},
                 outputPorts={"videoPath": "list<string>"},
                 loop=dict(contractVersion=2, mode="foreach", bodyWorkflowId="one_video",
                           itemInputPort="videoPath", maxIterations=10000, timeoutMs=0)),
        ], [edge("input", "items", "foreach", "items"), edge("foreach", "videoPath", "output", "videoPath")]),
        "one_video": workflow("One video", {"videoPath": "string"}, {"videoPath": "string"}, [
            operator("one", "vision.value.number", {"value": 1}),
            operator("initial", "vision.compare.number", {"operator": "eq", "rightValue": 1}),
            dict(nodeId="while", kind="loop", inputPorts=state, outputPorts=state,
                 loop=dict(contractVersion=2, mode="while", bodyWorkflowId="frame", maxIterations=10000,
                           timeoutMs=0, conditionMode="boolean", conditionPort="hasNext")),
        ], [edge("one", "value", "initial", "left"), edge("initial", "result", "while", "hasNext"),
            edge("initial", "result", "while", "reset"), edge("input", "videoPath", "while", "videoPath"),
            edge("while", "videoPath", "output", "videoPath")]),
        "frame": workflow("Sample and process", state, state, [
            operator("load", "vision.io.video_loader", {"videoPath": "old-missing-draft.mp4", "intervalSec": .25}),
            operator("blur", "vision.preprocess.blur", {"mode": "gaussian", "kernelSize": 3}),
            operator("zero", "vision.value.number", {"value": 0}),
            operator("no_reset", "vision.compare.number", {"operator": "eq", "rightValue": 1}),
        ], [edge("input", "videoPath", "load", "videoPath"), edge("input", "reset", "load", "reset"),
            edge("load", "image", "blur", "image"), edge("load", "frame", "blur", "frame"),
            edge("load", "hasNext", "output", "hasNext"), edge("load", "videoPath", "output", "videoPath"),
            edge("zero", "value", "no_reset", "left"), edge("no_reset", "result", "output", "reset")]),
    }
    payload = WorkflowStore().toPayload("File list and video While")
    used = {node["operatorId"] for value in workflows.values() for node in value["nodes"] if node["kind"] == "operator"}
    payload.update(workflows=workflows, workflowOrder=list(workflows),
                   dependencies={"operators": [{"operatorId": key, "version": registry[key].manifest.version}
                                               for key in sorted(used)]})
    return ProjectDocument.model_validate(payload)
