"""Build a private, portable face-detection project from current declarations."""
from copy import deepcopy
import argparse
import getpass
import hashlib
from pathlib import Path

import emo_master
from emo_master import __version__
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.workflow.compiler import WorkflowCompiler


MODEL = "models/yolov8n-face-detect.onnx"
PROJECT = "rtsp-face-detection.emoproj"
RESULTS = {"overlay": "image", "frame": "bbox2d", "detections": "detectionCollection",
           "faceCount": "integer", "hasFace": "boolean"}


def operatorRegistry():
    scan = PluginRegistry(__version__).scan(Path(emo_master.__file__).parent / "plugins/builtins")
    if scan.rejectedOperators:
        raise ValueError("Builtin operator scan rejected declarations")
    return scan.activeOperators


def buildProject(root, *, rtspUrl, username="admin", password="", passwordEnv="", registry=None):
    root = Path(root)
    registry = registry if registry is not None else operatorRegistry()
    payload = WorkflowStore().toPayload("RTSP Face Detection")
    template = deepcopy(payload["workflows"]["main"])

    def operator(nodeId, operatorId, params, title):
        manifest = registry[operatorId].manifest
        return dict(nodeId=nodeId, kind="operator", operatorId=operatorId, displayName=title,
                    params=params, paramSchema=deepcopy(manifest.paramSchema),
                    inputPorts={k: normalizePortType(v) for k, v in manifest.inputPorts.items()},
                    outputPorts={k: normalizePortType(v) for k, v in manifest.outputPorts.items()})

    def workflow(name, inputs, outputs, nodes, connections, positions):
        result = deepcopy(template)
        result.update(name=name, inputs=inputs, outputs=outputs, nodes=[
            dict(nodeId="input", kind="workflow_input", displayName="Workflow Input", outputPorts=inputs),
            *nodes,
            dict(nodeId="output", kind="workflow_output", displayName="Workflow Output", inputPorts=outputs),
        ], edges=[dict(fromNode=a, fromPort=b, toNode=c, toPort=d) for a, b, c, d in connections],
            layout={"nodePositions": {key: {"x": x, "y": y} for key, (x, y) in positions.items()}})
        return result

    detect = workflow("\u4eba\u8138\u68c0\u6d4b\u5904\u7406", {"image": "image", "frame": "bbox2d"}, RESULTS, [
        operator("yolo", "vision.inference.yolo", {
            "modelPath": MODEL, "confidence": 0.25, "iou": 0.45, "imageSize": 640,
            "maxDetections": 100, "device": "cpu", "classes": [0], "agnosticNms": False,
            "drawOverlay": True, "outputCrop": False, "cropTargetIndex": 1,
        }, "YOLO \u4eba\u8138\u68c0\u6d4b"),
        operator("count", "vision.collection.count", {"collectionType": "detections"}, "\u4eba\u8138\u6570\u91cf"),
        operator("presence", "vision.compare.number", {"operator": "gt", "rightValue": 0}, "\u662f\u5426\u6709\u4eba\u8138"),
    ], [
        ("input", "image", "yolo", "image"), ("input", "frame", "yolo", "frame"),
        ("yolo", "detections", "count", "detections"), ("count", "count", "presence", "left"),
        ("yolo", "overlay", "output", "overlay"), ("yolo", "frame", "output", "frame"),
        ("yolo", "detections", "output", "detections"), ("count", "count", "output", "faceCount"),
        ("presence", "result", "output", "hasFace"),
    ], {"input": (0, 80), "yolo": (280, 80), "count": (560, 80), "presence": (840, 80), "output": (1120, 80)})

    def entry(wid, name, source, *, correctOrientation=False):
        outputs = {"image": "image", **RESULTS, "archive": "json"}
        imageNode = "orient" if correctOrientation else "source"
        correction = [operator("orient", "vision.preprocess.flip", {"mode": "both"},
                               "\u753b\u9762\u6821\u6b63 180\u00b0")] if correctOrientation else []
        # Presentation has a bounded image lane; keep full-size detection and saves.
        previews = [operator(nid, "vision.preprocess.resize", {
            "width": 960, "height": 540, "mode": "fit", "interpolation": "area",
        }, title) for nid, title in [
            ("preview_image", "\u6821\u6b63\u753b\u9762\u9884\u89c8"),
            ("preview_overlay", "\u68c0\u6d4b\u7ed3\u679c\u9884\u89c8"),
        ]] if correctOrientation else []
        return workflow(name, {}, outputs, [source,
            *correction,
            *previews,
            dict(nodeId="detect", kind="subflow", targetWorkflowId="detect", displayName="\u4eba\u8138\u68c0\u6d4b\u5904\u7406",
                 inputPorts={"image": "image", "frame": "bbox2d"}, outputPorts=RESULTS),
            operator("archive", "vision.io.detection_archive", {
                "outputPath": f"outputs/{wid}/capture.jpg", "minIntervalMs": 2000,
            }, "\u4eba\u8138\u88c1\u526a\u548c\u6807\u6ce8\u7559\u6863"),
        ], [
            *(("source", port, "orient", port) for port in ("image", "frame") if correctOrientation),
            (imageNode, "image", "detect", "image"), (imageNode, "frame", "detect", "frame"),
            (imageNode, "image", "output", "image"), (imageNode, "image", "archive", "image"),
            (imageNode, "frame", "archive", "frame"), ("detect", "detections", "archive", "detections"),
            ("detect", "overlay", "archive", "overlay"), ("archive", "result", "output", "archive"),
            *(("detect", port, "output", port) for port in RESULTS),
            *([(imageNode, "image", "preview_image", "image"), (imageNode, "frame", "preview_image", "frame"),
               ("detect", "overlay", "preview_overlay", "image"), ("detect", "frame", "preview_overlay", "frame")]
              if correctOrientation else []),
        ], {"input": (0, -180), "source": (0, 80), "detect": (640, 80), "archive": (960, 480),
            "output": (1280, 80),
            **({"orient": (320, 80), "preview_image": (320, 800), "preview_overlay": (960, 800)} if correctOrientation else {})})

    entries = {
        "camera_once": entry("camera", "\u6444\u50cf\u5934\u5355\u5e27\u68c0\u6d4b", operator("source", "vision.io.rtsp_camera", {
            "rtspUrl": rtspUrl, "username": username, "password": password, "passwordEnv": passwordEnv,
            "openTimeoutMs": 7000, "readTimeoutMs": 7000, "colorMode": "color",
        }, "RTSP \u6444\u50cf\u5934"), correctOrientation=True),
    }
    samples = {"sample": ("\u79bb\u7ebf\u4eba\u8138\u6837\u4f8b", "samples/astronaut.png"),
               "empty": ("\u79bb\u7ebf\u65e0\u4eba\u8138\u6837\u4f8b", "samples/empty.png")}
    for wid, (name, path) in samples.items():
        entries[wid] = entry(wid, name, operator("source", "vision.io.image_loader",
                            {"imagePath": path, "colorMode": "color"}, "\u8bfb\u53d6\u6837\u4f8b\u56fe"))
    tick = workflow("\u5faa\u73af\u4f53", {}, {}, [dict(
        nodeId="capture", kind="subflow", targetWorkflowId="camera_once",
        displayName="\u53d6\u56fe\u68c0\u6d4b\u7559\u6863", inputPorts={}, outputPorts=entries["camera_once"]["outputs"],
    )], [], {"input": (0, 0), "capture": (280, 0), "output": (600, 0)})
    main = workflow("\u6444\u50cf\u5934\u6301\u7eed\u68c0\u6d4b", {}, {}, [dict(
        nodeId="camera_loop", kind="loop", displayName="While \u6301\u7eed\u8fd0\u884c", inputPorts={}, outputPorts={},
        loop=dict(contractVersion=2, mode="while", bodyWorkflowId="camera_tick", conditionMode="globalVariable",
                  conditionVariableId="keep_running", unlimited=True, maxIterations=1, timeoutMs=0),
    )], [], {"input": (0, 0), "camera_loop": (280, 0), "output": (600, 0)})
    payload["globalVariables"]["keep_running"] = dict(
        name="Keep running", type="boolean", initialValue=True, kind="constant", lifetime="job")
    payload["workflows"] = {"main": main, "camera_tick": tick, **entries, "detect": detect}
    payload["workflowOrder"] = ["main", "camera_tick", *entries, "detect"]
    payload["runtime"]["maxConcurrentJobs"] = 1
    payload["production"].update(autoStart=False, mode="single", cycleIntervalMs=2000)
    used = {n["operatorId"] for w in payload["workflows"].values() for n in w["nodes"] if n["kind"] == "operator"}
    payload["dependencies"]["operators"] = [
        {"operatorId": key, "version": registry[key].manifest.version} for key in sorted(used)]

    def resource(resourceId, path, purpose, wid, nid, parameter):
        content = (root / path).read_bytes()
        payload["resources"]["items"][resourceId] = dict(
            path=path, sha256=hashlib.sha256(content).hexdigest(), size=len(content), purpose=purpose)
        payload["resources"]["parameterBindings"].append(dict(resourceId=resourceId,
            target=dict(workflowId=wid, nodeId=nid, parameterPath=[parameter])))

    resource("face_model", MODEL, "model", "detect", "yolo", "modelPath")
    for wid, (_, path) in samples.items():
        resource(wid, path, "input_image", wid, "source", "imagePath")

    presentation = payload["presentation"]
    presentation.update(defaultPageId="main", pageOrder=["main", "sample", "empty"])
    for wid in presentation["pageOrder"]:
        scope = "camera_once" if wid == "main" else wid
        definition = main if wid == "main" else entries[wid]
        callPath = ([dict(nodeId="camera_loop", relation="loop_body"), dict(nodeId="capture", relation="subflow")]
                    if wid == "main" else [])
        presentation["resultScopes"][wid] = dict(entryWorkflowId=wid, scopeWorkflowId=scope, callPath=callPath)
        for port, valueType in {"overlay": "image", "image": "image", "faceCount": "integer", "hasFace": "boolean"}.items():
            presentation["dataSources"][f"{wid}_{port}"] = dict(kind="workflow_output", resultScopeId=wid,
                workflowId=scope, port=port, expectedType=valueType, callPath=callPath)
            if wid == "main" and port in {"image", "overlay"}:
                presentation["dataSources"][f"{wid}_{port}"].update(
                    kind="node_output", nodeId=f"preview_{port}", port="image")
        presentation["pages"][wid] = dict(name=definition["name"], layout={"columns": 2}, resultScopeIds=[wid], components=[
            dict(componentId=f"{wid}_count", type="number", layout={"row": 0, "column": 0},
                 props={"title": "\u4eba\u8138\u6570\u91cf", "decimals": 0}, bindings={"value": f"{wid}_faceCount"}),
            dict(componentId=f"{wid}_presence", type="indicator", layout={"row": 0, "column": 1},
                 props={"title": "\u4eba\u8138\u68c0\u6d4b\u72b6\u6001", "indicatorStates": {
                     "true": {"text": "\u68c0\u6d4b\u5230\u4eba\u8138", "color": "green"},
                     "false": {"text": "\u672a\u68c0\u6d4b\u5230\u4eba\u8138", "color": "neutral"}}},
                 bindings={"value": f"{wid}_hasFace"}),
            dict(componentId=f"{wid}_overlay", type="image", layout={"row": 1, "column": 0},
                 props={"title": "\u68c0\u6d4b\u53e0\u52a0\u56fe"}, bindings={"image": f"{wid}_overlay"}),
            dict(componentId=f"{wid}_raw", type="image", layout={"row": 1, "column": 1},
                 props={"title": "\u6821\u6b63\u753b\u9762" if wid == "main" else "\u539f\u59cb\u753b\u9762"},
                 bindings={"image": f"{wid}_image"}),
        ])
    document = ProjectDocument.model_validate(payload)
    WorkflowCompiler(operatorRegistry=registry).compile(prepareRuntimeProject(document, root, registry))
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--url", required=True)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--store-password", action="store_true", help="Prompt and save password in the private project")
    args = parser.parse_args()
    password = getpass.getpass("Camera password (saved in project): ") if args.store_password else ""
    document = buildProject(args.root, rtspUrl=args.url, username=args.username, password=password,
                            passwordEnv="" if args.store_password else "EMO_RTSP_CAMERA_PASSWORD")
    path = args.root / PROJECT
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite {path}")
    saveProject(path, document.model_dump(mode="json"))
    assert loadProjectDocument(path) == document
    print(path)


if __name__ == "__main__":
    main()
