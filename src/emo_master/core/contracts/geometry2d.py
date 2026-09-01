from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field


VISION_PAYLOAD_SCHEMA_VERSION = "1.1"
VISION_PAYLOAD_SCHEMA_VERSION_1_2 = "1.2"
SUPPORTED_VISION_PAYLOAD_SCHEMA_VERSIONS = frozenset({"1.0", "1.1", "1.2"})
IDENTITY_TRANSFORM_TO_SOURCE = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
IDENTITY_HOMOGRAPHY_TO_SOURCE = (
    1.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    1.0,
)
CONCRETE_GEOMETRY_PORT_TYPES = frozenset(
    {"point2d", "bbox2d", "rotatedBox2d", "polygon2d", "line2d", "circle2d"}
)
VISION_PAYLOAD_PORT_TYPES = frozenset(
    {
        *CONCRETE_GEOMETRY_PORT_TYPES,
        "vector2d",
        "geometry2d",
        "blobCollection",
        "detectionCollection",
        "colorStatistics",
        "contourCollection",
        "shapeMeasurementCollection",
        "histogram",
        "lineCollection",
        "circleCollection",
        "templateMatchCollection",
    }
)


class PayloadValidationError(ValueError):
    """Raised when a value does not satisfy a vision payload contract."""


def _object(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PayloadValidationError(f"{path} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise PayloadValidationError(f"{path} keys must be strings")
    return value


def _keys(
    value: Mapping[str, object],
    path: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    actual = set(value)
    missing = sorted(required - actual)
    if missing:
        raise PayloadValidationError(f"{path} is missing: {', '.join(missing)}")
    unknown = sorted(actual - required - optional)
    if unknown:
        raise PayloadValidationError(f"{path} has unknown fields: {', '.join(unknown)}")


def _typedObject(
    value: object,
    path: str,
    payloadType: str,
    required: set[str],
    optional: set[str] | None = None,
) -> Mapping[str, object]:
    payload = _object(value, path)
    _keys(
        payload,
        path,
        {"type", "schemaVersion", *required},
        optional,
    )
    if payload["type"] != payloadType:
        raise PayloadValidationError(
            f"{path}.type must be {payloadType!r}, got {payload['type']!r}"
        )
    validateVisionPayloadSchemaVersion(payload["schemaVersion"], path)
    return payload


def validateVisionPayloadSchemaVersion(value: object, path: str = "payload") -> str:
    if not isinstance(value, str):
        raise PayloadValidationError(f"{path}.schemaVersion must be a string")
    parts = value.split(".")
    if len(parts) != 2 or any(
        not part.isdigit() or (len(part) > 1 and part.startswith("0"))
        for part in parts
    ):
        raise PayloadValidationError(
            f"{path}.schemaVersion must use major.minor numeric form"
        )
    if value not in SUPPORTED_VISION_PAYLOAD_SCHEMA_VERSIONS:
        raise PayloadValidationError(
            f"unsupported {path}.schemaVersion: {value!r}; "
            f"supported: {', '.join(sorted(SUPPORTED_VISION_PAYLOAD_SCHEMA_VERSIONS))}"
        )
    return value


def isVisionPayloadSchemaVersionCompatible(
    actualVersion: object,
    expectedVersion: object | None = None,
) -> bool:
    try:
        actual = validateVisionPayloadSchemaVersion(actualVersion)
    except PayloadValidationError:
        return False
    if expectedVersion is None:
        return True
    if not isinstance(expectedVersion, str):
        return False
    if expectedVersion.endswith(".x"):
        major = expectedVersion[:-2]
        return major.isdigit() and actual.split(".", 1)[0] == major
    return actual == expectedVersion


def _string(value: object, path: str, *, allowEmpty: bool = False) -> str:
    if not isinstance(value, str) or (not allowEmpty and value == ""):
        suffix = "a string" if allowEmpty else "a non-empty string"
        raise PayloadValidationError(f"{path} must be {suffix}")
    return value


def _optionalString(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _integer(
    value: object,
    path: str,
    *,
    minimum: int | None = None,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PayloadValidationError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise PayloadValidationError(f"{path} must be >= {minimum}")
    return value


def _number(
    value: object,
    path: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PayloadValidationError(f"{path} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise PayloadValidationError(f"{path} must be finite")
    if minimum is not None and result < minimum:
        raise PayloadValidationError(f"{path} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise PayloadValidationError(f"{path} must be <= {maximum}")
    return result


def _jsonValue(value: object, path: str) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PayloadValidationError(f"{path} must not contain NaN or infinity")
        return value
    if isinstance(value, list):
        return [_jsonValue(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise PayloadValidationError(f"{path} object keys must be strings")
        return {
            key: _jsonValue(item, f"{path}.{key}")
            for key, item in value.items()
        }
    raise PayloadValidationError(f"{path} must contain JSON-compatible values")


def _jsonObject(value: object, path: str) -> dict[str, object]:
    payload = _object(value, path)
    return {
        key: _jsonValue(item, f"{path}.{key}")
        for key, item in payload.items()
    }


def _determinant3x3(values: tuple[float, ...]) -> float:
    a, b, c, d, e, f, g, h, i = values
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def normalizeHomography(values: object, path: str = "homography") -> tuple[float, ...]:
    if not isinstance(values, (list, tuple)):
        raise PayloadValidationError(f"{path} must be an array")
    matrix = tuple(
        _number(value, f"{path}[{index}]") for index, value in enumerate(values)
    )
    if len(matrix) != 9:
        raise PayloadValidationError(f"{path} must contain 9 numbers")
    norm = math.sqrt(sum(value * value for value in matrix))
    if norm <= 1e-12:
        raise PayloadValidationError(f"{path} must be non-zero")
    normalized = tuple(value / norm for value in matrix)
    first = next((value for value in normalized if abs(value) > 1e-12), 0.0)
    if first < 0.0:
        normalized = tuple(-value for value in normalized)
    if abs(_determinant3x3(normalized)) <= 1e-12:
        raise PayloadValidationError(f"{path} must be invertible")
    return normalized


def affineToHomography(values: object) -> tuple[float, ...]:
    if not isinstance(values, (list, tuple)):
        raise PayloadValidationError("affine transform must be an array")
    affine = tuple(
        _number(value, f"affine[{index}]") for index, value in enumerate(values)
    )
    if len(affine) != 6:
        raise PayloadValidationError("affine transform must contain 6 numbers")
    a, b, c, d, e, f = affine
    return (a, c, e, b, d, f, 0.0, 0.0, 1.0)


@dataclass(frozen=True)
class CoordinateSpace2D:
    """Coordinate metadata shared by every geometry payload in schema v1."""

    origin: str = "topLeft"
    xAxis: str = "right"
    yAxis: str = "down"
    unit: str = "pixel"
    reference: str = "sourceImage"
    imageWidth: int | None = None
    imageHeight: int | None = None
    sourceId: str = ""
    transformToSource: tuple[float, ...] | None = IDENTITY_TRANSFORM_TO_SOURCE
    homographyToSource: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if self.origin != "topLeft":
            raise PayloadValidationError("coordinateSpace.origin must be 'topLeft'")
        if self.xAxis != "right":
            raise PayloadValidationError("coordinateSpace.xAxis must be 'right'")
        if self.yAxis != "down":
            raise PayloadValidationError("coordinateSpace.yAxis must be 'down'")
        if self.unit != "pixel":
            raise PayloadValidationError("coordinateSpace.unit must be 'pixel'")
        _string(self.reference, "coordinateSpace.reference")
        if self.sourceId == "":
            object.__setattr__(self, "sourceId", self.reference)
        else:
            _string(self.sourceId, "coordinateSpace.sourceId")
        if (self.imageWidth is None) != (self.imageHeight is None):
            raise PayloadValidationError(
                "coordinateSpace.imageWidth and imageHeight must be supplied together"
            )
        if self.imageWidth is not None:
            _integer(self.imageWidth, "coordinateSpace.imageWidth", minimum=1)
            _integer(self.imageHeight, "coordinateSpace.imageHeight", minimum=1)
        if self.transformToSource is not None and self.homographyToSource is not None:
            raise PayloadValidationError(
                "coordinateSpace transformToSource and homographyToSource are mutually exclusive"
            )
        if self.homographyToSource is not None:
            homography = normalizeHomography(
                self.homographyToSource,
                "coordinateSpace.homographyToSource",
            )
            object.__setattr__(self, "transformToSource", None)
            object.__setattr__(self, "homographyToSource", homography)
            return
        rawTransform = (
            IDENTITY_TRANSFORM_TO_SOURCE
            if self.transformToSource is None
            else self.transformToSource
        )
        transform = tuple(
            _number(value, f"coordinateSpace.transformToSource[{index}]")
            for index, value in enumerate(rawTransform)
        )
        if len(transform) != 6:
            raise PayloadValidationError(
                "coordinateSpace.transformToSource must contain 6 numbers"
            )
        a, b, c, d, _, _ = transform
        if abs(a * d - b * c) < 1e-12:
            raise PayloadValidationError(
                "coordinateSpace.transformToSource must be invertible"
            )
        object.__setattr__(self, "transformToSource", transform)
        object.__setattr__(self, "homographyToSource", None)

    def toPayload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "origin": self.origin,
            "xAxis": self.xAxis,
            "yAxis": self.yAxis,
            "unit": self.unit,
            "reference": self.reference,
            "sourceId": self.sourceId,
        }
        if self.homographyToSource is not None:
            payload["homographyToSource"] = list(self.homographyToSource)
        else:
            payload["transformToSource"] = list(
                self.transformToSource or IDENTITY_TRANSFORM_TO_SOURCE
            )
        if self.imageWidth is not None:
            payload["imageWidth"] = self.imageWidth
            payload["imageHeight"] = self.imageHeight
        return payload

    def mapPointToSource(self, x: float, y: float) -> tuple[float, float]:
        pointX = _number(x, "x")
        pointY = _number(y, "y")
        if self.homographyToSource is None:
            a, b, c, d, e, f = (
                self.transformToSource or IDENTITY_TRANSFORM_TO_SOURCE
            )
            return (
                a * pointX + c * pointY + e,
                b * pointX + d * pointY + f,
            )
        h00, h01, h02, h10, h11, h12, h20, h21, h22 = self.homographyToSource
        denominator = h20 * pointX + h21 * pointY + h22
        if abs(denominator) <= 1e-12:
            raise PayloadValidationError(
                "homography maps the point to infinity (near-zero denominator)"
            )
        return (
            (h00 * pointX + h01 * pointY + h02) / denominator,
            (h10 * pointX + h11 * pointY + h12) / denominator,
        )

    def matrixToSource(self) -> tuple[float, ...]:
        if self.homographyToSource is not None:
            return self.homographyToSource
        return affineToHomography(
            self.transformToSource or IDENTITY_TRANSFORM_TO_SOURCE
        )

    @classmethod
    def fromPayload(
        cls,
        value: object,
        *,
        schemaVersion: object | None = None,
    ) -> CoordinateSpace2D:
        payload = _object(value, "coordinateSpace")
        _keys(
            payload,
            "coordinateSpace",
            {"origin", "xAxis", "yAxis", "unit", "reference"},
            {
                "imageWidth",
                "imageHeight",
                "sourceId",
                "transformToSource",
                "homographyToSource",
            },
        )
        version = (
            None
            if schemaVersion is None
            else validateVisionPayloadSchemaVersion(schemaVersion)
        )
        hasAffine = "transformToSource" in payload
        hasHomography = "homographyToSource" in payload
        if hasAffine and hasHomography:
            raise PayloadValidationError(
                "coordinateSpace transformToSource and homographyToSource are mutually exclusive"
            )
        if version in {"1.0", "1.1"} and hasHomography:
            raise PayloadValidationError(
                "coordinateSpace.homographyToSource requires schemaVersion '1.2'"
            )
        if version == "1.2" and not (hasAffine or hasHomography):
            raise PayloadValidationError(
                "schemaVersion '1.2' requires an explicit coordinate transform"
            )
        reference = _string(payload["reference"], "coordinateSpace.reference")
        imageWidth = payload.get("imageWidth")
        imageHeight = payload.get("imageHeight")
        rawTransform = payload.get("transformToSource")
        rawHomography = payload.get("homographyToSource")
        if rawTransform is not None and not isinstance(rawTransform, list):
            raise PayloadValidationError(
                "coordinateSpace.transformToSource must be an array"
            )
        if rawHomography is not None and not isinstance(rawHomography, list):
            raise PayloadValidationError(
                "coordinateSpace.homographyToSource must be an array"
            )
        return cls(
            origin=_string(payload["origin"], "coordinateSpace.origin"),
            xAxis=_string(payload["xAxis"], "coordinateSpace.xAxis"),
            yAxis=_string(payload["yAxis"], "coordinateSpace.yAxis"),
            unit=_string(payload["unit"], "coordinateSpace.unit"),
            reference=reference,
            imageWidth=(
                None
                if imageWidth is None
                else _integer(imageWidth, "coordinateSpace.imageWidth", minimum=1)
            ),
            imageHeight=(
                None
                if imageHeight is None
                else _integer(imageHeight, "coordinateSpace.imageHeight", minimum=1)
            ),
            sourceId=_string(
                payload.get("sourceId", reference), "coordinateSpace.sourceId"
            ),
            transformToSource=(
                IDENTITY_TRANSFORM_TO_SOURCE
                if rawTransform is None and rawHomography is None
                else None
                if rawTransform is None
                else tuple(
                    _number(value, f"coordinateSpace.transformToSource[{index}]")
                    for index, value in enumerate(rawTransform)
                )
            ),
            homographyToSource=(
                None
                if rawHomography is None
                else tuple(
                    _number(value, f"coordinateSpace.homographyToSource[{index}]")
                    for index, value in enumerate(rawHomography)
                )
            ),
        )


def schemaVersionForCoordinateSpace(space: CoordinateSpace2D) -> str:
    return (
        VISION_PAYLOAD_SCHEMA_VERSION_1_2
        if space.homographyToSource is not None
        else VISION_PAYLOAD_SCHEMA_VERSION
    )


@dataclass(frozen=True)
class Point2D:
    x: float
    y: float
    coordinateSpace: CoordinateSpace2D = field(default_factory=CoordinateSpace2D)

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _number(self.x, "point2d.x"))
        object.__setattr__(self, "y", _number(self.y, "point2d.y"))
        if not isinstance(self.coordinateSpace, CoordinateSpace2D):
            raise PayloadValidationError("point2d.coordinateSpace is invalid")

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "point2d",
            "schemaVersion": schemaVersionForCoordinateSpace(self.coordinateSpace),
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "x": self.x,
            "y": self.y,
        }

    @classmethod
    def fromPayload(cls, value: object) -> Point2D:
        payload = _typedObject(
            value,
            "point2d",
            "point2d",
            {"coordinateSpace", "x", "y"},
        )
        return cls(
            x=_number(payload["x"], "point2d.x"),
            y=_number(payload["y"], "point2d.y"),
            coordinateSpace=CoordinateSpace2D.fromPayload(
                payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
            ),
        )


@dataclass(frozen=True)
class Vector2D:
    dx: float
    dy: float
    coordinateSpace: CoordinateSpace2D = field(default_factory=CoordinateSpace2D)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dx", _number(self.dx, "vector2d.dx"))
        object.__setattr__(self, "dy", _number(self.dy, "vector2d.dy"))
        if not isinstance(self.coordinateSpace, CoordinateSpace2D):
            raise PayloadValidationError("vector2d.coordinateSpace is invalid")

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "vector2d",
            "schemaVersion": schemaVersionForCoordinateSpace(self.coordinateSpace),
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "dx": self.dx,
            "dy": self.dy,
        }

    @classmethod
    def fromPayload(cls, value: object) -> Vector2D:
        payload = _typedObject(
            value,
            "vector2d",
            "vector2d",
            {"coordinateSpace", "dx", "dy"},
        )
        return cls(
            dx=_number(payload["dx"], "vector2d.dx"),
            dy=_number(payload["dy"], "vector2d.dy"),
            coordinateSpace=CoordinateSpace2D.fromPayload(
                payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
            ),
        )


@dataclass(frozen=True)
class BBox2D:
    x: float
    y: float
    width: float
    height: float
    coordinateSpace: CoordinateSpace2D = field(default_factory=CoordinateSpace2D)

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _number(self.x, "bbox2d.x"))
        object.__setattr__(self, "y", _number(self.y, "bbox2d.y"))
        object.__setattr__(self, "width", _number(self.width, "bbox2d.width", minimum=0.0))
        object.__setattr__(self, "height", _number(self.height, "bbox2d.height", minimum=0.0))
        if not isinstance(self.coordinateSpace, CoordinateSpace2D):
            raise PayloadValidationError("bbox2d.coordinateSpace is invalid")

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "bbox2d",
            "schemaVersion": schemaVersionForCoordinateSpace(self.coordinateSpace),
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def fromPayload(cls, value: object) -> BBox2D:
        payload = _typedObject(
            value,
            "bbox2d",
            "bbox2d",
            {"coordinateSpace", "x", "y", "width", "height"},
        )
        return cls(
            x=_number(payload["x"], "bbox2d.x"),
            y=_number(payload["y"], "bbox2d.y"),
            width=_number(payload["width"], "bbox2d.width", minimum=0.0),
            height=_number(payload["height"], "bbox2d.height", minimum=0.0),
            coordinateSpace=CoordinateSpace2D.fromPayload(
                payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
            ),
        )


@dataclass(frozen=True)
class RotatedBox2D:
    centerX: float
    centerY: float
    width: float
    height: float
    angleDegrees: float
    coordinateSpace: CoordinateSpace2D = field(default_factory=CoordinateSpace2D)

    def __post_init__(self) -> None:
        object.__setattr__(self, "centerX", _number(self.centerX, "rotatedBox2d.centerX"))
        object.__setattr__(self, "centerY", _number(self.centerY, "rotatedBox2d.centerY"))
        object.__setattr__(
            self,
            "width",
            _number(self.width, "rotatedBox2d.width", minimum=0.0),
        )
        object.__setattr__(
            self,
            "height",
            _number(self.height, "rotatedBox2d.height", minimum=0.0),
        )
        object.__setattr__(
            self,
            "angleDegrees",
            _number(
                self.angleDegrees,
                "rotatedBox2d.angleDegrees",
                minimum=-180.0,
                maximum=180.0,
            ),
        )
        if self.angleDegrees == 180.0:
            raise PayloadValidationError("rotatedBox2d.angleDegrees must be < 180")
        if not isinstance(self.coordinateSpace, CoordinateSpace2D):
            raise PayloadValidationError("rotatedBox2d.coordinateSpace is invalid")

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "rotatedBox2d",
            "schemaVersion": schemaVersionForCoordinateSpace(self.coordinateSpace),
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "centerX": self.centerX,
            "centerY": self.centerY,
            "width": self.width,
            "height": self.height,
            "angleDegrees": self.angleDegrees,
        }

    @classmethod
    def fromPayload(cls, value: object) -> RotatedBox2D:
        payload = _typedObject(
            value,
            "rotatedBox2d",
            "rotatedBox2d",
            {
                "coordinateSpace",
                "centerX",
                "centerY",
                "width",
                "height",
                "angleDegrees",
            },
        )
        return cls(
            centerX=_number(payload["centerX"], "rotatedBox2d.centerX"),
            centerY=_number(payload["centerY"], "rotatedBox2d.centerY"),
            width=_number(payload["width"], "rotatedBox2d.width", minimum=0.0),
            height=_number(payload["height"], "rotatedBox2d.height", minimum=0.0),
            angleDegrees=_number(payload["angleDegrees"], "rotatedBox2d.angleDegrees"),
            coordinateSpace=CoordinateSpace2D.fromPayload(
                payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
            ),
        )


@dataclass(frozen=True)
class Polygon2D:
    points: tuple[Point2D, ...]

    def __post_init__(self) -> None:
        points = tuple(self.points)
        if len(points) < 3:
            raise PayloadValidationError("polygon2d.points must contain at least 3 points")
        if any(not isinstance(point, Point2D) for point in points):
            raise PayloadValidationError("polygon2d.points must contain Point2D values")
        coordinateSpace = points[0].coordinateSpace
        if any(point.coordinateSpace != coordinateSpace for point in points[1:]):
            raise PayloadValidationError("polygon2d points must use one coordinateSpace")
        object.__setattr__(self, "points", points)

    @property
    def coordinateSpace(self) -> CoordinateSpace2D:
        return self.points[0].coordinateSpace

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "polygon2d",
            "schemaVersion": schemaVersionForCoordinateSpace(self.coordinateSpace),
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "points": [{"x": point.x, "y": point.y} for point in self.points],
        }

    @classmethod
    def fromPayload(cls, value: object) -> Polygon2D:
        payload = _typedObject(
            value,
            "polygon2d",
            "polygon2d",
            {"coordinateSpace", "points"},
        )
        coordinateSpace = CoordinateSpace2D.fromPayload(
            payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
        )
        rawPoints = payload["points"]
        if not isinstance(rawPoints, list):
            raise PayloadValidationError("polygon2d.points must be an array")
        points: list[Point2D] = []
        for index, rawPoint in enumerate(rawPoints):
            point = _object(rawPoint, f"polygon2d.points[{index}]")
            _keys(point, f"polygon2d.points[{index}]", {"x", "y"})
            points.append(
                Point2D(
                    _number(point["x"], f"polygon2d.points[{index}].x"),
                    _number(point["y"], f"polygon2d.points[{index}].y"),
                    coordinateSpace,
                )
            )
        return cls(tuple(points))


@dataclass(frozen=True)
class Line2D:
    start: Point2D
    end: Point2D

    def __post_init__(self) -> None:
        if not isinstance(self.start, Point2D) or not isinstance(self.end, Point2D):
            raise PayloadValidationError("line2d.start and end must be Point2D")
        if self.start.coordinateSpace != self.end.coordinateSpace:
            raise PayloadValidationError("line2d endpoints must use one coordinateSpace")
        if math.isclose(self.start.x, self.end.x, abs_tol=1e-12) and math.isclose(
            self.start.y, self.end.y, abs_tol=1e-12
        ):
            raise PayloadValidationError("line2d endpoints must not be equal")

    @property
    def coordinateSpace(self) -> CoordinateSpace2D:
        return self.start.coordinateSpace

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "line2d",
            "schemaVersion": VISION_PAYLOAD_SCHEMA_VERSION_1_2,
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "start": {"x": self.start.x, "y": self.start.y},
            "end": {"x": self.end.x, "y": self.end.y},
        }

    @classmethod
    def fromPayload(cls, value: object) -> Line2D:
        payload = _typedObject(
            value,
            "line2d",
            "line2d",
            {"coordinateSpace", "start", "end"},
        )
        if payload["schemaVersion"] != VISION_PAYLOAD_SCHEMA_VERSION_1_2:
            raise PayloadValidationError("line2d requires schemaVersion '1.2'")
        space = CoordinateSpace2D.fromPayload(
            payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
        )
        points: list[Point2D] = []
        for name in ("start", "end"):
            rawPoint = _object(payload[name], f"line2d.{name}")
            _keys(rawPoint, f"line2d.{name}", {"x", "y"})
            points.append(
                Point2D(
                    _number(rawPoint["x"], f"line2d.{name}.x"),
                    _number(rawPoint["y"], f"line2d.{name}.y"),
                    space,
                )
            )
        return cls(points[0], points[1])


@dataclass(frozen=True)
class Circle2D:
    center: Point2D
    radius: float

    def __post_init__(self) -> None:
        if not isinstance(self.center, Point2D):
            raise PayloadValidationError("circle2d.center must be Point2D")
        object.__setattr__(
            self,
            "radius",
            _number(self.radius, "circle2d.radius", minimum=0.0),
        )
        if self.radius == 0.0:
            raise PayloadValidationError("circle2d.radius must be > 0")

    @property
    def coordinateSpace(self) -> CoordinateSpace2D:
        return self.center.coordinateSpace

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "circle2d",
            "schemaVersion": VISION_PAYLOAD_SCHEMA_VERSION_1_2,
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "center": {"x": self.center.x, "y": self.center.y},
            "radius": self.radius,
        }

    @classmethod
    def fromPayload(cls, value: object) -> Circle2D:
        payload = _typedObject(
            value,
            "circle2d",
            "circle2d",
            {"coordinateSpace", "center", "radius"},
        )
        if payload["schemaVersion"] != VISION_PAYLOAD_SCHEMA_VERSION_1_2:
            raise PayloadValidationError("circle2d requires schemaVersion '1.2'")
        space = CoordinateSpace2D.fromPayload(
            payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
        )
        rawCenter = _object(payload["center"], "circle2d.center")
        _keys(rawCenter, "circle2d.center", {"x", "y"})
        return cls(
            Point2D(
                _number(rawCenter["x"], "circle2d.center.x"),
                _number(rawCenter["y"], "circle2d.center.y"),
                space,
            ),
            _number(payload["radius"], "circle2d.radius", minimum=0.0),
        )


Geometry2D = Point2D | BBox2D | RotatedBox2D | Polygon2D | Line2D | Circle2D
DetectionGeometry = BBox2D | RotatedBox2D | Polygon2D


def parseGeometry2D(value: object) -> Geometry2D:
    payload = _object(value, "geometry2d")
    payloadType = payload.get("type")
    if payloadType == "point2d":
        return Point2D.fromPayload(payload)
    if payloadType == "bbox2d":
        return BBox2D.fromPayload(payload)
    if payloadType == "rotatedBox2d":
        return RotatedBox2D.fromPayload(payload)
    if payloadType == "polygon2d":
        return Polygon2D.fromPayload(payload)
    if payloadType == "line2d":
        return Line2D.fromPayload(payload)
    if payloadType == "circle2d":
        return Circle2D.fromPayload(payload)
    raise PayloadValidationError(f"unsupported geometry2d type: {payloadType!r}")


def parseDetectionGeometry(value: object) -> DetectionGeometry:
    geometry = parseGeometry2D(value)
    if isinstance(geometry, (Point2D, Line2D, Circle2D)):
        raise PayloadValidationError(
            "detection.geometry must be bbox2d, rotatedBox2d, or polygon2d"
        )
    return geometry


def _axisAlignedBBox(geometry: DetectionGeometry) -> BBox2D:
    if isinstance(geometry, BBox2D):
        return geometry
    if isinstance(geometry, Polygon2D):
        xs = [point.x for point in geometry.points]
        ys = [point.y for point in geometry.points]
        minimumX = min(xs)
        minimumY = min(ys)
        return BBox2D(
            minimumX,
            minimumY,
            max(xs) - minimumX,
            max(ys) - minimumY,
            geometry.coordinateSpace,
        )

    radians = math.radians(geometry.angleDegrees)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    halfWidth = geometry.width / 2.0
    halfHeight = geometry.height / 2.0
    corners = [
        (
            geometry.centerX + dx * cosine - dy * sine,
            geometry.centerY + dx * sine + dy * cosine,
        )
        for dx, dy in (
            (-halfWidth, -halfHeight),
            (halfWidth, -halfHeight),
            (halfWidth, halfHeight),
            (-halfWidth, halfHeight),
        )
    ]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    minimumX = min(xs)
    minimumY = min(ys)
    return BBox2D(
        minimumX,
        minimumY,
        max(xs) - minimumX,
        max(ys) - minimumY,
        geometry.coordinateSpace,
    )


def _sameBBox(left: BBox2D, right: BBox2D) -> bool:
    if left.coordinateSpace != right.coordinateSpace:
        return False
    return all(
        math.isclose(leftValue, rightValue, rel_tol=1e-9, abs_tol=1e-6)
        for leftValue, rightValue in (
            (left.x, right.x),
            (left.y, right.y),
            (left.width, right.width),
            (left.height, right.height),
        )
    )


@dataclass(frozen=True)
class Blob2D:
    blobId: str
    area: float
    centroid: Point2D
    bbox: BBox2D
    label: int | None = None
    contour: Polygon2D | None = None
    attributes: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _string(self.blobId, "blob.id")
        object.__setattr__(self, "area", _number(self.area, "blob.area", minimum=0.0))
        if not isinstance(self.centroid, Point2D):
            raise PayloadValidationError("blob.centroid must be Point2D")
        if not isinstance(self.bbox, BBox2D):
            raise PayloadValidationError("blob.bbox must be BBox2D")
        if self.label is not None:
            _integer(self.label, "blob.label", minimum=0)
        if self.contour is not None and not isinstance(self.contour, Polygon2D):
            raise PayloadValidationError("blob.contour must be Polygon2D")
        spaces = [self.centroid.coordinateSpace, self.bbox.coordinateSpace]
        if self.contour is not None:
            spaces.append(self.contour.coordinateSpace)
        if any(space != spaces[0] for space in spaces[1:]):
            raise PayloadValidationError("blob geometries must use one coordinateSpace")
        object.__setattr__(self, "attributes", _jsonObject(self.attributes, "blob.attributes"))

    @property
    def coordinateSpace(self) -> CoordinateSpace2D:
        return self.centroid.coordinateSpace

    def toPayload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.blobId,
            "area": self.area,
            "centroid": self.centroid.toPayload(),
            "bbox": self.bbox.toPayload(),
            "attributes": _jsonObject(self.attributes, "blob.attributes"),
        }
        if self.label is not None:
            payload["label"] = self.label
        if self.contour is not None:
            payload["contour"] = self.contour.toPayload()
        return payload

    @classmethod
    def fromPayload(cls, value: object) -> Blob2D:
        payload = _object(value, "blob")
        _keys(
            payload,
            "blob",
            {"id", "area", "centroid", "bbox"},
            {"label", "contour", "attributes"},
        )
        label = payload.get("label")
        contour = payload.get("contour")
        return cls(
            blobId=_string(payload["id"], "blob.id"),
            area=_number(payload["area"], "blob.area", minimum=0.0),
            centroid=Point2D.fromPayload(payload["centroid"]),
            bbox=BBox2D.fromPayload(payload["bbox"]),
            label=(None if label is None else _integer(label, "blob.label", minimum=0)),
            contour=None if contour is None else Polygon2D.fromPayload(contour),
            attributes=_jsonObject(payload.get("attributes", {}), "blob.attributes"),
        )


@dataclass(frozen=True)
class BlobCollection:
    items: tuple[Blob2D, ...] = ()
    coordinateSpace: CoordinateSpace2D = field(default_factory=CoordinateSpace2D)

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if any(not isinstance(item, Blob2D) for item in items):
            raise PayloadValidationError("blobCollection.items must contain Blob2D values")
        if any(item.coordinateSpace != self.coordinateSpace for item in items):
            raise PayloadValidationError(
                "blobCollection items must use the collection coordinateSpace"
            )
        object.__setattr__(self, "items", items)

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "blobCollection",
            "schemaVersion": schemaVersionForCoordinateSpace(self.coordinateSpace),
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "items": [item.toPayload() for item in self.items],
        }

    @classmethod
    def fromPayload(cls, value: object) -> BlobCollection:
        payload = _typedObject(
            value,
            "blobCollection",
            "blobCollection",
            {"coordinateSpace", "items"},
        )
        coordinateSpace = CoordinateSpace2D.fromPayload(
            payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
        )
        rawItems = payload["items"]
        if not isinstance(rawItems, list):
            raise PayloadValidationError("blobCollection.items must be an array")
        return cls(
            tuple(Blob2D.fromPayload(item) for item in rawItems),
            coordinateSpace,
        )


@dataclass(frozen=True)
class Detection2D:
    detectionId: str
    classId: int
    label: str
    confidence: float
    bbox: BBox2D
    polygon: Polygon2D | None = None
    attributes: Mapping[str, object] = field(default_factory=dict)
    geometry: DetectionGeometry | None = None

    def __post_init__(self) -> None:
        _string(self.detectionId, "detection.id")
        _integer(self.classId, "detection.classId", minimum=0)
        _string(self.label, "detection.label", allowEmpty=True)
        object.__setattr__(
            self,
            "confidence",
            _number(self.confidence, "detection.confidence", minimum=0.0, maximum=1.0),
        )
        if not isinstance(self.bbox, BBox2D):
            raise PayloadValidationError("detection.bbox must be BBox2D")
        if self.polygon is not None and not isinstance(self.polygon, Polygon2D):
            raise PayloadValidationError("detection.polygon must be Polygon2D")
        geometry = self.geometry
        if geometry is None:
            geometry = self.polygon if self.polygon is not None else self.bbox
        if not isinstance(geometry, (BBox2D, RotatedBox2D, Polygon2D)):
            raise PayloadValidationError(
                "detection.geometry must be BBox2D, RotatedBox2D, or Polygon2D"
            )
        if self.geometry is not None and self.polygon is not None:
            if self.geometry != self.polygon:
                raise PayloadValidationError(
                    "detection.geometry conflicts with legacy polygon"
                )
        expectedBBox = _axisAlignedBBox(geometry)
        if not _sameBBox(expectedBBox, self.bbox):
            raise PayloadValidationError(
                "detection.bbox must be the axis-aligned bounds of detection.geometry"
            )
        if (
            geometry.coordinateSpace != self.bbox.coordinateSpace
        ):
            raise PayloadValidationError(
                "detection geometries must use one coordinateSpace"
            )
        object.__setattr__(self, "geometry", geometry)
        if isinstance(geometry, Polygon2D) and self.polygon is None:
            object.__setattr__(self, "polygon", geometry)
        object.__setattr__(
            self,
            "attributes",
            _jsonObject(self.attributes, "detection.attributes"),
        )

    @property
    def coordinateSpace(self) -> CoordinateSpace2D:
        return self.bbox.coordinateSpace

    def toPayload(self) -> dict[str, object]:
        geometry = self.geometry
        if geometry is None:  # pragma: no cover - normalized in __post_init__
            raise PayloadValidationError("detection.geometry is missing")
        payload: dict[str, object] = {
            "id": self.detectionId,
            "classId": self.classId,
            "label": self.label,
            "confidence": self.confidence,
            "bbox": self.bbox.toPayload(),
            "geometry": geometry.toPayload(),
            "attributes": _jsonObject(self.attributes, "detection.attributes"),
        }
        return payload

    @classmethod
    def fromGeometry(
        cls,
        detectionId: str,
        classId: int,
        label: str,
        confidence: float,
        geometry: DetectionGeometry,
        attributes: Mapping[str, object] | None = None,
    ) -> Detection2D:
        return cls(
            detectionId=detectionId,
            classId=classId,
            label=label,
            confidence=confidence,
            bbox=_axisAlignedBBox(geometry),
            polygon=geometry if isinstance(geometry, Polygon2D) else None,
            attributes={} if attributes is None else attributes,
            geometry=geometry,
        )

    @classmethod
    def fromPayload(cls, value: object) -> Detection2D:
        payload = _object(value, "detection")
        _keys(
            payload,
            "detection",
            {"id", "classId", "label", "confidence"},
            {"geometry", "bbox", "polygon", "attributes"},
        )
        rawGeometry = payload.get("geometry")
        rawBBox = payload.get("bbox")
        rawPolygon = payload.get("polygon")
        if rawGeometry is None and rawBBox is None:
            raise PayloadValidationError("detection requires geometry or legacy bbox")
        if rawGeometry is not None and rawPolygon is not None:
            raise PayloadValidationError(
                "detection must not mix geometry with legacy polygon"
            )
        bbox = None if rawBBox is None else BBox2D.fromPayload(rawBBox)
        if rawGeometry is not None:
            geometry = parseDetectionGeometry(rawGeometry)
            polygon = geometry if isinstance(geometry, Polygon2D) else None
        else:
            polygon = (
                None if rawPolygon is None else Polygon2D.fromPayload(rawPolygon)
            )
            if polygon is not None:
                geometry = polygon
            elif bbox is not None:
                geometry = bbox
            else:  # pragma: no cover - guarded above
                raise PayloadValidationError("detection geometry is missing")
        if bbox is None:
            bbox = _axisAlignedBBox(geometry)
        return cls(
            detectionId=_string(payload["id"], "detection.id"),
            classId=_integer(payload["classId"], "detection.classId", minimum=0),
            label=_string(payload["label"], "detection.label", allowEmpty=True),
            confidence=_number(
                payload["confidence"],
                "detection.confidence",
                minimum=0.0,
                maximum=1.0,
            ),
            bbox=bbox,
            polygon=polygon,
            attributes=_jsonObject(
                payload.get("attributes", {}), "detection.attributes"
            ),
            geometry=geometry,
        )


@dataclass(frozen=True)
class DetectionCollection:
    items: tuple[Detection2D, ...] = ()
    coordinateSpace: CoordinateSpace2D = field(default_factory=CoordinateSpace2D)

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if any(not isinstance(item, Detection2D) for item in items):
            raise PayloadValidationError(
                "detectionCollection.items must contain Detection2D values"
            )
        if any(item.coordinateSpace != self.coordinateSpace for item in items):
            raise PayloadValidationError(
                "detectionCollection items must use the collection coordinateSpace"
            )
        object.__setattr__(self, "items", items)

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "detectionCollection",
            "schemaVersion": schemaVersionForCoordinateSpace(self.coordinateSpace),
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "items": [item.toPayload() for item in self.items],
        }

    @classmethod
    def fromPayload(cls, value: object) -> DetectionCollection:
        payload = _typedObject(
            value,
            "detectionCollection",
            "detectionCollection",
            {"coordinateSpace", "items"},
        )
        coordinateSpace = CoordinateSpace2D.fromPayload(
            payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
        )
        rawItems = payload["items"]
        if not isinstance(rawItems, list):
            raise PayloadValidationError("detectionCollection.items must be an array")
        return cls(
            tuple(Detection2D.fromPayload(item) for item in rawItems),
            coordinateSpace,
        )


@dataclass(frozen=True)
class ContourItem:
    contourId: str
    polygon: Polygon2D
    parentId: str | None = None
    firstChildId: str | None = None
    previousSiblingId: str | None = None
    nextSiblingId: str | None = None
    depth: int = 0
    isHole: bool = False

    def __post_init__(self) -> None:
        _string(self.contourId, "contour.id")
        if not isinstance(self.polygon, Polygon2D):
            raise PayloadValidationError("contour.polygon must be Polygon2D")
        for name, value in (
            ("parentId", self.parentId),
            ("firstChildId", self.firstChildId),
            ("previousSiblingId", self.previousSiblingId),
            ("nextSiblingId", self.nextSiblingId),
        ):
            _optionalString(value, f"contour.{name}")
        _integer(self.depth, "contour.depth", minimum=0)
        if not isinstance(self.isHole, bool):
            raise PayloadValidationError("contour.isHole must be boolean")
        if self.isHole != (self.depth % 2 == 1):
            raise PayloadValidationError("contour.isHole must match depth parity")

    @property
    def coordinateSpace(self) -> CoordinateSpace2D:
        return self.polygon.coordinateSpace

    def toPayload(self) -> dict[str, object]:
        return {
            "id": self.contourId,
            "polygon": self.polygon.toPayload(),
            "parentId": self.parentId,
            "firstChildId": self.firstChildId,
            "previousSiblingId": self.previousSiblingId,
            "nextSiblingId": self.nextSiblingId,
            "depth": self.depth,
            "isHole": self.isHole,
        }

    @classmethod
    def fromPayload(cls, value: object) -> ContourItem:
        payload = _object(value, "contour")
        _keys(
            payload,
            "contour",
            {
                "id",
                "polygon",
                "parentId",
                "firstChildId",
                "previousSiblingId",
                "nextSiblingId",
                "depth",
                "isHole",
            },
        )
        isHole = payload["isHole"]
        if not isinstance(isHole, bool):
            raise PayloadValidationError("contour.isHole must be boolean")
        return cls(
            contourId=_string(payload["id"], "contour.id"),
            polygon=Polygon2D.fromPayload(payload["polygon"]),
            parentId=_optionalString(payload["parentId"], "contour.parentId"),
            firstChildId=_optionalString(
                payload["firstChildId"], "contour.firstChildId"
            ),
            previousSiblingId=_optionalString(
                payload["previousSiblingId"], "contour.previousSiblingId"
            ),
            nextSiblingId=_optionalString(
                payload["nextSiblingId"], "contour.nextSiblingId"
            ),
            depth=_integer(payload["depth"], "contour.depth", minimum=0),
            isHole=isHole,
        )


@dataclass(frozen=True)
class ContourCollection:
    items: tuple[ContourItem, ...] = ()
    coordinateSpace: CoordinateSpace2D = field(default_factory=CoordinateSpace2D)

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if any(not isinstance(item, ContourItem) for item in items):
            raise PayloadValidationError(
                "contourCollection.items must contain ContourItem values"
            )
        if any(item.coordinateSpace != self.coordinateSpace for item in items):
            raise PayloadValidationError(
                "contourCollection items must use the collection coordinateSpace"
            )
        ids = [item.contourId for item in items]
        if len(ids) != len(set(ids)):
            raise PayloadValidationError("contourCollection item IDs must be unique")
        object.__setattr__(self, "items", items)

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "contourCollection",
            "schemaVersion": VISION_PAYLOAD_SCHEMA_VERSION_1_2,
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "items": [item.toPayload() for item in self.items],
        }

    @classmethod
    def fromPayload(cls, value: object) -> ContourCollection:
        payload = _typedObject(
            value,
            "contourCollection",
            "contourCollection",
            {"coordinateSpace", "items"},
        )
        if payload["schemaVersion"] != VISION_PAYLOAD_SCHEMA_VERSION_1_2:
            raise PayloadValidationError(
                "contourCollection requires schemaVersion '1.2'"
            )
        space = CoordinateSpace2D.fromPayload(
            payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
        )
        rawItems = payload["items"]
        if not isinstance(rawItems, list):
            raise PayloadValidationError("contourCollection.items must be an array")
        return cls(tuple(ContourItem.fromPayload(item) for item in rawItems), space)


@dataclass(frozen=True)
class ShapeMeasurement:
    measurementId: str
    sourceId: str
    sourceType: str
    area: float
    perimeter: float
    centroid: Point2D
    bbox: BBox2D
    minAreaRect: RotatedBox2D
    circularity: float

    def __post_init__(self) -> None:
        _string(self.measurementId, "shapeMeasurement.id")
        _string(self.sourceId, "shapeMeasurement.sourceId")
        if self.sourceType not in {"contour", "blob"}:
            raise PayloadValidationError(
                "shapeMeasurement.sourceType must be 'contour' or 'blob'"
            )
        object.__setattr__(
            self, "area", _number(self.area, "shapeMeasurement.area", minimum=0.0)
        )
        object.__setattr__(
            self,
            "perimeter",
            _number(self.perimeter, "shapeMeasurement.perimeter", minimum=0.0),
        )
        object.__setattr__(
            self,
            "circularity",
            _number(
                self.circularity,
                "shapeMeasurement.circularity",
                minimum=0.0,
                maximum=1.0,
            ),
        )
        if not isinstance(self.centroid, Point2D):
            raise PayloadValidationError("shapeMeasurement.centroid must be Point2D")
        if not isinstance(self.bbox, BBox2D):
            raise PayloadValidationError("shapeMeasurement.bbox must be BBox2D")
        if not isinstance(self.minAreaRect, RotatedBox2D):
            raise PayloadValidationError(
                "shapeMeasurement.minAreaRect must be RotatedBox2D"
            )
        spaces = (
            self.centroid.coordinateSpace,
            self.bbox.coordinateSpace,
            self.minAreaRect.coordinateSpace,
        )
        if any(space != spaces[0] for space in spaces[1:]):
            raise PayloadValidationError(
                "shapeMeasurement geometries must use one coordinateSpace"
            )

    @property
    def coordinateSpace(self) -> CoordinateSpace2D:
        return self.centroid.coordinateSpace

    def toPayload(self) -> dict[str, object]:
        return {
            "id": self.measurementId,
            "sourceId": self.sourceId,
            "sourceType": self.sourceType,
            "area": self.area,
            "perimeter": self.perimeter,
            "centroid": self.centroid.toPayload(),
            "bbox": self.bbox.toPayload(),
            "minAreaRect": self.minAreaRect.toPayload(),
            "circularity": self.circularity,
        }

    @classmethod
    def fromPayload(cls, value: object) -> ShapeMeasurement:
        payload = _object(value, "shapeMeasurement")
        _keys(
            payload,
            "shapeMeasurement",
            {
                "id",
                "sourceId",
                "sourceType",
                "area",
                "perimeter",
                "centroid",
                "bbox",
                "minAreaRect",
                "circularity",
            },
        )
        return cls(
            measurementId=_string(payload["id"], "shapeMeasurement.id"),
            sourceId=_string(payload["sourceId"], "shapeMeasurement.sourceId"),
            sourceType=_string(
                payload["sourceType"], "shapeMeasurement.sourceType"
            ),
            area=_number(payload["area"], "shapeMeasurement.area", minimum=0.0),
            perimeter=_number(
                payload["perimeter"], "shapeMeasurement.perimeter", minimum=0.0
            ),
            centroid=Point2D.fromPayload(payload["centroid"]),
            bbox=BBox2D.fromPayload(payload["bbox"]),
            minAreaRect=RotatedBox2D.fromPayload(payload["minAreaRect"]),
            circularity=_number(
                payload["circularity"],
                "shapeMeasurement.circularity",
                minimum=0.0,
                maximum=1.0,
            ),
        )


@dataclass(frozen=True)
class ShapeMeasurementCollection:
    items: tuple[ShapeMeasurement, ...] = ()
    coordinateSpace: CoordinateSpace2D = field(default_factory=CoordinateSpace2D)

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if any(not isinstance(item, ShapeMeasurement) for item in items):
            raise PayloadValidationError(
                "shapeMeasurementCollection.items must contain ShapeMeasurement values"
            )
        if any(item.coordinateSpace != self.coordinateSpace for item in items):
            raise PayloadValidationError(
                "shapeMeasurementCollection items must use the collection coordinateSpace"
            )
        ids = [item.measurementId for item in items]
        if len(ids) != len(set(ids)):
            raise PayloadValidationError(
                "shapeMeasurementCollection item IDs must be unique"
            )
        object.__setattr__(self, "items", items)

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "shapeMeasurementCollection",
            "schemaVersion": VISION_PAYLOAD_SCHEMA_VERSION_1_2,
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "items": [item.toPayload() for item in self.items],
        }

    @classmethod
    def fromPayload(cls, value: object) -> ShapeMeasurementCollection:
        payload = _typedObject(
            value,
            "shapeMeasurementCollection",
            "shapeMeasurementCollection",
            {"coordinateSpace", "items"},
        )
        if payload["schemaVersion"] != VISION_PAYLOAD_SCHEMA_VERSION_1_2:
            raise PayloadValidationError(
                "shapeMeasurementCollection requires schemaVersion '1.2'"
            )
        space = CoordinateSpace2D.fromPayload(
            payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
        )
        rawItems = payload["items"]
        if not isinstance(rawItems, list):
            raise PayloadValidationError(
                "shapeMeasurementCollection.items must be an array"
            )
        return cls(
            tuple(ShapeMeasurement.fromPayload(item) for item in rawItems),
            space,
        )


@dataclass(frozen=True)
class HistogramChannel:
    name: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        _string(self.name, "histogram.channel.name")
        values = tuple(
            _number(value, f"histogram.channel.values[{index}]", minimum=0.0)
            for index, value in enumerate(self.values)
        )
        object.__setattr__(self, "values", values)

    def toPayload(self) -> dict[str, object]:
        return {"name": self.name, "values": list(self.values)}

    @classmethod
    def fromPayload(cls, value: object, index: int) -> HistogramChannel:
        payload = _object(value, f"histogram.channels[{index}]")
        _keys(payload, f"histogram.channels[{index}]", {"name", "values"})
        rawValues = payload["values"]
        if not isinstance(rawValues, list):
            raise PayloadValidationError(
                f"histogram.channels[{index}].values must be an array"
            )
        return cls(
            _string(payload["name"], f"histogram.channels[{index}].name"),
            tuple(
                _number(
                    item,
                    f"histogram.channels[{index}].values[{valueIndex}]",
                    minimum=0.0,
                )
                for valueIndex, item in enumerate(rawValues)
            ),
        )


@dataclass(frozen=True)
class Histogram:
    coordinateSpace: CoordinateSpace2D
    colorSpace: str
    normalization: str
    pixelCount: int
    binEdges: tuple[float, ...]
    channels: tuple[HistogramChannel, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.coordinateSpace, CoordinateSpace2D):
            raise PayloadValidationError("histogram.coordinateSpace is invalid")
        if self.colorSpace not in {"GRAY", "BGR"}:
            raise PayloadValidationError("histogram.colorSpace must be GRAY or BGR")
        if self.normalization not in {"counts", "probability"}:
            raise PayloadValidationError(
                "histogram.normalization must be counts or probability"
            )
        _integer(self.pixelCount, "histogram.pixelCount", minimum=0)
        edges = tuple(
            _number(value, f"histogram.binEdges[{index}]")
            for index, value in enumerate(self.binEdges)
        )
        if len(edges) < 2 or any(
            left >= right for left, right in zip(edges, edges[1:])
        ):
            raise PayloadValidationError(
                "histogram.binEdges must contain at least two increasing values"
            )
        channels = tuple(self.channels)
        expectedNames = ("GRAY",) if self.colorSpace == "GRAY" else ("B", "G", "R")
        if tuple(channel.name for channel in channels) != expectedNames:
            raise PayloadValidationError(
                "histogram channels do not match the declared colorSpace"
            )
        binCount = len(edges) - 1
        for channel in channels:
            if len(channel.values) != binCount:
                raise PayloadValidationError(
                    "histogram channel value count must match binEdges"
                )
            total = sum(channel.values)
            if self.normalization == "counts":
                if any(not value.is_integer() for value in channel.values):
                    raise PayloadValidationError(
                        "histogram counts must contain integer values"
                    )
                if not math.isclose(total, float(self.pixelCount), abs_tol=1e-9):
                    raise PayloadValidationError(
                        "histogram counts must sum to pixelCount"
                    )
            else:
                expectedTotal = 0.0 if self.pixelCount == 0 else 1.0
                if not math.isclose(total, expectedTotal, abs_tol=1e-6):
                    raise PayloadValidationError(
                        "histogram probabilities must sum to 1 for non-empty inputs"
                    )
        object.__setattr__(self, "binEdges", edges)
        object.__setattr__(self, "channels", channels)

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "histogram",
            "schemaVersion": VISION_PAYLOAD_SCHEMA_VERSION_1_2,
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "colorSpace": self.colorSpace,
            "normalization": self.normalization,
            "pixelCount": self.pixelCount,
            "binEdges": list(self.binEdges),
            "channels": [channel.toPayload() for channel in self.channels],
        }

    @classmethod
    def fromPayload(cls, value: object) -> Histogram:
        payload = _typedObject(
            value,
            "histogram",
            "histogram",
            {
                "coordinateSpace",
                "colorSpace",
                "normalization",
                "pixelCount",
                "binEdges",
                "channels",
            },
        )
        if payload["schemaVersion"] != VISION_PAYLOAD_SCHEMA_VERSION_1_2:
            raise PayloadValidationError("histogram requires schemaVersion '1.2'")
        rawEdges = payload["binEdges"]
        rawChannels = payload["channels"]
        if not isinstance(rawEdges, list):
            raise PayloadValidationError("histogram.binEdges must be an array")
        if not isinstance(rawChannels, list):
            raise PayloadValidationError("histogram.channels must be an array")
        return cls(
            coordinateSpace=CoordinateSpace2D.fromPayload(
                payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
            ),
            colorSpace=_string(payload["colorSpace"], "histogram.colorSpace"),
            normalization=_string(
                payload["normalization"], "histogram.normalization"
            ),
            pixelCount=_integer(
                payload["pixelCount"], "histogram.pixelCount", minimum=0
            ),
            binEdges=tuple(
                _number(item, f"histogram.binEdges[{index}]")
                for index, item in enumerate(rawEdges)
            ),
            channels=tuple(
                HistogramChannel.fromPayload(item, index)
                for index, item in enumerate(rawChannels)
            ),
        )


@dataclass(frozen=True)
class LineItem:
    lineId: str
    line: Line2D

    def __post_init__(self) -> None:
        _string(self.lineId, "line.id")
        if not isinstance(self.line, Line2D):
            raise PayloadValidationError("line item must contain Line2D")

    @property
    def coordinateSpace(self) -> CoordinateSpace2D:
        return self.line.coordinateSpace

    def toPayload(self) -> dict[str, object]:
        return {"id": self.lineId, "line": self.line.toPayload()}

    @classmethod
    def fromPayload(cls, value: object) -> LineItem:
        payload = _object(value, "lineItem")
        _keys(payload, "lineItem", {"id", "line"})
        return cls(
            _string(payload["id"], "lineItem.id"),
            Line2D.fromPayload(payload["line"]),
        )


@dataclass(frozen=True)
class LineCollection:
    items: tuple[LineItem, ...] = ()
    coordinateSpace: CoordinateSpace2D = field(default_factory=CoordinateSpace2D)

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if any(not isinstance(item, LineItem) for item in items):
            raise PayloadValidationError(
                "lineCollection.items must contain LineItem values"
            )
        if any(item.coordinateSpace != self.coordinateSpace for item in items):
            raise PayloadValidationError(
                "lineCollection items must use the collection coordinateSpace"
            )
        ids = [item.lineId for item in items]
        if len(ids) != len(set(ids)):
            raise PayloadValidationError("lineCollection item IDs must be unique")
        object.__setattr__(self, "items", items)

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "lineCollection",
            "schemaVersion": VISION_PAYLOAD_SCHEMA_VERSION_1_2,
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "items": [item.toPayload() for item in self.items],
        }

    @classmethod
    def fromPayload(cls, value: object) -> LineCollection:
        payload = _typedObject(
            value, "lineCollection", "lineCollection", {"coordinateSpace", "items"}
        )
        if payload["schemaVersion"] != VISION_PAYLOAD_SCHEMA_VERSION_1_2:
            raise PayloadValidationError("lineCollection requires schemaVersion '1.2'")
        rawItems = payload["items"]
        if not isinstance(rawItems, list):
            raise PayloadValidationError("lineCollection.items must be an array")
        return cls(
            tuple(LineItem.fromPayload(item) for item in rawItems),
            CoordinateSpace2D.fromPayload(
                payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
            ),
        )


@dataclass(frozen=True)
class CircleItem:
    circleId: str
    circle: Circle2D

    def __post_init__(self) -> None:
        _string(self.circleId, "circle.id")
        if not isinstance(self.circle, Circle2D):
            raise PayloadValidationError("circle item must contain Circle2D")

    @property
    def coordinateSpace(self) -> CoordinateSpace2D:
        return self.circle.coordinateSpace

    def toPayload(self) -> dict[str, object]:
        return {"id": self.circleId, "circle": self.circle.toPayload()}

    @classmethod
    def fromPayload(cls, value: object) -> CircleItem:
        payload = _object(value, "circleItem")
        _keys(payload, "circleItem", {"id", "circle"})
        return cls(
            _string(payload["id"], "circleItem.id"),
            Circle2D.fromPayload(payload["circle"]),
        )


@dataclass(frozen=True)
class CircleCollection:
    items: tuple[CircleItem, ...] = ()
    coordinateSpace: CoordinateSpace2D = field(default_factory=CoordinateSpace2D)

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if any(not isinstance(item, CircleItem) for item in items):
            raise PayloadValidationError(
                "circleCollection.items must contain CircleItem values"
            )
        if any(item.coordinateSpace != self.coordinateSpace for item in items):
            raise PayloadValidationError(
                "circleCollection items must use the collection coordinateSpace"
            )
        ids = [item.circleId for item in items]
        if len(ids) != len(set(ids)):
            raise PayloadValidationError("circleCollection item IDs must be unique")
        object.__setattr__(self, "items", items)

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "circleCollection",
            "schemaVersion": VISION_PAYLOAD_SCHEMA_VERSION_1_2,
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "items": [item.toPayload() for item in self.items],
        }

    @classmethod
    def fromPayload(cls, value: object) -> CircleCollection:
        payload = _typedObject(
            value,
            "circleCollection",
            "circleCollection",
            {"coordinateSpace", "items"},
        )
        if payload["schemaVersion"] != VISION_PAYLOAD_SCHEMA_VERSION_1_2:
            raise PayloadValidationError(
                "circleCollection requires schemaVersion '1.2'"
            )
        rawItems = payload["items"]
        if not isinstance(rawItems, list):
            raise PayloadValidationError("circleCollection.items must be an array")
        return cls(
            tuple(CircleItem.fromPayload(item) for item in rawItems),
            CoordinateSpace2D.fromPayload(
                payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
            ),
        )


@dataclass(frozen=True)
class TemplateMatch:
    matchId: str
    bbox: BBox2D
    rawScore: float
    quality: float

    def __post_init__(self) -> None:
        _string(self.matchId, "templateMatch.id")
        if not isinstance(self.bbox, BBox2D):
            raise PayloadValidationError("templateMatch.bbox must be BBox2D")
        object.__setattr__(
            self, "rawScore", _number(self.rawScore, "templateMatch.rawScore")
        )
        object.__setattr__(
            self,
            "quality",
            _number(
                self.quality,
                "templateMatch.quality",
                minimum=0.0,
                maximum=1.0,
            ),
        )

    @property
    def coordinateSpace(self) -> CoordinateSpace2D:
        return self.bbox.coordinateSpace

    def toPayload(self) -> dict[str, object]:
        return {
            "id": self.matchId,
            "bbox": self.bbox.toPayload(),
            "rawScore": self.rawScore,
            "quality": self.quality,
        }

    @classmethod
    def fromPayload(cls, value: object) -> TemplateMatch:
        payload = _object(value, "templateMatch")
        _keys(payload, "templateMatch", {"id", "bbox", "rawScore", "quality"})
        return cls(
            _string(payload["id"], "templateMatch.id"),
            BBox2D.fromPayload(payload["bbox"]),
            _number(payload["rawScore"], "templateMatch.rawScore"),
            _number(
                payload["quality"],
                "templateMatch.quality",
                minimum=0.0,
                maximum=1.0,
            ),
        )


@dataclass(frozen=True)
class TemplateMatchCollection:
    method: str
    label: str
    templateWidth: int
    templateHeight: int
    items: tuple[TemplateMatch, ...] = ()
    coordinateSpace: CoordinateSpace2D = field(default_factory=CoordinateSpace2D)

    def __post_init__(self) -> None:
        if self.method not in {
            "sqdiff",
            "sqdiffNormed",
            "ccorr",
            "ccorrNormed",
            "ccoeff",
            "ccoeffNormed",
        }:
            raise PayloadValidationError("templateMatchCollection.method is invalid")
        _string(self.label, "templateMatchCollection.label", allowEmpty=True)
        _integer(
            self.templateWidth, "templateMatchCollection.templateWidth", minimum=1
        )
        _integer(
            self.templateHeight, "templateMatchCollection.templateHeight", minimum=1
        )
        items = tuple(self.items)
        if any(not isinstance(item, TemplateMatch) for item in items):
            raise PayloadValidationError(
                "templateMatchCollection.items must contain TemplateMatch values"
            )
        if any(item.coordinateSpace != self.coordinateSpace for item in items):
            raise PayloadValidationError(
                "templateMatchCollection items must use the collection coordinateSpace"
            )
        ids = [item.matchId for item in items]
        if len(ids) != len(set(ids)):
            raise PayloadValidationError(
                "templateMatchCollection item IDs must be unique"
            )
        object.__setattr__(self, "items", items)

    def toPayload(self) -> dict[str, object]:
        return {
            "type": "templateMatchCollection",
            "schemaVersion": VISION_PAYLOAD_SCHEMA_VERSION_1_2,
            "coordinateSpace": self.coordinateSpace.toPayload(),
            "method": self.method,
            "label": self.label,
            "templateWidth": self.templateWidth,
            "templateHeight": self.templateHeight,
            "items": [item.toPayload() for item in self.items],
        }

    @classmethod
    def fromPayload(cls, value: object) -> TemplateMatchCollection:
        payload = _typedObject(
            value,
            "templateMatchCollection",
            "templateMatchCollection",
            {
                "coordinateSpace",
                "method",
                "label",
                "templateWidth",
                "templateHeight",
                "items",
            },
        )
        if payload["schemaVersion"] != VISION_PAYLOAD_SCHEMA_VERSION_1_2:
            raise PayloadValidationError(
                "templateMatchCollection requires schemaVersion '1.2'"
            )
        rawItems = payload["items"]
        if not isinstance(rawItems, list):
            raise PayloadValidationError(
                "templateMatchCollection.items must be an array"
            )
        return cls(
            method=_string(payload["method"], "templateMatchCollection.method"),
            label=_string(
                payload["label"], "templateMatchCollection.label", allowEmpty=True
            ),
            templateWidth=_integer(
                payload["templateWidth"],
                "templateMatchCollection.templateWidth",
                minimum=1,
            ),
            templateHeight=_integer(
                payload["templateHeight"],
                "templateMatchCollection.templateHeight",
                minimum=1,
            ),
            items=tuple(TemplateMatch.fromPayload(item) for item in rawItems),
            coordinateSpace=CoordinateSpace2D.fromPayload(
                payload["coordinateSpace"], schemaVersion=payload["schemaVersion"]
            ),
        )


@dataclass(frozen=True)
class ChannelStatistics:
    minimum: float
    maximum: float
    mean: float
    standardDeviation: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "minimum", _number(self.minimum, "channel.minimum"))
        object.__setattr__(self, "maximum", _number(self.maximum, "channel.maximum"))
        object.__setattr__(self, "mean", _number(self.mean, "channel.mean"))
        object.__setattr__(
            self,
            "standardDeviation",
            _number(
                self.standardDeviation,
                "channel.standardDeviation",
                minimum=0.0,
            ),
        )
        if self.minimum > self.maximum:
            raise PayloadValidationError("channel.minimum must be <= channel.maximum")
        if not self.minimum <= self.mean <= self.maximum:
            raise PayloadValidationError("channel.mean must be within minimum..maximum")

    def toPayload(self) -> dict[str, object]:
        return {
            "minimum": self.minimum,
            "maximum": self.maximum,
            "mean": self.mean,
            "standardDeviation": self.standardDeviation,
        }

    @classmethod
    def fromPayload(cls, value: object, path: str = "channel") -> ChannelStatistics:
        payload = _object(value, path)
        _keys(
            payload,
            path,
            {"minimum", "maximum", "mean", "standardDeviation"},
        )
        return cls(
            minimum=_number(payload["minimum"], f"{path}.minimum"),
            maximum=_number(payload["maximum"], f"{path}.maximum"),
            mean=_number(payload["mean"], f"{path}.mean"),
            standardDeviation=_number(
                payload["standardDeviation"],
                f"{path}.standardDeviation",
                minimum=0.0,
            ),
        )


@dataclass(frozen=True)
class ColorStatistics:
    channels: Mapping[str, ChannelStatistics]
    pixelCount: int
    colorSpace: str = "RGB"
    region: Geometry2D | None = None
    attributes: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _string(self.colorSpace, "colorStatistics.colorSpace")
        _integer(self.pixelCount, "colorStatistics.pixelCount", minimum=0)
        if not isinstance(self.channels, Mapping) or not self.channels:
            raise PayloadValidationError("colorStatistics.channels must be a non-empty object")
        channels: dict[str, ChannelStatistics] = {}
        for name, statistics in self.channels.items():
            channelName = _string(name, "colorStatistics channel name")
            if not isinstance(statistics, ChannelStatistics):
                raise PayloadValidationError(
                    f"colorStatistics.channels.{channelName} is invalid"
                )
            channels[channelName] = statistics
        if self.region is not None and not isinstance(
            self.region, (Point2D, BBox2D, RotatedBox2D, Polygon2D, Line2D, Circle2D)
        ):
            raise PayloadValidationError("colorStatistics.region must be Geometry2D")
        object.__setattr__(self, "channels", channels)
        object.__setattr__(
            self,
            "attributes",
            _jsonObject(self.attributes, "colorStatistics.attributes"),
        )

    def toPayload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": "colorStatistics",
            "schemaVersion": (
                schemaVersionForCoordinateSpace(self.region.coordinateSpace)
                if self.region is not None
                else VISION_PAYLOAD_SCHEMA_VERSION
            ),
            "colorSpace": self.colorSpace,
            "pixelCount": self.pixelCount,
            "channels": {
                name: statistics.toPayload()
                for name, statistics in self.channels.items()
            },
            "attributes": _jsonObject(
                self.attributes, "colorStatistics.attributes"
            ),
        }
        if self.region is not None:
            payload["region"] = self.region.toPayload()
        return payload

    @classmethod
    def fromPayload(cls, value: object) -> ColorStatistics:
        payload = _typedObject(
            value,
            "colorStatistics",
            "colorStatistics",
            {"colorSpace", "pixelCount", "channels"},
            {"region", "attributes"},
        )
        rawChannels = _object(payload["channels"], "colorStatistics.channels")
        region = payload.get("region")
        return cls(
            channels={
                name: ChannelStatistics.fromPayload(
                    statistics, f"colorStatistics.channels.{name}"
                )
                for name, statistics in rawChannels.items()
            },
            pixelCount=_integer(
                payload["pixelCount"], "colorStatistics.pixelCount", minimum=0
            ),
            colorSpace=_string(payload["colorSpace"], "colorStatistics.colorSpace"),
            region=None if region is None else parseGeometry2D(region),
            attributes=_jsonObject(
                payload.get("attributes", {}), "colorStatistics.attributes"
            ),
        )


VisionPayload = (
    Geometry2D
    | Vector2D
    | BlobCollection
    | DetectionCollection
    | ColorStatistics
    | ContourCollection
    | ShapeMeasurementCollection
    | Histogram
    | LineCollection
    | CircleCollection
    | TemplateMatchCollection
)


def parseVisionPayload(value: object) -> VisionPayload:
    payload = _object(value, "visionPayload")
    payloadType = payload.get("type")
    if payloadType == "vector2d":
        return Vector2D.fromPayload(payload)
    if isinstance(payloadType, str) and payloadType in CONCRETE_GEOMETRY_PORT_TYPES:
        return parseGeometry2D(payload)
    if payloadType == "blobCollection":
        return BlobCollection.fromPayload(payload)
    if payloadType == "detectionCollection":
        return DetectionCollection.fromPayload(payload)
    if payloadType == "colorStatistics":
        return ColorStatistics.fromPayload(payload)
    if payloadType == "contourCollection":
        return ContourCollection.fromPayload(payload)
    if payloadType == "shapeMeasurementCollection":
        return ShapeMeasurementCollection.fromPayload(payload)
    if payloadType == "histogram":
        return Histogram.fromPayload(payload)
    if payloadType == "lineCollection":
        return LineCollection.fromPayload(payload)
    if payloadType == "circleCollection":
        return CircleCollection.fromPayload(payload)
    if payloadType == "templateMatchCollection":
        return TemplateMatchCollection.fromPayload(payload)
    raise PayloadValidationError(f"unsupported vision payload type: {payloadType!r}")


def matchesVisionPortType(
    value: object,
    expectedType: str,
    expectedSchemaVersion: object | None = None,
) -> bool:
    """Return whether a JSON-boundary value satisfies a semantic vision port."""
    if not isinstance(value, dict):
        return False
    try:
        if not isVisionPayloadSchemaVersionCompatible(
            value.get("schemaVersion"), expectedSchemaVersion
        ):
            return False
        if expectedType == "geometry2d":
            parseGeometry2D(value)
            return True
        payload = parseVisionPayload(value)
        return value.get("type") == expectedType and payload is not None
    except (PayloadValidationError, TypeError, ValueError):
        return False
