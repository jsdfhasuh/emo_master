"""Small, stateless geometry/numeric bridges; no device or recipe semantics."""
from __future__ import annotations

from dataclasses import dataclass
import math

from emo_master.core.contracts.geometry2d import Point2D


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict
    outputPorts: dict
    paramSchema: dict


def _ports(**types):
    return {key: dict(type=value, required=True, nullable=False) for key, value in types.items()}


def _meta(identifier, title, inputs, outputs):
    return OperatorMeta(identifier, title, "1.0.0", _ports(**inputs), _ports(**outputs),
                        dict(type="object", properties={}, additionalProperties=False))


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是数值，不能是布尔值、文本或空值。")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} 必须是有限数值。")
    return value


class _NumericOperator:
    def validateParams(self, params):
        if params:
            return dict(code="E_PARAM_INVALID", message="此算子不接受参数，请连接输入端口。")
        return None

    def executeNode(self, inputs, params, runtimeContext):
        error = self.validateParams(params)
        if error:
            return dict(status="error", error=error)
        missing = [name for name in self.meta.inputPorts if name not in inputs]
        if missing:
            return dict(status="error", error=dict(code="E_INPUT_MISSING", message="缺少输入：" + ", ".join(missing)))
        try:
            outputs = self._calculate(inputs)
        except (ValueError, TypeError, OverflowError) as exc:
            return dict(status="error", error=dict(code="E_INPUT_SHAPE", message=str(exc)))
        return dict(status="ok", outputs=outputs)


class PointComponentsOperator(_NumericOperator):
    meta = _meta("vision.geometry.point_components", "单点拆分 X/Y",
                 dict(points="list<point2d>"), dict(x="number", y="number"))

    def _calculate(self, inputs):
        points = inputs["points"]
        if not isinstance(points, list) or len(points) != 1:
            raise ValueError("需要且只能有一个参考点；不自动选择多点中的第一个。")
        point = Point2D.fromPayload(points[0])
        # Components stay in the input point's coordinate space; no reframe,
        # scaling, unit conversion or rounding is implied by this bridge.
        return dict(x=point.x, y=point.y)


class NumberAbsoluteOperator(_NumericOperator):
    meta = _meta("vision.math.absolute", "绝对值", dict(value="number"), dict(value="number"))

    def _calculate(self, inputs):
        return dict(value=abs(_number(inputs["value"], "value")))


class NumberExactIntegerOperator(_NumericOperator):
    meta = _meta("vision.math.exact_integer", "严格转整数", dict(value="number"), dict(value="integer"))

    def _calculate(self, inputs):
        value = _number(inputs["value"], "value")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("value 含小数，不能严格转换为整数；不会截断、四舍五入或按容差吸附。")
        return dict(value=int(value))


class NumberPairOperator(_NumericOperator):
    meta = _meta("vision.collection.number_pair", "数值对组装",
                 dict(first="number", second="number"), dict(values="list<number>"))

    def _calculate(self, inputs):
        return dict(values=[_number(inputs[name], name) for name in ("first", "second")])
