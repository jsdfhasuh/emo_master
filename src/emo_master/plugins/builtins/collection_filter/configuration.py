from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import TypedDict


COLLECTION_TYPES = {
    "detections": "检测结果",
    "blobs": "连通域",
    "contours": "轮廓",
    "measurements": "形状测量",
    "lines": "直线",
    "circles": "圆",
    "matches": "模板匹配结果",
}


class FilterGroup(TypedDict):
    title: str
    types: tuple[str, ...]
    fields: tuple[str, ...]


FILTER_GROUPS: dict[str, FilterGroup] = {
    "areaEnabled": {
        "title": "面积筛选", "types": ("blobs", "contours", "measurements"),
        "fields": ("minArea", "maxArea"),
    },
    "perimeterEnabled": {
        "title": "周长筛选", "types": ("contours", "measurements"),
        "fields": ("minPerimeter", "maxPerimeter"),
    },
    "circularityEnabled": {
        "title": "圆度筛选", "types": ("blobs", "measurements"),
        "fields": ("minCircularity", "maxCircularity"),
    },
    "blobLabelsEnabled": {
        "title": "连通域标签筛选", "types": ("blobs",), "fields": ("blobLabels",),
    },
    "contourEnabled": {
        "title": "轮廓存在性筛选", "types": ("blobs",), "fields": ("contourMode",),
    },
    "confidenceEnabled": {
        "title": "置信度筛选", "types": ("detections",),
        "fields": ("minConfidence", "maxConfidence"),
    },
    "classIdsEnabled": {
        "title": "类别编号筛选", "types": ("detections",), "fields": ("classIds",),
    },
    "detectionLabelsEnabled": {
        "title": "检测标签筛选", "types": ("detections",), "fields": ("detectionLabels",),
    },
    "geometryTypesEnabled": {
        "title": "几何类型筛选", "types": ("detections",), "fields": ("geometryTypes",),
    },
    "holeEnabled": {
        "title": "孔洞筛选", "types": ("contours",), "fields": ("holeMode",),
    },
    "sourceTypesEnabled": {
        "title": "来源类型筛选", "types": ("measurements",), "fields": ("sourceTypes",),
    },
    "lengthEnabled": {
        "title": "长度筛选", "types": ("lines",), "fields": ("minLength", "maxLength"),
    },
    "radiusEnabled": {
        "title": "半径筛选", "types": ("circles",), "fields": ("minRadius", "maxRadius"),
    },
    "qualityEnabled": {
        "title": "质量筛选", "types": ("matches",), "fields": ("minQuality", "maxQuality"),
    },
    "templateMethodsEnabled": {
        "title": "模板匹配方法筛选", "types": ("matches",), "fields": ("templateMethods",),
    },
    "spatialEnabled": {
        "title": "空间关系筛选", "types": tuple(COLLECTION_TYPES), "fields": ("spatialMode",),
    },
}

_NEUTRAL_VALUES: dict[str, object] = {
    "minArea": 0.0, "maxArea": 0.0,
    "minPerimeter": 0.0, "maxPerimeter": 0.0,
    "minCircularity": 0.0, "maxCircularity": 1.0,
    "blobLabels": [], "contourMode": "any",
    "minConfidence": 0.0, "maxConfidence": 1.0,
    "classIds": [], "detectionLabels": [], "geometryTypes": [],
    "holeMode": "any", "sourceTypes": [],
    "minLength": 0.0, "maxLength": 0.0,
    "minRadius": 0.0, "maxRadius": 0.0,
    "minQuality": 0.0, "maxQuality": 1.0,
    "templateMethods": [], "spatialMode": "none",
}


def prepareFilterParams(
    params: Mapping[str, object],
    collectionType: str = "",
    dynamicParameters: Iterable[str] = (),
) -> dict[str, object]:
    """Prepare an editor copy before form defaults, retaining inactive drafts.

    dynamicParameters contains bound parameter names. Circularity was already
    opt-in in legacy projects, so missing circularityEnabled still means off.
    """
    result = deepcopy(dict(params))
    selected = result.get("collectionType", "")
    if selected == "":
        selected = collectionType
    result["collectionType"] = selected
    bound = set(dynamicParameters)
    for flag, group in FILTER_GROUPS.items():
        if flag in result:
            continue
        result[flag] = flag != "circularityEnabled" and (
            flag in bound or any(
                field in bound or result.get(field, _NEUTRAL_VALUES[field]) != _NEUTRAL_VALUES[field]
                for field in group["fields"]
            )
        )
    # An inactive legacy mode needs a usable choice when the editor enables it.
    if result["spatialEnabled"] is False and result.get("spatialMode", "none") == "none":
        result["spatialMode"] = "centerInside"
    return result


def effectiveFilterParams(
    params: Mapping[str, object], collectionType: str = ""
) -> dict[str, object]:
    """Return execution/validation values; never erase disabled saved values."""
    result = deepcopy(dict(params))
    selected = result.get("collectionType", "")
    if selected == "":
        selected = collectionType
    result["collectionType"] = selected
    for flag, group in FILTER_GROUPS.items():
        # Only circularity required an explicit opt-in before group controls.
        enabled = result.get(flag, flag != "circularityEnabled")
        applicable = selected == "" or selected in group["types"]
        if not applicable or enabled is False:
            result[flag] = False
            for field in group["fields"]:
                result[field] = deepcopy(_NEUTRAL_VALUES[field])
    return result
