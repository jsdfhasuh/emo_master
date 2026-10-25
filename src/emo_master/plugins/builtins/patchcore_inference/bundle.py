"""Read-only consumer of completed Vision Workshop DINO deployment packages.

Producer provenance is checked, not re-issued as emo_master target qualification.
No training registry, calibration, checkpoint loading or environment bootstrap.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re

from .numeric import PREPROCESS, decision


FORMATS = {"dino_onnx_deployment_p6_v1", "dino_onnx_deployment_p6_v2"}
PROFILES = {"dino_fp32_p5_v1", "dino_fp32_features_fp64_distance_p5_v1"}
SCORE_KIND = "patchcore_dinov2_l2_nnmax_raw_v1"
CALIBRATION_POLICY = {
    "strategy": "onnx_rgb_normal_quantile_p6_v2", "graph_source": "final_frozen_export",
    "distance_profile": "dino_fp32_features_fp64_distance_p5_v1", "backend": "onnxruntime", "ort": "1.23.2",
    "provider": "CPUExecutionProvider", "optimization": "BASIC", "threads": 2, "inter_threads": 1,
    "batch": 1, "preprocess": "dino_rgb_letterbox256_fp32_v1", "samples": "ordered_frozen_val_good",
    "rule": "platform_normal_quantile",
}
INPUTS = [{"name": "image", "shape": [1, 3, 256, 256], "dtype": 1}]
OUTPUTS = [{"name": "pred_score", "shape": [1], "dtype": 1},
           {"name": "anomaly_map", "shape": [1, 1, 256, 256], "dtype": 1}]


class BackendUnavailableError(RuntimeError):
    pass


class ModelError(ValueError):
    pass


@dataclass(frozen=True)
class Deployment:
    root: Path
    manifest: dict
    manifestSha256: str
    threshold: float


def contentHash(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def fileHash(path, checkCancelled):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checkCancelled()
            digest.update(block)
    return digest.hexdigest()


def relativeFile(root, name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or PurePosixPath(name).is_absolute() or any(p in {"", ".", ".."} for p in name.split("/"))):
        raise ModelError("invalid package-relative file path")
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ModelError(f"missing or out-of-package file: {name}")
    return path


def readJson(path):
    def invalid(value):
        raise ModelError("nonfinite model record: " + value)

    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ModelError("duplicate model record key: " + key)
            result[key] = value
        return result

    result = json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid, object_pairs_hook=pairs)
    if not isinstance(result, dict):
        raise ModelError("model record must be an object")
    return result


def checkEntries(root, rows, checkCancelled, verified):
    if not isinstance(rows, list):
        raise ModelError("file inventory must be an array")
    result = {}
    seen = set()
    for row in rows:
        name = row["path"]
        path = relativeFile(root, name)
        if (name.casefold() in seen or type(row["size"]) is not int or row["size"] < 0
                or not isinstance(row["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])):
            raise ModelError("invalid file inventory entry")
        seen.add(name.casefold())
        if name not in verified:
            verified[name] = {"path": name, "size": path.stat().st_size,
                              "sha256": fileHash(path, checkCancelled)}
        if verified[name] != row:
            raise ModelError("package file size/digest mismatch: " + name)
        result[name] = row
    return result


def graphInventory(root, files, *, allowControlFlow):
    try:
        import onnx
        import numpy as np
    except ImportError as error:
        raise BackendUnavailableError("PatchCore requires onnx==1.16.0 in the Runtime environment") from error
    if "model.onnx" not in files or sum(row["size"] for row in files.values()) > 2 * 1024**3:
        raise ModelError("model.onnx is missing or graph exceeds 2 GiB")
    graphPath = relativeFile(root, "model.onnx")
    model = onnx.load_model(str(graphPath), load_external_data=False)
    if (model.functions or model.training_info or model.graph.sparse_initializer
            or any(op.domain not in {"", "ai.onnx"} for op in model.opset_import)):
        raise ModelError("unsupported ONNX graph structure/domain")
    tensors = []

    def collect(graph, depth=0):
        if depth > 8 or graph.sparse_initializer:
            raise ModelError("unsupported ONNX subgraph")
        tensors.extend(graph.initializer)
        for node in graph.node:
            if node.domain not in {"", "ai.onnx"}:
                raise ModelError("custom ONNX operators are unsupported")
            for attr in node.attribute:
                if attr.type == onnx.AttributeProto.GRAPH:
                    if not allowControlFlow or node.op_type not in {"Loop", "If"}:
                        raise ModelError("unsupported ONNX control flow")
                    collect(attr.g, depth + 1)
                elif attr.type in {onnx.AttributeProto.GRAPHS, onnx.AttributeProto.SPARSE_TENSOR,
                                   onnx.AttributeProto.SPARSE_TENSORS}:
                    raise ModelError("unsupported ONNX tensor attribute")
                elif attr.type == onnx.AttributeProto.TENSOR:
                    tensors.append(attr.t)
                elif attr.type == onnx.AttributeProto.TENSORS:
                    tensors.extend(attr.tensors)

    collect(model.graph)
    references, used = [], {"model.onnx"}
    for tensor in tensors:
        if tensor.data_location != onnx.TensorProto.EXTERNAL:
            if tensor.external_data:
                raise ModelError("invalid external tensor reference")
            continue
        fields = {item.key: item.value for item in tensor.external_data}
        if len(fields) != len(tensor.external_data) or set(fields) - {"location", "offset", "length", "checksum"}:
            raise ModelError("invalid external tensor fields")
        name = fields.get("location")
        relativeFile(root, name)
        if name not in files:
            raise ModelError("undeclared external tensor file: " + str(name))
        offset = int(fields.get("offset", "0"))
        length = int(fields.get("length", str(files[name]["size"] - offset)))
        dtype = np.dtype(onnx.helper.tensor_dtype_to_np_dtype(tensor.data_type))
        if (dtype.hasobject or offset < 0 or length <= 0 or tensor.raw_data
                or length != math.prod(tensor.dims) * dtype.itemsize or offset + length > files[name]["size"]):
            raise ModelError("invalid external tensor range")
        used.add(name)
        references.append({"tensor": tensor.name, "path": name, "offset": offset, "length": length})
    if used != set(files):
        raise ModelError("graph file inventory does not match external references")
    # All external paths/ranges have been checked before the checker or ORT reads them.
    onnx.checker.check_model(str(graphPath))

    def port(value):
        shape = [dim.dim_value if dim.HasField("dim_value") else None
                 for dim in value.type.tensor_type.shape.dim]
        return {"name": value.name, "shape": shape, "dtype": value.type.tensor_type.elem_type}

    return {"files": sorted(files.values(), key=lambda row: row["path"]),
            "external_references": references, "ir_version": model.ir_version,
            "opsets": {op.domain: op.version for op in model.opset_import}, "nodes": len(model.graph.node),
            "inputs": [port(x) for x in model.graph.input], "outputs": [port(x) for x in model.graph.output]}


def loadDeployment(directory, checkCancelled=lambda: None):
    try:
        return _loadDeployment(Path(directory).resolve(), checkCancelled)
    except (BackendUnavailableError, ModelError):
        raise
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        raise ModelError(f"invalid PatchCore deployment: {error}") from error


def _loadDeployment(root, checkCancelled):
    checkCancelled()
    verified = {}
    transfer = None
    if (root / "transfer.json").is_file():
        transfer = readJson(root / "transfer.json")
        if transfer.get("format") != "patchcore_local_transfer_v1" or transfer.get("kind") != "dino":
            raise ModelError("expected a Vision Workshop DINO transfer package")
        payload = (root / "payload").resolve()
        if not payload.is_relative_to(root):
            raise ModelError("transfer payload escapes package")
        root = payload
        checkEntries(root, transfer["files"], checkCancelled, verified)
        if "deployment.json" not in verified:
            raise ModelError("transfer does not bind deployment.json")
    manifestPath = relativeFile(root, "deployment.json")
    if manifestPath.stat().st_size > 2 * 1024**2:
        raise ModelError("deployment manifest exceeds 2 MiB")
    manifestHash = fileHash(manifestPath, checkCancelled)
    if transfer is not None and manifestHash != transfer["manifest_sha256"]:
        raise ModelError("transfer deployment manifest digest mismatch")
    manifest = readJson(manifestPath)
    if manifest.get("format") not in FORMATS or manifest.get("profile") not in PROFILES:
        raise ModelError("only completed DINO ONNX p6_v1/p6_v2 deployments are supported")
    if manifest.get("algorithm") != "patchcore" or manifest.get("backbone") != "dinov2_small":
        raise ModelError("expected PatchCore dinov2_small")
    if manifest.get("qualification") != "PASS" or manifest.get("cloud_qualification") != "PASS":
        raise ModelError("producer deployment verification has not passed")
    v2 = manifest["format"].endswith("_v2")
    if v2 and manifest.get("deployment_calibration_state") != "FROZEN":
        raise ModelError("deployment calibration must be FROZEN")
    source, task = manifest["source"], manifest["task_contract"]
    spec = source["spec"]
    identity = task["identity"]
    if (set(identity) != {"dataset_id", "run_id", "identity_sha256", "run_identity_sha256"}
            or any(not isinstance(value, str) or not value.strip() for value in identity.values())
            or any(not re.fullmatch(r"[0-9a-f]{64}", identity[key])
                   for key in ("identity_sha256", "run_identity_sha256"))):
        raise ModelError("incomplete frozen model identity")
    if (task["format"] != ("dino_cloud_task_p6_v2" if v2 else "dino_cloud_task_p6_v1")
            or contentHash(task) != manifest["task_sha256"] or task["spec"] != spec
            or task["distance_profile"] != manifest["profile"]
            or any(source["run_identity"].get(k) != value for k, value in identity.items())):
        raise ModelError("frozen model/task identity mismatch")
    if (manifest["preprocess"] != PREPROCESS or spec["preprocess"] != PREPROCESS
            or manifest["coverage"] != [252, 252] or spec["backbone"] != "dinov2_small"
            or spec["recipe_id"] != "dinov2_small_letterbox_l2_nnmax_v1"
            or spec["score"]["kind"] != SCORE_KIND or spec["score"]["decision_rule"] != "score_gte_threshold"):
        raise ModelError("unsupported DINO preprocessing or score contract")
    for name in ("model_sha256", "threshold_sha256"):
        if not isinstance(source[name], str) or not re.fullmatch(r"[0-9a-f]{64}", source[name]):
            raise ModelError("invalid source identity: " + name)
    target = manifest["target"]
    if target != {"ort": "1.23.2", "provider": "CPUExecutionProvider", "threads": 2,
                  "inter_threads": 1, "optimization": "BASIC"}:
        raise ModelError("unsupported producer target profile")
    files = checkEntries(root, manifest["graph"]["files"], checkCancelled, verified)
    checkCancelled()
    try:
        graph = graphInventory(root, files, allowControlFlow=manifest["profile"].endswith("distance_p5_v1"))
    except (BackendUnavailableError, ModelError):
        raise
    except Exception as error:
        raise ModelError(f"invalid ONNX graph: {error}") from error
    if graph != manifest["graph"] or graph["inputs"] != INPUTS or graph["outputs"] != OUTPUTS:
        raise ModelError("DINO graph inventory or fixed tensor schema mismatch")
    rows = [manifest["threshold_file"], manifest["verification_report"], *manifest["support_files"]]
    if v2:
        rows.append(manifest["training_candidate_file"])
    checkEntries(root, rows, checkCancelled, verified)
    threshold = readJson(relativeFile(root, manifest["threshold_file"]["path"]))
    decision(0.0, threshold["threshold"])
    decision(0.0, manifest["threshold"])
    if threshold["threshold"] != manifest["threshold"]:
        raise ModelError("deployment threshold binding mismatch")
    samples = [row for row in task["samples"] if row["role"] == "val.good"]
    method = task["calibration"]
    if (not samples or method.get("mode") != "normal_quantile"
            or type(method.get("quantile")) not in (float, int) or not 0 < method["quantile"] < 1):
        raise ModelError("invalid frozen calibration samples/method")
    if v2:
        expected = {"format": "dino_onnx_rgb_calibration_p6_v2", "task_sha256": manifest["task_sha256"],
                    "graph_files": graph["files"], "profile": manifest["profile"], "preprocess": PREPROCESS,
                    "calibration_owner": "cloud_deployment", "decision_rule": "score_gte_threshold",
                    "source_model_sha256": source["model_sha256"], "source_threshold_sha256": source["threshold_sha256"],
                    "policy": CALIBRATION_POLICY, "samples": samples, "method": method}
        if (any(threshold.get(k) != value for k, value in expected.items())
                or task["deployment_calibration"] != CALIBRATION_POLICY
                or threshold["environment"]["scope"] != "linux_cloud"):
            raise ModelError("frozen deployment calibration binding mismatch")
        scoreRows = threshold["score_rows"]
        if ([row["sample"] for row in scoreRows] != samples or contentHash(scoreRows) != threshold["score_rows_sha256"]
                or any(type(row["score"]) not in (float, int) or not math.isfinite(row["score"])
                       or not re.fullmatch(r"[0-9a-f]{64}", row["tensor_sha256"]) for row in scoreRows)):
            raise ModelError("frozen calibration score/sample binding mismatch")
    elif manifest["profile"] == "dino_fp32_p5_v1":
        if threshold != source["threshold_document"]:
            raise ModelError("original model threshold changed")
    else:
        expected = {"format": "dino_p5_candidate_calibration_v1", "profile": manifest["profile"],
                    "calibration_owner": "training", "decision_rule": "score_gte_threshold",
                    "graph_files": graph["files"], "source_model_sha256": source["model_sha256"],
                    "source_threshold_sha256": source["threshold_sha256"],
                    "backend": "native_torch_fp32_features_fp64_distance",
                    "calibration_data_sha256": contentHash(samples), "sample_sha256": [row["sha256"] for row in samples]}
        if any(threshold.get(k) != value for k, value in expected.items()):
            raise ModelError("training candidate threshold binding mismatch")
        if (threshold["calibration_data_sha256"] != source["threshold_document"]["calibration_data_sha256"]
                or threshold["method"]["quantile"] != method["quantile"]
                or len(threshold["scores"]) != len(samples)
                or any(type(score) not in (float, int) or not math.isfinite(score) for score in threshold["scores"])):
            raise ModelError("training candidate sample/method binding mismatch")
    report = readJson(relativeFile(root, manifest["verification_report"]["path"]))
    expected = {"status": "PASS", "graph": graph, "profile": manifest["profile"],
                "target": target, "threshold_sha256": manifest["threshold_file"]["sha256"]}
    if (any(report.get(k) != value for k, value in expected.items()) or report["environment"]["scope"] != "linux_cloud"
            or [(row["role"], row["sample_sha256"]) for row in report["items"] if row["role"].startswith("val.")]
            != [(row["role"], row["sha256"]) for row in samples]):
        raise ModelError("producer verification report binding mismatch")
    if transfer is not None and not set(verified) <= {row["path"] for row in transfer["files"]}:
        raise ModelError("transfer inventory does not bind every deployment file")
    checkCancelled()
    return Deployment(root, manifest, manifestHash, float(threshold["threshold"]))
