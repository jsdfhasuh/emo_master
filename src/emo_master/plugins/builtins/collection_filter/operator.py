from __future__ import annotations

import math
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

from emo_master.core.contracts.geometry2d import (
    Blob2D,
    CircleItem,
    ContourItem,
    Detection2D,
    LineItem,
    ShapeMeasurement,
    TemplateMatch,
    TemplateMatchCollection,
)
from emo_master.plugins.builtins._collection_ops import (
    CollectionInputError,
    CollectionItem,
    ParsedCollection,
    axisAlignedBBox,
    bboxContained,
    bboxesIntersect,
    ensureSameCoordinateSpace,
    geometryType,
    itemBBox,
    itemCenter,
    itemField,
    parseCollectionInputs,
    parseRoi,
    pointInGeometry,
)
from emo_master.plugins.builtins.collection_filter.configuration import (
    COLLECTION_TYPES,
    FILTER_GROUPS,
    effectiveFilterParams,
)


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


_METHODS = [
    "sqdiff",
    "sqdiffNormed",
    "ccorr",
    "ccorrNormed",
    "ccoeff",
    "ccoeffNormed",
]
_SPATIAL_MODES = {"none", "centerInside", "bboxIntersects", "bboxContained"}
_GEOMETRY_TYPES = {"bbox2d", "rotatedBox2d", "polygon2d"}
_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "minArea": {
            "description": "面积含下限，单位pixel²；Blob用已有area，轮廓用cv2.contourArea绝对值，measurement用已有area。",
            "title": "最小面积", "type": "number", "minimum": 0.0, "default": 0.0
        },
        "maxArea": {
            "description": "面积含上限，0表示无上限；来源同minArea。",
            "title": "最大面积", "type": "number", "minimum": 0.0, "default": 0.0
        },
        "minContourPoints": {
            "title": "最小轮廓点数", "type": "integer", "minimum": 0, "default": 0,
            "description": "当前轮廓polygon.points数量含下限；针对上游近似后顶点数，0不限制。",
        },
        "minPerimeter": {
            "description": "轮廓闭合周长或measurement.perimeter的含下限，单位pixel。",
            "title": "最小周长", "type": "number", "minimum": 0.0, "default": 0.0
        },
        "maxPerimeter": {"description": "周长含上限，0表示无上限。", "title": "最大周长", "type": "number", "minimum": 0.0, "default": 0.0},
        "circularityEnabled": {"title": "启用圆度筛选", "type": "boolean", "default": False},
        "minCircularity": {
            "description": "圆度含下限；Blob取attributes.circularity且字段缺失不通过已启用筛选，measurement取本身circularity。",
            "title": "最小圆度", "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0
        },
        "maxCircularity": {
            "description": "圆度含上限，1是实际上限而非无限哨兵；与minCircularity共同约束[0,1]。",
            "title": "最大圆度", "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 1.0
        },
        "blobLabels": {
            "description": "允许的连通域整数label列表；空列表不限制，按label而非blobId过滤。",
            "title": "连通域标签", "type": "array", "items": {
            "title": "列表项", "description": "列表单项：允许的连通域整数label列表；空列表不限制，按label而非blobId过滤。",
            "type": "integer", "minimum": 0
        }, "default": []
        },
        "contourMode": {
            "description": "any不限制；present要求Blob.contour非None；absent要求None。",
            "title": "轮廓筛选模式", "type": "string", "enum": ["any", "present", "absent"], "default": "any"
        },
        "minConfidence": {
            "description": "Detection.confidence含下限，范围[0,1]；不是框架执行成功概率。",
            "title": "最低置信度", "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0
        },
        "maxConfidence": {
            "description": "Detection.confidence含上限，默认1。",
            "title": "最高置信度", "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 1.0
        },
        "classIds": {
            "description": "允许的Detection.classId整数列表；空列表不限制。",
            "title": "类别编号", "type": "array", "items": {
            "title": "列表项", "description": "列表单项：允许的Detection.classId整数列表；空列表不限制。",
            "type": "integer", "minimum": 0
        }, "default": []
        },
        "detectionLabels": {
            "description": "允许的Detection.label字符串列表；空列表不限制，精确匹配。",
            "title": "检测标签", "type": "array", "items": {
            "title": "列表项", "description": "列表单项：允许的Detection.label字符串列表；空列表不限制，精确匹配。",
            "type": "string"
        }, "default": []
        },
        "geometryTypes": {
            "description": "允许的检测几何类型列表；优先item.geometry，无geometry时用bbox；空列表不限制。",
            "title": "几何类型", "type": "array", "default": [],
            "items": {"title": "允许的几何类型", "description": "矩形框、多边形或旋转矩形框。",
                      "type": "string", "enum": sorted(_GEOMETRY_TYPES)},
        },
        "holeMode": {
            "description": "any不限；outer要求isHole=false；hole要求isHole=true。",
            "title": "孔洞筛选模式", "type": "string", "enum": ["any", "outer", "hole"], "default": "any"
        },
        "sourceTypes": {
            "description": "允许的ShapeMeasurement.sourceType（contour/blob）列表；空列表不限制。",
            "title": "来源类型", "type": "array", "default": [],
            "items": {"title": "允许的来源类型", "description": "形状测量的来源，可为轮廓或连通域。",
                      "type": "string", "enum": ["contour", "blob"]},
        },
        "minLength": {"description": "Line两端欧氏距离含下限，单位pixel。", "title": "最小长度", "type": "number", "minimum": 0.0, "default": 0.0},
        "maxLength": {"description": "Line长度含上限，0无上限。", "title": "最大长度", "type": "number", "minimum": 0.0, "default": 0.0},
        "minRadius": {
            "description": "Circle.radius含下限，单位pixel。",
            "title": "最小半径（像素）", "type": "number", "minimum": 0.0, "default": 0.0
        },
        "maxRadius": {
            "description": "Circle半径含上限，0无上限。",
            "title": "最大半径（像素）", "type": "number", "minimum": 0.0, "default": 0.0
        },
        "minQuality": {
            "description": "TemplateMatch.quality含下限；使用已定义质量分值，不以rawScore直接代替。",
            "title": "最低质量", "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0
        },
        "maxQuality": {
            "description": "模板quality含上限，范围[0,1]。",
            "title": "最高质量", "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 1.0
        },
        "templateMethods": {
            "description": "允许的TemplateMatchCollection.method列表；按集合原始方法精确筛选，空列表不限。",
            "title": "模板匹配方法", "type": "array", "default": [],
            "items": {"title": "允许的匹配方法", "description": "一个模板匹配算法名称，按集合的方法精确匹配。",
                      "type": "string", "enum": _METHODS},
        },
        "spatialMode": {
            "description": "none无空间筛选；centerInside按对象中心落在原ROI几何中；bboxIntersects/bboxContained使用对象及ROI的轴对齐包围框；边界相交计入。",
            "title": "空间筛选模式", "type": "string", "enum": sorted(_SPATIAL_MODES), "default": "none"
        },
    },
}


def _groupedParamSchema() -> dict[str, object]:
    fields = cast(dict[str, dict[str, object]], _PARAM_SCHEMA["properties"])
    labels = {
        "contourMode": {"any": "不限", "present": "存在轮廓", "absent": "无轮廓"},
        "geometryTypes": {"bbox2d": "矩形框", "polygon2d": "多边形", "rotatedBox2d": "旋转矩形框"},
        "holeMode": {"any": "不限", "outer": "外轮廓", "hole": "孔洞轮廓"},
        "sourceTypes": {"contour": "轮廓", "blob": "连通域"},
        "templateMethods": {
            "sqdiff": "平方差", "sqdiffNormed": "归一化平方差",
            "ccorr": "互相关", "ccorrNormed": "归一化互相关",
            "ccoeff": "相关系数", "ccoeffNormed": "归一化相关系数",
        },
        "spatialMode": {
            "none": "请选择空间关系", "centerInside": "中心位于区域内",
            "bboxIntersects": "包围框与区域相交", "bboxContained": "包围框完全位于区域内",
        },
    }
    properties: dict[str, object] = {
        "collectionType": {
            "description": "筛选对象；为空时在执行阶段采用实际输入集合端口。",
            "title": "筛选对象", "type": "string", "default": "",
            "enum": ["", *COLLECTION_TYPES],
            "xOptionLabels": {"": "请选择筛选对象", **COLLECTION_TYPES},
        },
    }
    for flag, group in FILTER_GROUPS.items():
        visibleTypes = list(group["types"])
        validateTypes = ["", *group["types"]]
        flagSchema: dict[str, object] = {
            "title": group["title"], "type": "boolean",
            "description": f"开启后对{'、'.join(COLLECTION_TYPES[kind] for kind in visibleTypes)}应用此组条件。关闭时保留编辑值，执行时忽略此组条件。",
            "xVisibleWhen": {"collectionType": visibleTypes},
            "xValidateWhen": {"collectionType": validateTypes},
        }
        # New flags must stay absent when snapshots merge legacy schema defaults.
        if flag == "circularityEnabled":
            flagSchema["default"] = False
        properties[flag] = flagSchema
        for name in group["fields"]:
            field = dict(fields[name])
            field["xVisibleWhen"] = {"collectionType": visibleTypes, flag: [True]}
            field["xValidateWhen"] = {"collectionType": validateTypes, flag: [True]}
            if name in labels:
                field["xOptionLabels"] = labels[name]
                if field["type"] == "array":
                    field["items"] = {**cast(dict, field["items"]), "xOptionLabels": labels[name]}
            if name in {"blobLabels", "classIds"}:
                field["xWidget"] = "integer-list"
            properties[name] = field
    return {"type": "object", "xCollectionFilter": True, "properties": properties}


_PARAM_SCHEMA = _groupedParamSchema()


def _collectionInputs() -> dict[str, object]:
    return {
        "blobs": {"type": "blobCollection", "required": False, "nullable": False, "schemaVersion": "1.x"},
        "detections": {"type": "detectionCollection", "required": False, "nullable": False, "schemaVersion": "1.x"},
        "contours": {"type": "contourCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
        "measurements": {"type": "shapeMeasurementCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
        "lines": {"type": "lineCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
        "circles": {"type": "circleCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
        "matches": {"type": "templateMatchCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
        "roi": {"type": "geometry2d", "required": False, "nullable": False, "schemaVersion": "1.x"},
    }


def _collectionOutputs() -> dict[str, object]:
    result: dict[str, object] = {
        "keptCount": {"type": "integer", "required": True, "nullable": False},
        "rejectedCount": {"type": "integer", "required": True, "nullable": False},
    }
    for name, typeName, version in (
        ("Blobs", "blobCollection", "1.x"),
        ("Detections", "detectionCollection", "1.x"),
        ("Contours", "contourCollection", "1.2"),
        ("Measurements", "shapeMeasurementCollection", "1.2"),
        ("Lines", "lineCollection", "1.2"),
        ("Circles", "circleCollection", "1.2"),
        ("Matches", "templateMatchCollection", "1.2"),
    ):
        for prefix in ("kept", "rejected"):
            result[f"{prefix}{name}"] = {
                "type": typeName,
                "required": False,
                "nullable": False,
                "schemaVersion": version,
            }
    return result


class CollectionFilterOperator:
    meta = OperatorMeta(
        operatorId="vision.collection.filter",
        displayName="Filter Collection",
        version="1.3.0",
        inputPorts=_collectionInputs(),
        outputPorts=_collectionOutputs(),
        paramSchema=_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        selected = params.get("collectionType", "")
        if not isinstance(selected, str) or (selected and selected not in COLLECTION_TYPES):
            return _paramError("collectionType must be a supported collection input port or empty")
        params = effectiveFilterParams(params)
        for flag in FILTER_GROUPS:
            if not isinstance(params.get(flag, flag != "circularityEnabled"), bool):
                return _paramError(f"{flag} must be boolean")
        minContourPoints = params.get("minContourPoints", 0)
        if not _integer(minContourPoints) or minContourPoints < 0:
            return _paramError("minContourPoints must be a non-negative integer")
        for minimumName, maximumName in (
            ("minArea", "maxArea"),
            ("minPerimeter", "maxPerimeter"),
            ("minLength", "maxLength"),
            ("minRadius", "maxRadius"),
        ):
            minimum = params.get(minimumName, 0.0)
            maximum = params.get(maximumName, 0.0)
            if not _nonNegativeNumber(minimum) or not _nonNegativeNumber(maximum):
                return _paramError(f"{minimumName} and {maximumName} must be >= 0")
            if float(maximum) != 0.0 and float(maximum) < float(minimum):
                return _paramError(f"{maximumName} must be 0 or >= {minimumName}")
        for minimumName, maximumName in (
            ("minCircularity", "maxCircularity"),
            ("minConfidence", "maxConfidence"),
            ("minQuality", "maxQuality"),
        ):
            minimum = params.get(minimumName, 0.0)
            maximum = params.get(maximumName, 1.0)
            if not _unitNumber(minimum) or not _unitNumber(maximum):
                return _paramError(f"{minimumName} and {maximumName} must be in [0, 1]")
            if float(minimum) > float(maximum):
                return _paramError(f"{maximumName} must be >= {minimumName}")
        if params.get("contourMode", "any") not in ("any", "present", "absent"):
            return _paramError("contourMode must be any, present, or absent")
        if params.get("holeMode", "any") not in ("any", "outer", "hole"):
            return _paramError("holeMode must be any, outer, or hole")
        spatialMode = params.get("spatialMode", "none")
        if not isinstance(spatialMode, str) or spatialMode not in _SPATIAL_MODES:
            return _paramError("spatialMode is invalid")
        if params.get("spatialEnabled") is True and spatialMode == "none":
            return _paramError("spatialMode must select a spatial relation when spatialEnabled is true")
        for name in ("blobLabels", "classIds"):
            if not _integerList(params.get(name, [])):
                return _paramError(f"{name} must contain non-negative integers")
        if not _stringList(params.get("detectionLabels", [])):
            return _paramError("detectionLabels must contain strings")
        if not _enumList(params.get("geometryTypes", []), _GEOMETRY_TYPES):
            return _paramError("geometryTypes contains an unsupported type")
        if not _enumList(params.get("sourceTypes", []), {"contour", "blob"}):
            return _paramError("sourceTypes contains an unsupported type")
        if not _enumList(params.get("templateMethods", []), set(_METHODS)):
            return _paramError("templateMethods contains an unsupported method")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        started = perf_counter()
        try:
            collection = parseCollectionInputs(inputs)
            inputPort = next(name for name in COLLECTION_TYPES if name in inputs)
            selected = params.get("collectionType", "")
            if selected not in ("", inputPort):
                return _error(
                    "E_INPUT_TYPE",
                    f"collectionType {selected!r} does not match input '{inputPort}'",
                )
            params = effectiveFilterParams(params, inputPort)
            paramError = self.validateParams(params)
            if paramError is not None:
                return {"status": "error", "error": paramError}
            roi = None
            spatialMode = cast(str, params.get("spatialMode", "none"))
            if spatialMode != "none":
                if "roi" not in inputs:
                    return _error(
                        "E_INPUT_MISSING", "input 'roi' is required for spatial filtering"
                    )
                roi = parseRoi(inputs["roi"])
                ensureSameCoordinateSpace(
                    collection.coordinateSpace,
                    roi.coordinateSpace,
                    message="collection and ROI coordinate spaces do not match",
                )
            kept: list[CollectionItem] = []
            rejected: list[CollectionItem] = []
            for item in collection.items:
                matches = _matchesItem(item, collection, params)
                if matches and roi is not None:
                    if spatialMode == "centerInside":
                        centerX, centerY = itemCenter(item)
                        matches = pointInGeometry(centerX, centerY, roi)
                    else:
                        itemBox = itemBBox(item)
                        roiBox = axisAlignedBBox(roi)
                        matches = (
                            bboxesIntersect(itemBox, roiBox)
                            if spatialMode == "bboxIntersects"
                            else bboxContained(itemBox, roiBox)
                        )
                (kept if matches else rejected).append(item)
        except CollectionInputError as err:
            return _error(err.code, str(err))
        outputs: dict[str, object] = {
            collection.outputName("kept"): collection.payload(kept),
            collection.outputName("rejected"): collection.payload(rejected),
            "keptCount": len(kept),
            "rejectedCount": len(rejected),
        }
        return {
            "status": "ok",
            "outputs": outputs,
            "metrics": {
                "latencyMs": round((perf_counter() - started) * 1000.0, 3),
                "collectionKind": collection.kind,
                "keptCount": len(kept),
                "rejectedCount": len(rejected),
            },
            "diagnostics": {
                "text": f"Kept {len(kept)} of {len(collection.items)} items",
                "effectiveParameters": _effectiveSummary(params, inputPort, selected == ""),
            },
        }


def _effectiveSummary(params: dict[str, object], inputPort: str, inferred: bool) -> list[dict[str, object]]:
    fields = cast(dict[str, dict[str, object]], _PARAM_SCHEMA["properties"])
    selected: dict[str, object] = {}
    for flag, group in FILTER_GROUPS.items():
        if inputPort not in group["types"]:
            continue
        enabled = params.get(flag, flag != "circularityEnabled")
        selected[flag] = enabled
        if enabled:
            for name in group["fields"]:
                selected[name] = params.get(name, fields[name]["default"])
    return [
        {"path": ["collectionType"], "value": inputPort, "source": "input" if inferred else "normalized"},
        *({"path": [name], "value": value, "source": "normalized"} for name, value in selected.items()),
    ]


def _matchesItem(
    item: CollectionItem, collection: ParsedCollection, params: dict[str, object]
) -> bool:
    if isinstance(item, Blob2D):
        if not _range(item.area, params, "minArea", "maxArea"):
            return False
        labels = cast(list[int], params.get("blobLabels", []))
        if labels and item.label not in labels:
            return False
        mode = cast(str, params.get("contourMode", "any"))
        if mode == "present" and item.contour is None:
            return False
        if mode == "absent" and item.contour is not None:
            return False
        if cast(bool, params.get("circularityEnabled", False)):
            missing, value = itemField(item, "circularity")
            return not missing and _unitRange(float(value), params, "minCircularity", "maxCircularity")
        return True
    if isinstance(item, Detection2D):
        if not _unitRange(item.confidence, params, "minConfidence", "maxConfidence"):
            return False
        classIds = cast(list[int], params.get("classIds", []))
        detectionLabels = cast(list[str], params.get("detectionLabels", []))
        types = cast(list[str], params.get("geometryTypes", []))
        geometry = item.geometry if item.geometry is not None else item.bbox
        return (
            (not classIds or item.classId in classIds)
            and (not detectionLabels or item.label in detectionLabels)
            and (not types or geometryType(geometry) in types)
        )
    if isinstance(item, ContourItem):
        area = float(itemField(item, "area")[1])
        perimeter = float(itemField(item, "perimeter")[1])
        holeMode = cast(str, params.get("holeMode", "any"))
        return (
            len(item.polygon.points) >= cast(int, params.get("minContourPoints", 0))
            and _range(area, params, "minArea", "maxArea")
            and _range(perimeter, params, "minPerimeter", "maxPerimeter")
            and (holeMode == "any" or (holeMode == "hole") == item.isHole)
        )
    if isinstance(item, ShapeMeasurement):
        sourceTypes = cast(list[str], params.get("sourceTypes", []))
        result = (
            _range(item.area, params, "minArea", "maxArea")
            and _range(item.perimeter, params, "minPerimeter", "maxPerimeter")
            and (not sourceTypes or item.sourceType in sourceTypes)
        )
        if result and cast(bool, params.get("circularityEnabled", False)):
            result = _unitRange(
                item.circularity, params, "minCircularity", "maxCircularity"
            )
        return result
    if isinstance(item, LineItem):
        return _range(
            float(itemField(item, "length")[1]), params, "minLength", "maxLength"
        )
    if isinstance(item, CircleItem):
        return _range(item.circle.radius, params, "minRadius", "maxRadius")
    match = cast(TemplateMatch, item)
    methods = cast(list[str], params.get("templateMethods", []))
    source = cast(TemplateMatchCollection, collection.source)
    return _unitRange(match.quality, params, "minQuality", "maxQuality") and (
        not methods or source.method in methods
    )


def _range(
    value: float,
    params: dict[str, object],
    minimumName: str,
    maximumName: str,
) -> bool:
    minimum = float(cast(int | float, params.get(minimumName, 0.0)))
    maximum = float(cast(int | float, params.get(maximumName, 0.0)))
    return value >= minimum and (maximum == 0.0 or value <= maximum)


def _unitRange(
    value: float,
    params: dict[str, object],
    minimumName: str,
    maximumName: str,
) -> bool:
    return float(cast(int | float, params.get(minimumName, 0.0))) <= value <= float(
        cast(int | float, params.get(maximumName, 1.0))
    )


def _integerList(value: object) -> bool:
    return isinstance(value, list) and all(_integer(item) and item >= 0 for item in value)


def _stringList(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _enumList(value: object, allowed: set[str]) -> bool:
    return _stringList(value) and all(item in allowed for item in cast(list[str], value))


def _nonNegativeNumber(value: object) -> TypeGuard[int | float]:
    return _finite(value) and float(value) >= 0.0


def _unitNumber(value: object) -> TypeGuard[int | float]:
    return _finite(value) and 0.0 <= float(value) <= 1.0


def _integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
