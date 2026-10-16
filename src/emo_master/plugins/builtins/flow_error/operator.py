"""Explicit, branch-gated technical failure; not a business NG policy."""
import re

from emo_master.plugins.builtins._geometry_bridges import OperatorMeta, error


class FlowErrorOperator:
    meta = OperatorMeta("vision.flow.error", "停止并报告技术错误", "1.0.0",
        {"after": {"type": "any", "required": True, "nullable": False}}, {},
        {"type": "object", "additionalProperties": False, "properties": {
            "code": {"title": "错误码", "type": "string", "default": "E_WORKFLOW_GUARD", "pattern": "^E_[A-Z0-9_]+$"},
            "message": {"title": "错误说明", "type": "string", "default": "技术条件不满足，停止当前流程", "minLength": 1}}})

    def validateParams(self, params):
        if set(params) - {"code", "message"} or not isinstance(params.get("code", "E_WORKFLOW_GUARD"), str) or not re.fullmatch(r"E_[A-Z0-9_]+", params.get("code", "E_WORKFLOW_GUARD")):
            return {"code": "E_PARAM_INVALID", "message": "invalid technical error code"}
        if not isinstance(params.get("message", "error"), str) or not params.get("message", "error").strip():
            return {"code": "E_PARAM_INVALID", "message": "message must be nonempty"}
        return None

    def executeNode(self, inputs, params, runtimeContext):
        if self.validateParams(params):
            return {"status": "error", "error": self.validateParams(params)}
        if "after" not in inputs or inputs["after"] is None:
            return error("E_INPUT_MISSING", "after is required")
        return error(params.get("code", "E_WORKFLOW_GUARD"), params.get("message", "技术条件不满足，停止当前流程"))
