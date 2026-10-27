from copy import deepcopy

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.project.models import ProjectDocument


def cameraProject(registry, audioUrl="http://camera.invalid"):
    def operator(nodeId, operatorId, params):
        manifest = registry[operatorId].manifest
        return dict(nodeId=nodeId, kind="operator", operatorId=operatorId, params=params,
                    displayName=manifest.displayName, paramSchema=deepcopy(manifest.paramSchema),
                    inputPorts={key: normalizePortType(value) for key, value in manifest.inputPorts.items()},
                    outputPorts={key: normalizePortType(value) for key, value in manifest.outputPorts.items()})

    payload = WorkflowStore().toPayload("Network camera fixture")
    workflow = payload["workflows"]["main"]
    workflow["inputs"] = {"trigger": "boolean"}
    workflow["outputs"] = {"image": "image", "frame": "bbox2d", "triggered": "boolean"}
    nodes = [
        dict(nodeId="input", kind="workflow_input", outputPorts=workflow["inputs"]),
        operator("camera", "vision.io.rtsp_camera", {"rtspUrl": "rtsp://camera.invalid/live", "username": ""}),
        operator("blur", "vision.preprocess.blur", {"kernelSize": 3}),
        operator("audio", "communication.camera.hikvision_audio", {"cameraUrl": audioUrl, "password": "fixture-only"}),
        dict(nodeId="output", kind="workflow_output", inputPorts=workflow["outputs"]),
    ]
    workflow.update(nodes=nodes, edges=[
        dict(fromNode=source, fromPort=port, toNode=target, toPort=targetPort)
        for source, port, target, targetPort in [
            ("input", "trigger", "audio", "trigger"), ("camera", "image", "blur", "image"),
            ("camera", "frame", "blur", "frame"), ("blur", "image", "output", "image"),
            ("blur", "frame", "output", "frame"), ("audio", "triggered", "output", "triggered"),
        ]
    ], layout={"nodePositions": {node["nodeId"]: {"x": index * 280, "y": 100}
                                  for index, node in enumerate(nodes)}})
    payload["dependencies"]["operators"] = [
        {"operatorId": node["operatorId"], "version": registry[node["operatorId"]].manifest.version}
        for node in nodes if node["kind"] == "operator"
    ]
    return ProjectDocument.model_validate(payload)
