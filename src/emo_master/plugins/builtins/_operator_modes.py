"""Shared configuration rules for the built-ins with mutually exclusive modes."""
from copy import deepcopy
from importlib import import_module

from emo_master.core.project.snapshots import parameterValidationApplies
from emo_master.plugins.builtins.collection_filter.configuration import COLLECTION_TYPES


INPUT_CHOICES = {
    "vision.collection.sort": ("collectionType", COLLECTION_TYPES),
    "vision.collection.select": ("collectionType", COLLECTION_TYPES),
    "vision.collection.count": ("collectionType", COLLECTION_TYPES),
    "vision.io.result_writer": ("inputType", {
        **COLLECTION_TYPES, "histogram": "直方图", "statistics": "颜色统计",
        "geometry": "几何对象", "numberValue": "数值", "booleanValue": "布尔值",
        "stringValue": "文本",
    }),
    "vision.analysis.shape_measurement": ("inputType", {"contours": "轮廓", "blobs": "连通域"}),
    "vision.geometry.extract_points": ("inputType", {
        "circles": "圆", "measurements": "形状测量", "bbox": "包围框",
    }),
}
FEATURE_INPUTS = {
    "circleCenter": ["circles"], "bboxCenter": ["bbox", "measurements"],
    "bboxTopLeft": ["bbox", "measurements"], "minAreaRectCenter": ["measurements"],
    "centroid": ["measurements"],
}
SORT_DEFAULTS = {
    "blob": "area", "detection": "confidence", "contour": "area", "measurement": "area",
    "line": "length", "circle": "radius", "match": "quality",
}
PARAMETER_CONDITIONS: dict[str, dict[str, dict[str, list[object]]]] = {
    "vision.collection.select": {
        "index": {"mode": ["index"]}, "onOutOfRange": {"mode": ["index"]},
        "count": {"mode": ["topK"]},
    },
    "vision.preprocess.blur": {
        "kernelSize": {"mode": ["gaussian", "median", "mean"]},
        "sigmaX": {"mode": ["gaussian"]}, "sigmaY": {"mode": ["gaussian"]},
        "diameter": {"mode": ["bilateral"]}, "sigmaColor": {"mode": ["bilateral"]},
        "sigmaSpace": {"mode": ["bilateral"]},
    },
    "vision.geometry.coordinate_calculator": {
        "pairing": {"mode": ["add", "subtract", "dot"]},
        "scalarValue": {"mode": ["scalarMultiply"]},
        "closed": {"mode": ["measure", "transform", "scalarMultiply"]},
        **{name: {"mode": ["transform"]} for name in
           ("offsetX", "offsetY", "scaleX", "scaleY", "rotationDegrees", "pivot")},
        "pivotX": {"mode": ["transform"], "pivot": ["custom"]},
        "pivotY": {"mode": ["transform"], "pivot": ["custom"]},
    },
    "vision.preprocess.roi": {
        "x": {"roiType": ["bbox"]}, "y": {"roiType": ["bbox"]},
        "width": {"roiType": ["bbox", "rotatedBox"]},
        "height": {"roiType": ["bbox", "rotatedBox"]},
        **{name: {"roiType": ["rotatedBox"]} for name in ("centerX", "centerY", "angleDegrees")},
        "points": {"roiType": ["polygon"]},
    },
    "vision.preprocess.resize": {"padValue": {"mode": ["letterbox"]}},
    "vision.inference.yolo": {"cropTargetIndex": {"outputCrop": [True]}},
}
MODE_ENTRIES = {
    "vision.collection.sort": "collection_sort.operator:CollectionSortOperator",
    "vision.collection.select": "collection_select.operator:CollectionSelectOperator",
    "vision.collection.count": "collection_count.operator:CollectionCountOperator",
    "vision.io.result_writer": "result_writer.operator:ResultWriterOperator",
    "vision.analysis.shape_measurement": "_shape_operators:ShapeMeasurementOperator",
    "vision.geometry.extract_points": "_geometry_bridges:GeometryPointsOperator",
    "vision.preprocess.blur": "blur.operator:BlurOperator",
    "vision.preprocess.roi": "roi.operator:RoiOperator",
    "vision.preprocess.resize": "resize.operator:ResizeOperator",
    "vision.geometry.coordinate_calculator": "_coordinate_operators:CoordinateCalculatorOperator",
    "vision.inference.yolo": "yolo_inference.operator:YoloInferenceOperator",
}


def modeSchema(operatorId, schema):
    schema = deepcopy(schema)
    schema["xModeOperator"] = operatorId
    properties = schema["properties"]
    if operatorId in INPUT_CHOICES:
        selector, choices = INPUT_CHOICES[operatorId]
        properties = {selector: {
            "title": "处理对象", "type": "string", "enum": ["", *choices], "default": "",
            "xOptionLabels": {"": "请选择处理对象", **choices},
        }, **properties}
        schema["properties"] = properties
        if operatorId == "vision.geometry.extract_points":
            properties[selector]["xEnumBy"] = {"parameter": "feature", "options": FEATURE_INPUTS}
    for name, condition in PARAMETER_CONDITIONS.get(operatorId, {}).items():
        properties[name]["xVisibleWhen"] = deepcopy(condition)
        properties[name]["xValidateWhen"] = deepcopy(condition)
    return schema


def currentModeSchema(operatorId):
    entry = MODE_ENTRIES.get(operatorId)
    if entry is None:
        return None
    module, name = entry.split(":")
    return deepcopy(getattr(import_module("emo_master.plugins.builtins." + module), name).meta.paramSchema)


def effectiveModeParams(params, schema):
    """Neutralize inactive fields only in the validation copy, keeping saved drafts."""
    properties = schema["properties"]
    values = {name: field["default"] for name, field in properties.items() if "default" in field}
    values.update(params)
    result = dict(params)
    for name, field in properties.items():
        if not parameterValidationApplies(values, field):
            result[name] = deepcopy(field.get("default"))
    return result


def inputChoiceError(operatorId, params, inputs=None):
    selector, choices = INPUT_CHOICES[operatorId]
    selected = params.get(selector, "")
    if not isinstance(selected, str) or selected != "" and selected not in choices:
        return {"code": "E_PARAM_INVALID", "message": "请选择有效的处理对象"}
    if operatorId == "vision.geometry.extract_points" and selected:
        if selected not in FEATURE_INPUTS.get(params.get("feature", "circleCenter"), []):
            return {"code": "E_PARAM_INVALID", "message": "处理对象不适用于当前取点特征"}
    if inputs is not None and selected:
        present = {name for name in choices if name in inputs}
        if present != {selected}:
            return {"code": "E_INPUT_SHAPE", "message": "输入必须与所选处理对象一致，且只能连接一种对象"}
    return None


def inferredInputType(operatorId, params, nodeId, edges, dynamicParameters=()):
    selector, choices = INPUT_CHOICES[operatorId]
    if params.get(selector):
        return params[selector]
    connected = {edge.toPort for edge in edges if edge.toNode == nodeId and edge.toPort in choices}
    if connected:
        return next(iter(connected)) if len(connected) == 1 else ""
    prefix = {"vision.collection.sort": "sorted", "vision.collection.select": "selected"}.get(operatorId)
    if prefix:
        outputs = {edge.fromPort for edge in edges if edge.fromNode == nodeId}
        candidates = {kind for kind in choices if prefix + kind.capitalize() in outputs}
        if len(candidates) == 1:
            return next(iter(candidates))
    if operatorId == "vision.geometry.extract_points" and "feature" not in dynamicParameters:
        options = FEATURE_INPUTS.get(params.get("feature", "circleCenter"), [])
        if len(options) == 1:
            return options[0]
    return ""
