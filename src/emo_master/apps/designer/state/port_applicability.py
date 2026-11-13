"""Pure mode-dependent port applicability, shared by all editing frontends."""
from emo_master.plugins.builtins._operator_modes import FEATURE_INPUTS, INPUT_CHOICES, inferredInputType
from emo_master.plugins.builtins.collection_filter.configuration import COLLECTION_TYPES

CONDITIONAL_OUTPUTS = {
    "vision.inference.yolo": {
        "drawOverlay": (True, {"overlay"}),
        "outputCrop": (False, {"croppedImage", "croppedFrame"}),
    },
    "vision.color.rgb_statistics": {"emitMask": (False, {"mask"})},
    "vision.io.coordinate_reader": {"asPolygon": (False, {"polygon"})},
    "vision.analysis.blob": {"drawOverlay": (False, {"overlay"})},
}
MODE_PORT_OPERATORS = set(INPUT_CHOICES) | set(CONDITIONAL_OUTPUTS) | {
    "vision.mask.logic", "vision.geometry.coordinate_calculator", "vision.collection.filter",
}


_MEASUREMENT_OUTPUTS = {
    "points", "centroid", "bounds", "segmentLengths", "pathLength", "perimeter",
    "signedArea", "area", "firstToLastDistance", "firstToLastAngleDegrees", "resultCount",
}
_MODES = {"measure", "transform", "scalarMultiply", "add", "subtract", "dot"}


def activeCoordinatePorts(params, dynamicParameters=()):
    mode = params.get("mode", "transform")
    modes = _MODES if "mode" in dynamicParameters or mode not in _MODES else {mode}
    inputs, outputs = {"points"}, set()
    for selected in modes:
        if selected in {"add", "subtract", "dot"}:
            inputs.add("otherPoints")
            outputs.update({
                "add": {"points", "resultCount"},
                "subtract": {"vectors", "magnitudes", "resultCount"},
                "dot": {"values", "dotSum", "resultCount"},
            }[selected])
        else:
            if selected == "scalarMultiply":
                inputs.add("scalar")
            outputs.update(_MEASUREMENT_OUTPUTS)
            if params.get("closed", False) or "closed" in dynamicParameters:
                outputs.add("polygon")
    return inputs, outputs


def filterCollectionType(node, edges):
    selected = node.params.get("collectionType")
    if selected:
        return selected
    connected = {edge.toPort for edge in edges
                 if edge.toNode == node.nodeId and edge.toPort in COLLECTION_TYPES}
    if len(connected) == 1:
        return next(iter(connected))
    # A legacy node can have its input disconnected while its outputs remain wired.
    outputs = {edge.fromPort for edge in edges if edge.fromNode == node.nodeId}
    candidates = {kind for kind in COLLECTION_TYPES
                  if outputs.intersection({"kept" + kind.capitalize(), "rejected" + kind.capitalize()})}
    return next(iter(candidates)) if len(candidates) == 1 else ""


def activeFilterPorts(params, collectionType="", dynamicParameters=()):
    dynamic = set(dynamicParameters)
    selected = params.get("collectionType") or collectionType
    types = set(COLLECTION_TYPES) if "collectionType" in dynamic else (
        {selected} if selected in COLLECTION_TYPES else set())
    inputs = set(types)
    outputs = {"keptCount", "rejectedCount"}
    for kind in types:
        outputs.update({"kept" + kind.capitalize(), "rejected" + kind.capitalize()})
    spatial = params.get("spatialEnabled", params.get("spatialMode", "none") != "none"
                         or "spatialMode" in dynamic)
    if types and (spatial or "spatialEnabled" in dynamic) and (
            params.get("spatialMode", "none") != "none" or "spatialMode" in dynamic):
        inputs.add("roi")
    return inputs, outputs


def activeOperatorPorts(node, edges, dynamicParameters=()):
    dynamic = set(dynamicParameters)
    dynamic.update(binding["parameterPath"][0] for binding in node.globalVariableBindings if binding.get("parameterPath"))
    if node.operatorId == "vision.geometry.coordinate_calculator":
        return activeCoordinatePorts(node.params, dynamic)
    if node.operatorId == "vision.collection.filter":
        return activeFilterPorts(node.params, filterCollectionType(node, edges), dynamic)
    inputs, outputs = set(node.inputPorts), set(node.outputPorts)
    if node.operatorId in INPUT_CHOICES:
        selector, choices = INPUT_CHOICES[node.operatorId]
        selected = inferredInputType(node.operatorId, node.params, node.nodeId, edges, dynamic)
        inputs = set(choices) if selector in dynamic else ({selected} if selected in choices else set())
        if node.operatorId == "vision.geometry.extract_points":
            if "feature" in dynamic and not node.params.get(selector):
                inputs = set(choices)
            elif "feature" not in dynamic:
                inputs.intersection_update(FEATURE_INPUTS.get(node.params.get("feature", "circleCenter"), []))
        prefix = {"vision.collection.sort": "sorted", "vision.collection.select": "selected"}.get(node.operatorId)
        if prefix:
            outputs = {prefix + name.capitalize() for name in inputs}
            if node.operatorId == "vision.collection.select":
                outputs.add("selectedCount")
                if node.params.get("mode", "first") == "index" or "mode" in dynamic:
                    inputs.add("index")
    for flag, (default, ports) in CONDITIONAL_OUTPUTS.get(node.operatorId, {}).items():
        if flag not in dynamic and not node.params.get(flag, default):
            outputs.difference_update(ports)
    if node.operatorId == "vision.mask.logic" and node.params.get("operation", "and") == "not" and "operation" not in dynamic:
        inputs.difference_update({"maskB", "frameB"})
    return inputs, outputs
