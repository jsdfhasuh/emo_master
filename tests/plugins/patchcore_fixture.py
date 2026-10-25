"""Synthetic deployment mechanics only; never a qualified business PatchCore model."""
from copy import deepcopy
import hashlib
import json

import numpy as np


PREPROCESS = {
    "input_size": [256, 256], "color_space": "RGB", "input_range": [0.0, 1.0],
    "resize_mode": "letterbox", "interpolation": "bilinear", "align_corners": False,
    "antialias": False, "rounding": "python_round", "padding": "center_remainder_bottom_right",
    "fill_before_normalize": 0.0, "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225],
    "patch_edge_policy": "floor_valid_no_extra_pad", "patch_coverage": [252, 252],
}
CALIBRATION_POLICY = {
    "strategy": "onnx_rgb_normal_quantile_p6_v2", "graph_source": "final_frozen_export",
    "distance_profile": "dino_fp32_features_fp64_distance_p5_v1", "backend": "onnxruntime", "ort": "1.23.2",
    "provider": "CPUExecutionProvider", "optimization": "BASIC", "threads": 2, "inter_threads": 1,
    "batch": 1, "preprocess": "dino_rgb_letterbox256_fp32_v1", "samples": "ordered_frozen_val_good",
    "rule": "platform_normal_quantile",
}


def writeJson(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, allow_nan=False), encoding="utf-8")


def contentHash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("ascii")).hexdigest()


def entry(path):
    return {"path": path.name, "size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def inventory(root):
    import onnx
    model = onnx.load_model(str(root / "model.onnx"), load_external_data=False)
    tensors = []

    def collect(graph):
        tensors.extend(graph.initializer)
        for node in graph.node:
            for attribute in node.attribute:
                if attribute.type == onnx.AttributeProto.GRAPH:
                    collect(attribute.g)
                elif attribute.type == onnx.AttributeProto.TENSOR:
                    tensors.append(attribute.t)
    collect(model.graph)
    refs = []
    names = {"model.onnx"}
    for tensor in tensors:
        if tensor.data_location == onnx.TensorProto.EXTERNAL:
            fields = {item.key: item.value for item in tensor.external_data}
            names.add(fields["location"])
            refs.append({"tensor": tensor.name, "path": fields["location"],
                         "offset": int(fields["offset"]), "length": int(fields["length"])})

    def port(value):
        return {"name": value.name, "shape": [d.dim_value for d in value.type.tensor_type.shape.dim],
                "dtype": value.type.tensor_type.elem_type}
    return {"files": [entry(root / name) for name in sorted(names)], "external_references": refs,
            "ir_version": model.ir_version, "opsets": {op.domain: op.version for op in model.opset_import},
            "nodes": len(model.graph.node), "inputs": [port(p) for p in model.graph.input],
            "outputs": [port(p) for p in model.graph.output]}


def makeDeployment(root, *, version=2, profile="dino_fp32_features_fp64_distance_p5_v1",
                   score=2.5, threshold=2.5, rawMap=None, external=True, nested=False,
                   nestedInitializer=False, scoreShape=(1,), scoreDtype=np.float32):
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    root.mkdir(parents=True)
    rawMap = (np.arange(256 * 256, dtype=np.float32).reshape(1, 1, 256, 256) / 20000
              if rawMap is None else rawMap)
    inputs = [helper.make_tensor_value_info("image", TensorProto.FLOAT, [1, 3, 256, 256])]
    outputs = [helper.make_tensor_value_info("pred_score", helper.np_dtype_to_tensor_dtype(np.dtype(scoreDtype)), list(scoreShape)),
               helper.make_tensor_value_info("anomaly_map", helper.np_dtype_to_tensor_dtype(rawMap.dtype), list(rawMap.shape))]
    initializers = [numpy_helper.from_array(np.full(scoreShape, score, scoreDtype), "score_value")]
    nodes = [helper.make_node("Identity", ["score_value"], ["pred_score"])]
    if nested:
        if nestedInitializer:
            initializers.append(numpy_helper.from_array(np.asarray(True), "condition"))
        else:
            initializers.append(numpy_helper.from_array(np.asarray(0, np.float32), "zero"))
            nodes.extend([helper.make_node("ReduceMean", ["image"], ["mean"], keepdims=0),
                          helper.make_node("Greater", ["mean", "zero"], ["condition"])])

        def branch(name):
            value = numpy_helper.from_array(rawMap, name + "_value")
            if not nestedInitializer:
                initializers.append(value)
            return helper.make_graph([helper.make_node("Identity", [value.name], [name])], name, [],
                                     [helper.make_tensor_value_info(name, TensorProto.FLOAT, [1, 1, 256, 256])],
                                     [value] if nestedInitializer else [])
        nodes.append(helper.make_node("If", ["condition"], ["anomaly_map"],
                                      then_branch=branch("yes"), else_branch=branch("no")))
    else:
        initializers.append(numpy_helper.from_array(rawMap, "map_value"))
        nodes.append(helper.make_node("Identity", ["map_value"], ["anomaly_map"]))
    graph = helper.make_graph(nodes, "synthetic_patchcore_contract", inputs, outputs, initializers)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)], ir_version=8)
    onnx.save_model(model, str(root / "model.onnx"), save_as_external_data=external,
                    all_tensors_to_one_file=True, location="weights.bin", size_threshold=0)
    graphInfo = inventory(root)
    spec = {"backbone": "dinov2_small", "recipe_id": "dinov2_small_letterbox_l2_nnmax_v1",
            "preprocess": deepcopy(PREPROCESS), "score": {"kind": "patchcore_dinov2_l2_nnmax_raw_v1",
                                                         "decision_rule": "score_gte_threshold"}}
    identity = {"run_id": "synthetic", "dataset_id": "synthetic", "identity_sha256": "a" * 64,
                "run_identity_sha256": "b" * 64}
    samples = [{"sample_id": "train", "role": "train.good", "sha256": "e" * 64},
               {"sample_id": "validation", "role": "val.good", "sha256": "f" * 64}]
    task = {"format": f"dino_cloud_task_p6_v{version}", "spec": spec, "identity": identity,
            "distance_profile": profile, "samples": samples, "calibration": {"mode": "normal_quantile", "quantile": .95}}
    if version == 2:
        task["deployment_calibration"] = deepcopy(CALIBRATION_POLICY)
    source = {"spec": spec, "run_identity": identity, "model_sha256": "c" * 64,
              "threshold_sha256": "d" * 64, "memory_bank_shape": [1, 768],
              "threshold_document": {"threshold": threshold, "calibration_data_sha256": contentHash(samples[1:])}}
    target = {"ort": "1.23.2", "provider": "CPUExecutionProvider", "optimization": "BASIC",
              "threads": 2, "inter_threads": 1}
    thresholdRecord = deepcopy(source["threshold_document"])
    if version == 2 or profile != "dino_fp32_p5_v1":
        thresholdRecord.update(format="dino_onnx_rgb_calibration_p6_v2" if version == 2 else "dino_p5_candidate_calibration_v1",
            profile=profile, graph_files=graphInfo["files"], source_model_sha256=source["model_sha256"],
            source_threshold_sha256=source["threshold_sha256"], decision_rule="score_gte_threshold",
            calibration_owner="cloud_deployment" if version == 2 else "training")
    if version == 2:
        rows = [{"sample": samples[1], "score": threshold, "tensor_sha256": "1" * 64}]
        thresholdRecord.update(task_sha256=contentHash(task), preprocess=deepcopy(PREPROCESS),
                               environment={"scope": "linux_cloud"}, policy=deepcopy(CALIBRATION_POLICY),
                               samples=samples[1:], method=task["calibration"], score_rows=rows, score_rows_sha256=contentHash(rows))
    elif profile != "dino_fp32_p5_v1":
        thresholdRecord.update(backend="native_torch_fp32_features_fp64_distance", sample_sha256=[samples[1]["sha256"]],
                               scores=[threshold], method={"method": "normal_quantile", "quantile": .95})
    writeJson(root / "threshold.json", thresholdRecord)
    manifest = {"format": f"dino_onnx_deployment_p6_v{version}", "profile": profile,
                "algorithm": "patchcore", "backbone": "dinov2_small", "source": source,
                "task_contract": task, "task_sha256": contentHash(task), "target": target,
                "graph": graphInfo, "preprocess": deepcopy(PREPROCESS), "coverage": [252, 252],
                "threshold": threshold, "threshold_file": entry(root / "threshold.json"),
                "qualification": "PASS", "cloud_qualification": "PASS", "support_files": [],
                "fixture_note": "Synthetic mechanics test, not model qualification"}
    report = {"status": "PASS", "graph": graphInfo, "profile": profile, "target": target,
              "threshold_sha256": manifest["threshold_file"]["sha256"], "environment": {"scope": "linux_cloud"},
              "items": [{"role": "val.good", "sample_sha256": samples[1]["sha256"]}]}
    writeJson(root / "verification.json", report)
    manifest["verification_report"] = entry(root / "verification.json")
    if version == 2:
        writeJson(root / "training_candidate_threshold.json", {"threshold": threshold})
        manifest.update(deployment_calibration_state="FROZEN",
                        training_candidate_file=entry(root / "training_candidate_threshold.json"))
    writeJson(root / "deployment.json", manifest)
    return root


def wrapTransfer(root):
    payload = root / "payload"
    payload.mkdir()
    for file in list(root.iterdir()):
        if file != payload:
            file.rename(payload / file.name)
    writeJson(root / "transfer.json", {"format": "patchcore_local_transfer_v1", "kind": "dino",
        "transfer_id": "synthetic", "manifest_sha256": entry(payload / "deployment.json")["sha256"],
        "files": [entry(file) for file in sorted(payload.iterdir())]})
    return root
